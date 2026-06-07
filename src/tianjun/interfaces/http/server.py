from __future__ import annotations

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...application.control_plane import CentralControlPlane
from ...chat import ChatRuntime
from ...scenarios import node_from_dict, task_from_dict
from ..dashboard.page import render_dashboard_html
from .legacy_routes import handle_legacy_get, handle_legacy_post

STATIC_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "static"


def build_http_server(
    control_plane: CentralControlPlane,
    host: str,
    port: int,
    *,
    chat_runtime: ChatRuntime | None = None,
) -> ThreadingHTTPServer:
    chat = chat_runtime or ChatRuntime(control_plane)
    class ControlPlaneHandler(BaseHTTPRequestHandler):
        server_version = "TianjunControlPlane/0.2"

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in {"/", "/dashboard"}:
                    self._write_html(200, render_dashboard_html())
                    return
                if self._write_dashboard_static(path):
                    return
                if path == "/report":
                    self._write_json(200, control_plane.build_report())
                    return
                if path == "/health":
                    self._write_json(
                        200,
                        {
                            "status": "ok",
                            "model_runtime": control_plane.scheduler.model_runtime.describe(),
                            "chat_runtime": chat.describe(),
                        },
                    )
                    return
                if handle_legacy_get(self, path, control_plane, chat):
                    return
                if path.startswith("/policies/"):
                    policy_id = path.removeprefix("/policies/").strip("/")
                    if policy_id:
                        self._write_json(200, control_plane.get_policy(policy_id))
                        return
                if path.startswith("/conversations/"):
                    session_id = path.removeprefix("/conversations/").strip("/")
                    if session_id:
                        self._write_json(200, control_plane.get_requirement_session(session_id))
                        return
                if path.startswith("/chat/sessions/"):
                    session_id = path.removeprefix("/chat/sessions/").strip("/")
                    if session_id:
                        self._write_json(200, chat.get_session(session_id))
                        return
                self._write_json(404, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                self._write_json(400, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/topology/register":
                    self._write_json(200, control_plane.register_topology(payload))
                    return
                if path == "/nodes/register":
                    self._write_json(200, control_plane.register_node(node_from_dict(payload)))
                    return
                if path == "/nodes/heartbeat":
                    result = control_plane.record_heartbeat(
                        payload["node_id"],
                        health_score=payload.get("health_score"),
                        online=payload.get("online"),
                        reliability_score=payload.get("reliability_score"),
                        cost_per_tick=payload.get("cost_per_tick"),
                        region=payload.get("region"),
                        location=payload.get("location"),
                        service_region=payload.get("service_region"),
                        labels=None if "labels" not in payload else set(payload.get("labels", [])),
                        performance_factors=payload.get("performance_factors"),
                        network_paths=payload.get("network_paths"),
                    )
                    self._write_json(200, result)
                    return
                if path == "/schedule/preview":
                    self._write_json(200, self._schedule_cloudsim_task(payload, commit=False))
                    return
                if path == "/schedule/commit":
                    self._write_json(200, self._schedule_cloudsim_task(payload, commit=True))
                    return
                if path == "/tasks":
                    self._write_json(200, control_plane.submit_task(task_from_dict(payload)))
                    return
                if path.startswith("/tasks/") and path.endswith("/schedule"):
                    task_id = path.removeprefix("/tasks/").removesuffix("/schedule").strip("/")
                    if not bool(payload.get("confirmed_by_user_button") or payload.get("confirmed")):
                        self._write_json(403, {"error": "pending task scheduling requires explicit confirmation"})
                        return
                    self._write_json(200, control_plane.schedule_pending_task(task_id))
                    return
                if path == "/requirements/parse":
                    result = control_plane.parse_requirement(
                        str(payload.get("message", "")),
                        overrides=payload.get("overrides"),
                    )
                    self._write_json(200, result)
                    return
                if handle_legacy_post(self, path, payload, control_plane, chat):
                    return
                if path == "/conversations/start":
                    result = control_plane.start_requirement_session(
                        str(payload.get("message", "")),
                        overrides=payload.get("overrides"),
                    )
                    self._write_json(200, result)
                    return
                if path == "/chat/sessions/stream":
                    session_id = payload.get("session_id")
                    message = str(payload.get("message", ""))
                    if session_id:
                        self._write_chat_event_stream(lambda emit: chat.continue_session(str(session_id), message, stream_emit=emit))
                    else:
                        self._write_chat_event_stream(lambda emit: chat.start(message, stream_emit=emit))
                    return
                if path.startswith("/chat/sessions/") and path.endswith("/messages/stream"):
                    session_id = path.removeprefix("/chat/sessions/").removesuffix("/messages/stream").strip("/")
                    message = str(payload.get("message", ""))
                    self._write_chat_event_stream(lambda emit: chat.continue_session(session_id, message, stream_emit=emit))
                    return
                if path == "/chat/sessions":
                    session_id = payload.get("session_id")
                    if session_id:
                        result = chat.continue_session(str(session_id), str(payload.get("message", "")))
                    else:
                        result = chat.start(str(payload.get("message", "")))
                    self._write_json(200, result)
                    return
                if path.startswith("/chat/sessions/") and path.endswith("/messages"):
                    session_id = path.removeprefix("/chat/sessions/").removesuffix("/messages").strip("/")
                    self._write_json(200, chat.continue_session(session_id, str(payload.get("message", ""))))
                    return
                if path.startswith("/chat/sessions/") and path.endswith("/commit"):
                    session_id = path.removeprefix("/chat/sessions/").removesuffix("/commit").strip("/")
                    result = chat.commit_session(session_id, policy_id=payload.get("policy_id"))
                    result["dashboard_payload"] = self._dashboard_payload_from_chat_result(result)
                    self._write_json(200, result)
                    return
                if path.startswith("/conversations/") and path.endswith("/continue"):
                    session_id = path.removeprefix("/conversations/").removesuffix("/continue").strip("/")
                    result = control_plane.continue_requirement_session(
                        session_id,
                        str(payload.get("message", "")),
                        overrides=payload.get("overrides"),
                    )
                    self._write_json(200, result)
                    return
                if path.startswith("/conversations/") and path.endswith("/draft"):
                    session_id = path.removeprefix("/conversations/").removesuffix("/draft").strip("/")
                    self._write_json(
                        200,
                        control_plane.draft_policy_from_session(
                            session_id,
                            execution_payload=payload.get("execution"),
                        ),
                    )
                    return
                if path == "/policies/draft":
                    requirement = payload.get("requirement")
                    if requirement is None:
                        requirement = control_plane.parse_requirement(
                            str(payload.get("message", "")),
                            overrides=payload.get("overrides"),
                        )
                    result = control_plane.draft_policy(
                        requirement,
                        execution_payload=payload.get("execution"),
                    )
                    self._write_json(200, result)
                    return
                if path == "/policies/compare":
                    requirement = payload.get("requirement")
                    if requirement is None:
                        requirement = control_plane.parse_requirement(
                            str(payload.get("message", "")),
                            overrides=payload.get("overrides"),
                        )
                    result = control_plane.compare_policy_options(
                        requirement,
                        execution_payload=payload.get("execution"),
                        option_profiles=payload.get("option_profiles"),
                    )
                    self._write_json(200, result)
                    return
                if path == "/policies/simulate":
                    self._write_json(200, control_plane.simulate_policy(str(payload["policy_id"])))
                    return
                if path == "/policies/commit":
                    if not bool(payload.get("confirmed_by_user_button") or payload.get("confirmed")):
                        self._write_json(403, {"error": "policy commit requires explicit user button confirmation"})
                        return
                    self._write_json(200, control_plane.commit_policy(str(payload["policy_id"])))
                    return
                if path == "/policy-weights":
                    if not bool(payload.get("confirmed_by_user_button") or payload.get("confirmed")):
                        self._write_json(403, {"error": "policy weight update requires explicit confirmation"})
                        return
                    self._write_json(
                        200,
                        control_plane.update_policy_weights(
                            dict(payload.get("weights") or {}),
                            reason=str(payload.get("reason") or "用户手动提交多维策略权重。"),
                        ),
                    )
                    return
                if path == "/feedback/parse":
                    self._write_json(200, control_plane.parse_feedback(payload))
                    return
                if path == "/feedback":
                    self._write_json(200, control_plane.record_user_feedback(payload))
                    return
                if path.startswith("/policies/") and path.endswith("/optimize"):
                    policy_id = path.removeprefix("/policies/").removesuffix("/optimize").strip("/")
                    feedback = dict(payload)
                    feedback["policy_id"] = policy_id
                    self._write_json(200, control_plane.optimize_policy_from_feedback(feedback))
                    return
                if path.startswith("/policies/") and path.endswith("/resimulate"):
                    policy_id = path.removeprefix("/policies/").removesuffix("/resimulate").strip("/")
                    self._write_json(200, control_plane.simulate_policy(policy_id))
                    return
                if path == "/leases/next":
                    self._write_json(200, control_plane.request_lease(payload["node_id"]))
                    return
                if path == "/task-runs/progress":
                    self._write_json(
                        200,
                        control_plane.report_task_progress(
                            node_id=payload["node_id"],
                            task_id=payload["task_id"],
                            stage=str(payload.get("stage", "running")),
                            status=str(payload.get("status", "running")),
                            progress=payload.get("progress"),
                            message=payload.get("message"),
                            metrics=payload.get("metrics"),
                        ),
                    )
                    return
                if path == "/task-runs/cancel":
                    self._write_json(
                        200,
                        control_plane.cancel_task_run(
                            task_id=str(payload["task_id"]),
                            requeue=bool(payload.get("requeue", False)),
                        ),
                    )
                    return
                if path == "/task-runs/result":
                    result = control_plane.report_task_result(
                        node_id=payload["node_id"],
                        task_id=payload["task_id"],
                        success=bool(payload["success"]),
                        duration_seconds=float(payload["duration_seconds"]),
                        stdout=payload.get("stdout", ""),
                        stderr=payload.get("stderr", ""),
                        failure_reason=payload.get("failure_reason"),
                        returncode=payload.get("returncode"),
                        cost=payload.get("cost"),
                        metadata=payload.get("metadata"),
                    )
                    self._write_json(200, result)
                    return
                self._write_json(404, {"error": "not_found"})
            except Exception as exc:  # noqa: BLE001
                self._write_json(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            return json.loads(raw)

        def _write_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_dashboard_static(self, path: str) -> bool:
            if not (path.startswith("/css/") or path.startswith("/js/")):
                return False
            target = (STATIC_DASHBOARD_DIR / path.lstrip("/")).resolve()
            if not target.is_file() or STATIC_DASHBOARD_DIR not in target.parents:
                self._write_json(404, {"error": "not_found"})
                return True
            body = target.read_bytes()
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix == ".js":
                content_type = "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def _schedule_cloudsim_task(self, payload: dict[str, Any], *, commit: bool) -> dict[str, Any]:
            task = task_from_dict(payload)
            preview = control_plane.preview_task(task)
            if preview is None:
                return {
                    "status": "rejected",
                    "task_id": task.task_id,
                    "node_id": "",
                    "total_score": 0.0,
                    "preview_decision": None,
                    "lease": None,
                    "reason": "no feasible online node",
                }
            if not commit:
                return {
                    "status": "preview",
                    "task_id": task.task_id,
                    "node_id": preview.get("node_id", ""),
                    "total_score": preview.get("total_score", 0.0),
                    "preview_decision": preview,
                    "lease": None,
                }

            if task.task_id not in control_plane.tasks:
                control_plane.submit_task(task)
            result = control_plane.schedule_pending_task(task.task_id)
            if result.get("status") == "committed":
                result = dict(result)
                result["status"] = "scheduled"
            return result

        def _dashboard_payload_from_chat_result(self, result: dict[str, Any]) -> dict[str, Any] | None:
            artifacts = result.get("artifacts") or {}
            commit = artifacts.get("commit") if isinstance(artifacts, dict) else None
            policy = commit.get("policy") if isinstance(commit, dict) else artifacts.get("policy")
            if not isinstance(policy, dict):
                return None
            policy_id = str(policy.get("policy_id", ""))
            task = control_plane.policy_tasks.get(policy_id)
            submitted_task = commit.get("submitted_task") if isinstance(commit, dict) else None
            return {
                "status": commit.get("status", "committed") if isinstance(commit, dict) else "preview",
                "mode": "hermes_dialogue_policy",
                "task": submitted_task or (task.to_dict() if task is not None else None),
                "preview_decision": policy.get("decision"),
                "submitted_task": submitted_task,
                "policy": policy,
            }

        def _write_chat_event_stream(self, runner) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def emit(event: dict[str, Any]) -> None:
                event_type = str(event.get("type") or "message")
                body = json.dumps(event, ensure_ascii=False)
                payload = f"event: {event_type}\ndata: {body}\n\n".encode("utf-8")
                self.wfile.write(payload)
                self.wfile.flush()

            try:
                result = runner(emit)
                emit({"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                emit({"type": "error", "message": str(exc)})
                emit({"type": "done", "result": None})

        def _write_html(self, status: int, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), ControlPlaneHandler)
