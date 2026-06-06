from __future__ import annotations

from typing import Any

TOOL_NAMES = [
    "get_cluster_state",
    "analyze_user_intent",
    "start_requirement_dialogue",
    "continue_requirement_dialogue",
    "draft_compute_network_policy",
    "compare_policy_options",
    "simulate_policy",
    "explain_policy",
    "parse_user_feedback",
    "optimize_policy_from_feedback",
    "commit_policy",
    "schedule_pending_task",
]

CHAT_TOOL_NAMES = [
    "start_chat_session",
    "continue_chat_session",
    "get_chat_session",
]

MCP_TOOL_NAMES = [
    "get_cluster_state",
    *CHAT_TOOL_NAMES,
    "analyze_user_intent",
    "start_requirement_dialogue",
    "continue_requirement_dialogue",
    "draft_compute_network_policy",
    "compare_policy_options",
    "simulate_policy",
    "explain_policy",
    "parse_user_feedback",
    "optimize_policy_from_feedback",
    "commit_policy",
    "schedule_pending_task",
]


def tianjun_tool_contract() -> dict[str, Any]:
    """Single contract shared by Dashboard, MCP/Hermes and compatibility shims."""
    return {
        "contract": "tianjun.tools.v2",
        "return_format": "structured_json",
        "tools": list(TOOL_NAMES),
        "policy": {
            "llm_may_explain": True,
            "llm_may_commit_without_tool": False,
            "llm_must_not_invent_inventory": True,
            "require_user_confirmation_before_commit": True,
            "commit_requires_explicit_button_or_confirmed_flag": True,
            "requirement_source": "llm_first_when_configured_with_deterministic_fallback",
            "resource_configuration_source": "tianjun_control_plane_and_inventory",
            "state_transition_source": "tianjun_control_plane_only",
        },
        "capability_boundary": {
            "cluster_state": "only from /report or control-plane state",
            "future_inventory_calendar": "only if configured inventory supplies release-calendar data",
            "cost_and_sla": "only from policy/simulation artifacts",
            "execution": "only via node-agent or sim-backend lease/result flow",
        },
    }
