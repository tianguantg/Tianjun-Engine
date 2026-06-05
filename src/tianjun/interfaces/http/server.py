from __future__ import annotations

import json
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...application.control_plane import CentralControlPlane
from ...chat import ChatRuntime
from ...scenarios import node_from_dict, task_from_dict


DEFAULT_CORS_ALLOW_ORIGIN = "http://127.0.0.1:5173"


def cors_allow_origin() -> str:
    return os.environ.get("TIANJUN_CORS_ALLOW_ORIGIN", DEFAULT_CORS_ALLOW_ORIGIN)

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

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/":
                    self._write_json(200, {"name": "Tianjun Engine API", "status": "ok"})
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
                            "cors_allow_origin": cors_allow_origin(),
                        },
                    )
                    return
                if path == "/hermes/status":
                    self._write_json(
                        200,
                        {
                            "status": "ok",
                            "mode": "optimized_chat_runtime",
                            "chat_runtime": chat.describe(),
                            "model_runtime": control_plane.scheduler.model_runtime.describe(),
                        },
                    )
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
                if path == "/intent":
                    result = self._legacy_intent(payload)
                    self._write_json(200, result)
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
                if path == "/hermes/chat/stream":
                    message = str(payload.get("message", "")).strip()
                    if not message:
                        self._write_json(400, {"error": "message is required"})
                        return
                    self._write_legacy_hermes_stream(message, session_id=payload.get("session_id"))
                    return
                if path.startswith("/chat/sessions/") and path.endswith("/messages/stream"):
                    session_id = path.removeprefix("/chat/sessions/").removesuffix("/messages/stream").strip("/")
                    message = str(payload.get("message", ""))
                    self._write_chat_event_stream(lambda emit: chat.continue_session(session_id, message, stream_emit=emit))
                    return
                if path in {"/chat", "/chat/sessions"}:
                    session_id = payload.get("session_id")
                    if session_id:
                        result = chat.continue_session(str(session_id), str(payload.get("message", "")))
                    else:
                        result = chat.start(str(payload.get("message", "")))
                    self._write_json(200, result)
                    return
                if path == "/hermes/chat":
                    message = str(payload.get("message", "")).strip()
                    if not message:
                        self._write_json(400, {"error": "message is required"})
                        return
                    result = chat.start(message)
                    self._write_json(
                        200,
                        {
                            "status": "ok",
                            "reply": result.get("message", ""),
                            "raw": result,
                        },
                    )
                    return
                if path.startswith("/chat/sessions/") and path.endswith("/messages"):
                    session_id = path.removeprefix("/chat/sessions/").removesuffix("/messages").strip("/")
                    self._write_json(200, chat.continue_session(session_id, str(payload.get("message", ""))))
                    return
                if path.startswith("/chat/sessions/") and path.endswith("/commit"):
                    session_id = path.removeprefix("/chat/sessions/").removesuffix("/commit").strip("/")
                    if not bool(payload.get("confirmed_by_user_button") or payload.get("confirmed")):
                        self._write_json(
                            403,
                            {"error": "chat policy commit requires explicit user button confirmation"},
                        )
                        return
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
            self._send_cors_headers()
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

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", cors_allow_origin())
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

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
            return control_plane.schedule_pending_task(task.task_id)

        def _legacy_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
            message = str(payload.get("message", "")).strip()
            if not message:
                raise ValueError("message is required")
            dry_run = bool(payload.get("dry_run", False))
            requirement = control_plane.parse_requirement(message, overrides=payload.get("overrides"))
            policy = control_plane.draft_policy(requirement, execution_payload=payload.get("execution"))
            policy_id = str(policy["policy_id"])
            task = control_plane.policy_tasks[policy_id]
            preview = control_plane.preview_task(task)
            submitted = None
            status = "preview"
            lease = None
            if not dry_run:
                committed = control_plane.commit_policy(policy_id)
                submitted = committed.get("submitted_task")
                status = committed.get("status", "committed")
            return {
                "status": status,
                "mode": "optimized_intent_gateway",
                "interpretation": {
                    "requirement": requirement,
                    "policy_id": policy_id,
                    "questions": requirement.get("questions", []),
                    "dialogue_status": requirement.get("dialogue_status"),
                },
                "task": task.to_dict(),
                "preview_decision": preview,
                "submitted_task": submitted,
                "lease": lease,
                "policy": policy,
                "hermes_tool_contract": {
                    "endpoint": "/intent",
                    "method": "POST",
                    "payload": {"message": "自然语言调度需求", "dry_run": False},
                    "purpose": "兼容原仪表盘：将自然语言需求转为优化版策略草案、调度预览或显式提交。",
                },
            }

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
            self._send_cors_headers()
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

        def _write_legacy_hermes_stream(self, message: str, *, session_id: Any = None) -> None:
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def send(payload: dict[str, Any]) -> None:
                body = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(body)
                self.wfile.flush()

            assistant_was_streamed = False

            def emit(event: dict[str, Any]) -> None:
                nonlocal assistant_was_streamed
                event_type = str(event.get("type") or "")
                if event_type == "assistant_delta":
                    assistant_was_streamed = True
                    send({"type": "delta", "text": str(event.get("delta", ""))})
                elif event_type == "llm_start":
                    send({"type": "delta", "text": "\n[DeepSeek 辅助] 正在解析用户意图...\n"})
                elif event_type == "llm_done":
                    send({"type": "delta", "text": "[DeepSeek 辅助完成] 意图字段已交由 Hermes 校验。\n"})
                elif event_type == "llm_fallback":
                    reason = str(event.get("reason") or "接口请求未完成")
                    send({"type": "delta", "text": f"[DeepSeek 辅助降级] {reason}，Hermes 已使用本地规则处理。\n"})
                elif event_type == "tool_start":
                    send({"type": "delta", "text": f"\n[工具] {event.get('tool', '')} ...\n"})
                elif event_type in {"tool_done", "tool_result"}:
                    send({"type": "delta", "text": f"\n[工具完成] {event.get('summary', '')}\n"})
                elif event_type == "session":
                    session = event.get("session") or {}
                    send({"type": "session", "session_id": session.get("session_id")})

            try:
                if session_id:
                    result = chat.continue_session(str(session_id), message, stream_emit=emit)
                else:
                    result = chat.start(message, stream_emit=emit)
                session = result.get("session") or {}
                send(
                    {
                        "type": "result",
                        "session_id": session.get("session_id"),
                        "action": result.get("action"),
                        "commit_policy_id": result.get("commit_policy_id"),
                        "dashboard_payload": self._dashboard_payload_from_chat_result(result),
                    }
                )
                if result and result.get("message") and not assistant_was_streamed:
                    send({"type": "delta", "text": str(result.get("message"))})
                send({"type": "done"})
                self.close_connection = True
            except Exception as exc:  # noqa: BLE001
                send({"type": "error", "error": str(exc)})
                send({"type": "done"})
                self.close_connection = True

    return ThreadingHTTPServer((host, port), ControlPlaneHandler)
