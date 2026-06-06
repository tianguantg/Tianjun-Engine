from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..application.control_plane import CentralControlPlane
from .schema import tianjun_tool_contract


@dataclass(slots=True)
class TianjunToolService:
    """Unified in-process Tianjun tool surface.

    This is the only place that maps agent-facing tools to control-plane actions.
    Dashboard ChatRuntime, Hermes compatibility shims, tests and future in-process
    agents should call this service instead of duplicating wrappers.
    """

    control_plane: CentralControlPlane

    def contract(self) -> dict[str, Any]:
        return tianjun_tool_contract()

    def run(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        if tool_name == "get_cluster_state":
            return self.get_cluster_state()
        if tool_name == "analyze_user_intent":
            return self.analyze_user_intent(str(args.get("message", "")), overrides=args.get("overrides"))
        if tool_name == "start_requirement_dialogue":
            return self.start_requirement_dialogue(str(args.get("message", "")), overrides=args.get("overrides"))
        if tool_name == "continue_requirement_dialogue":
            return self.continue_requirement_dialogue(
                str(args["session_id"]),
                str(args.get("message", "")),
                overrides=args.get("overrides"),
            )
        if tool_name == "draft_compute_network_policy":
            return self.draft_compute_network_policy(
                requirement=args.get("requirement"),
                message=args.get("message"),
                session_id=args.get("session_id"),
                overrides=args.get("overrides"),
                execution=args.get("execution"),
            )
        if tool_name == "compare_policy_options":
            return self.compare_policy_options(
                requirement=args.get("requirement"),
                message=args.get("message"),
                session_id=args.get("session_id"),
                overrides=args.get("overrides"),
                execution=args.get("execution"),
                option_profiles=args.get("option_profiles"),
            )
        if tool_name == "simulate_policy":
            return self.simulate_policy(str(args["policy_id"]))
        if tool_name == "explain_policy":
            return self.explain_policy(str(args["policy_id"]))
        if tool_name == "parse_user_feedback":
            return self.parse_user_feedback(args)
        if tool_name == "optimize_policy_from_feedback":
            return self.optimize_policy_from_feedback(args)
        if tool_name == "commit_policy":
            return self.commit_policy(
                str(args["policy_id"]),
                confirmed_by_user_button=bool(args.get("confirmed_by_user_button") or args.get("confirmed")),
            )
        if tool_name == "schedule_pending_task":
            return self.schedule_pending_task(
                str(args["task_id"]),
                confirmed_by_user_button=bool(args.get("confirmed_by_user_button") or args.get("confirmed")),
            )
        raise ValueError(f"Unsupported Tianjun tool: {tool_name}")

    def get_cluster_state(self) -> dict[str, Any]:
        return self.control_plane.build_report()

    def analyze_user_intent(self, message: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.control_plane.parse_requirement(message, overrides=overrides)

    def start_requirement_dialogue(self, message: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.control_plane.start_requirement_session(message, overrides=overrides)

    def continue_requirement_dialogue(
        self,
        session_id: str,
        message: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.control_plane.continue_requirement_session(session_id, message, overrides=overrides)

    def draft_compute_network_policy(
        self,
        requirement: dict[str, Any] | None = None,
        *,
        message: str | None = None,
        session_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if session_id is not None:
            policy = self.control_plane.draft_policy_from_session(str(session_id), execution_payload=execution)
        else:
            if requirement is None:
                if message is None:
                    raise ValueError("Either requirement, message or session_id is required.")
                requirement = self.control_plane.parse_requirement(str(message), overrides=overrides)
            policy = self.control_plane.draft_policy(requirement, execution_payload=execution)
        return {"policy": policy, "summary": self._policy_summary(policy)}

    def simulate_policy(self, policy_id: str) -> dict[str, Any]:
        return self.control_plane.simulate_policy(policy_id)

    def compare_policy_options(
        self,
        requirement: dict[str, Any] | None = None,
        *,
        message: str | None = None,
        session_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        option_profiles: list[str] | None = None,
    ) -> dict[str, Any]:
        if session_id is not None:
            comparison = self.control_plane.compare_policy_options_from_session(
                str(session_id),
                execution_payload=execution,
                option_profiles=option_profiles,
            )
        else:
            if requirement is None:
                if message is None:
                    raise ValueError("Either requirement, message or session_id is required.")
                requirement = self.control_plane.parse_requirement(str(message), overrides=overrides)
            comparison = self.control_plane.compare_policy_options(
                requirement,
                execution_payload=execution,
                option_profiles=option_profiles,
            )
        return comparison

    def explain_policy(self, policy_id: str) -> dict[str, Any]:
        return self.control_plane.get_policy(policy_id)

    def parse_user_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.control_plane.parse_feedback(payload)

    def optimize_policy_from_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.control_plane.optimize_policy_from_feedback(payload)

    def commit_policy(self, policy_id: str, *, confirmed_by_user_button: bool = False) -> dict[str, Any]:
        if not confirmed_by_user_button:
            raise PermissionError("commit_policy requires explicit user button confirmation")
        return self.control_plane.commit_policy(policy_id)

    def schedule_pending_task(self, task_id: str, *, confirmed_by_user_button: bool = False) -> dict[str, Any]:
        if not confirmed_by_user_button:
            raise PermissionError("schedule_pending_task requires explicit user confirmation")
        return self.control_plane.schedule_pending_task(task_id)

    @staticmethod
    def _policy_summary(policy: dict[str, Any]) -> dict[str, Any]:
        effect = policy["expected_effect"]
        return {
            "policy_id": policy["policy_id"],
            "status": policy["status"],
            "selected_compute": policy["selected_compute"].get("node_id"),
            "expected_latency_ms": effect["latency"].get("expected_ms"),
            "expected_cost": effect["cost"].get("expected_cost"),
            "sla_probability": effect["service_quality"].get("sla_probability"),
            "security_score": effect["security"].get("security_score"),
            "risks": policy.get("explanation", {}).get("risks", []),
            "questions": policy.get("explanation", {}).get("questions", []),
        }
