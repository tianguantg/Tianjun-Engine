from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..application.control_plane import CentralControlPlane
from ..llm import LLMSettings, OpenAICompatibleClient
from ..tools import TianjunToolService
from ..policy.generator import REGION_ALIASES

StreamEmit = Callable[[dict[str, Any]], None]

_CONFIRM_WORDS = ("确认", "提交", "同意", "批准", "可以执行", "开始执行", "commit", "approve", "submit", "yes")
_CANCEL_WORDS = ("取消", "先不", "不要提交", "别提交", "stop", "cancel")
_FEEDBACK_WORDS = (
    "太高",
    "太慢",
    "太贵",
    "不满意",
    "优化",
    "调整",
    "换",
    "降低",
    "提高",
    "成本",
    "预算",
    "延迟",
    "时延",
    "安全",
    "sla",
    "qos",
    "反馈",
)
_REGION_LABELS = {
    "east": "东部区域",
    "west": "西部区域",
    "south": "华南区域",
    "dc1": "DC1",
    "dc2": "DC2",
    "dc3": "DC3",
    "shanghai": "上海",
    "beijing": "北京",
    "hangzhou": "杭州",
    "shenzhen": "深圳",
    "guangzhou": "广州",
    "dongguan": "东莞",
    "chengdu": "成都",
    "chongqing": "重庆",
    "wuhan": "武汉",
    "huizhou": "惠州",
    "zhuhai": "珠海",
    "foshan": "佛山",
    "zhongshan": "中山",
}
_WORKLOAD_LABELS = {
    "inference": "推理",
    "training": "训练",
    "streaming": "流式处理",
    "analytics": "分析",
    "batch": "批处理",
}
_FACTOR_LABELS = {
    "network": "网络质量",
    "completion": "任务完成能力",
    "performance": "算力性能",
    "security": "安全匹配度",
    "cost": "成本表现",
    "load": "负载余量",
    "availability": "可用性",
}


@dataclass(slots=True)
class ChatTurn:
    role: str
    content: str
    created_at: float = field(default_factory=time.time)
    tool_name: str | None = None
    tool_payload: dict[str, Any] | None = None

    def to_dict(self, *, include_tool_payload: bool = True) -> dict[str, Any]:
        payload = {
            "role": self.role,
            "content": self.content,
            "created_at": round(self.created_at, 4),
        }
        if self.tool_name:
            payload["tool_name"] = self.tool_name
        if include_tool_payload and self.tool_payload is not None:
            payload["tool_payload"] = self.tool_payload
        return payload


@dataclass(slots=True)
class ChatSession:
    session_id: str
    status: str = "active"
    requirement_session_id: str | None = None
    policy_id: str | None = None
    pending_confirmation: bool = False
    pending_option_selection: bool = False
    policy_options: dict[str, str] = field(default_factory=dict)
    turns: list[ChatTurn] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self, *, include_tool_payload: bool = True) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "requirement_session_id": self.requirement_session_id,
            "policy_id": self.policy_id,
            "pending_confirmation": self.pending_confirmation,
            "pending_option_selection": self.pending_option_selection,
            "policy_options": dict(self.policy_options),
            "turns": [turn.to_dict(include_tool_payload=include_tool_payload) for turn in self.turns],
            "tool_trace": list(self.tool_trace),
            "created_at": round(self.created_at, 4),
            "updated_at": round(self.updated_at, 4),
        }


class ChatRuntime:
    """Conversation orchestrator for Tianjun's controlled LLM/chat boundary.

    The runtime executes a fixed safe tool flow: clarify requirement, draft policy,
    simulate, wait for user confirmation, commit, and optimize from feedback. An
    OpenAI-compatible LLM is the default response-generation layer, while Tianjun
    tools remain the only source of cluster facts and state transitions.
    """

    def __init__(
        self,
        control_plane: CentralControlPlane,
        *,
        llm_client: OpenAICompatibleClient | None = None,
    ) -> None:
        self.control_plane = control_plane
        self.tools = TianjunToolService(control_plane)
        self.llm_client = llm_client
        self.sessions: dict[str, ChatSession] = {}
        self.llm_activity: dict[str, Any] = {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "last_operation": None,
            "last_status": "not_used",
            "last_used_at": None,
            "last_error": None,
        }

    @classmethod
    def with_llm_settings(
        cls,
        control_plane: CentralControlPlane,
        settings: LLMSettings | None,
    ) -> "ChatRuntime":
        if settings is not None:
            settings.validate_for_chat()
        client = None if settings is None or not settings.enabled() else OpenAICompatibleClient(settings)
        return cls(control_plane, llm_client=client)

    def describe(self) -> dict[str, Any]:
        return {
            "sessions": len(self.sessions),
            "llm": {
                "enabled": bool(self.llm_client and self.llm_client.is_enabled()),
                "role": "intent_understanding_assistant",
                "boundary": "DeepSeek may extract intent slots; Hermes templates, policy facts and state transitions remain controlled by Tianjun",
                "settings": None if not self.llm_client else self.llm_client.settings.describe(),
                "activity": dict(self.llm_activity),
            },
        }

    def start(self, message: str, *, stream_emit: StreamEmit | None = None) -> dict[str, Any]:
        session = ChatSession(session_id=self._new_chat_id())
        self.sessions[session.session_id] = session
        if stream_emit:
            stream_emit({"type": "session", "session": session.to_dict(include_tool_payload=False)})
        result = self._handle(session, message, trace_start=0, stream_emit=stream_emit)
        result["tool_trace_delta"] = list(session.tool_trace)
        return result

    def continue_session(self, session_id: str, message: str, *, stream_emit: StreamEmit | None = None) -> dict[str, Any]:
        session = self._session_or_raise(session_id)
        trace_start = len(session.tool_trace)
        if stream_emit:
            stream_emit({"type": "session", "session": session.to_dict(include_tool_payload=False)})
        result = self._handle(session, message, trace_start=trace_start, stream_emit=stream_emit)
        result["tool_trace_delta"] = session.tool_trace[trace_start:]
        return result

    def commit_session(self, session_id: str, policy_id: str | None = None) -> dict[str, Any]:
        """Commit the current policy from an explicit user UI action.

        Chat text such as "确认提交" is intentionally not allowed to trigger
        this state transition. A committed task must originate from a dedicated
        UI/API action so that an LLM response cannot accidentally dispatch work.
        """
        session = self._session_or_raise(session_id)
        target_policy_id = policy_id or session.policy_id
        if not target_policy_id:
            raise ValueError("No policy is ready for commit in this chat session.")
        if session.policy_id and target_policy_id != session.policy_id:
            raise ValueError("Can only commit the latest policy for this chat session.")
        trace_start = len(session.tool_trace)
        committed = self._call_tool(
            session,
            "commit_policy",
            {"policy_id": target_policy_id, "confirmed_by_user_button": True},
        )
        session.policy_id = target_policy_id
        session.pending_confirmation = False
        session.pending_option_selection = False
        session.policy_options = {}
        session.status = "committed"
        response = _commit_response(committed)
        result = self._finish(session, response, action="commit_policy", artifacts={"commit": committed})
        result["tool_trace_delta"] = session.tool_trace[trace_start:]
        return result

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._session_or_raise(session_id).to_dict()

    def _handle(self, session: ChatSession, message: str, *, trace_start: int = 0, stream_emit: StreamEmit | None = None) -> dict[str, Any]:
        text = " ".join(str(message or "").strip().split())
        if not text:
            raise ValueError("chat message must not be empty")
        session.turns.append(ChatTurn(role="user", content=text))

        if session.requirement_session_id is None and session.policy_id is None and _is_greeting_only(text):
            response = "你好，我是天钧智能体。请直接描述业务目标、资源约束、部署地域、预算或安全要求，我会先澄清需求，再生成可审计的算网策略。"
            return self._finish(session, response, action="greeting", artifacts={}, stream_emit=stream_emit)

        if _looks_like_cluster_inventory_question(text):
            report = self._call_tool(session, "get_cluster_state", {}, stream_emit=stream_emit)
            response, inventory = _cluster_inventory_response(text, report)
            return self._finish(
                session,
                response,
                action="query_cluster_state",
                artifacts={"cluster_inventory": inventory},
                stream_emit=stream_emit,
            )

        if _looks_like_general_chat_question(text) and not session.pending_confirmation and not session.pending_option_selection:
            response = self._general_chat_response(session=session, message=text, stream_emit=stream_emit)
            return self._finish(session, response, action="general_chat", artifacts={}, stream_emit=stream_emit)

        if session.requirement_session_id is None and session.policy_id is None and not _looks_like_scheduling_request(text):
            response = self._general_chat_response(session=session, message=text, stream_emit=stream_emit)
            return self._finish(session, response, action="general_chat", artifacts={}, stream_emit=stream_emit)

        if session.pending_confirmation and _is_cancel(text):
            session.pending_confirmation = False
            session.status = "active"
            response = "已取消本次提交确认，策略仍保留为草案。你可以继续提出优化要求。"
            return self._finish(session, response, action="cancel_commit", stream_emit=stream_emit)

        if session.pending_confirmation and _is_confirm(text):
            response = (
                "已收到你的提交意图。为避免 LLM 或自然语言误触发任务下发，"
                "正式提交必须点击右侧/下方的「正式下发」按钮完成。"
            )
            return self._finish(
                session,
                response,
                action="await_user_button_commit",
                artifacts={"policy_id": session.policy_id, "requires_user_button": True},
                stream_emit=stream_emit,
            )

        if session.pending_option_selection:
            selected_policy_id = _selected_policy_option(text, session.policy_options)
            if selected_policy_id is not None:
                policy = self._call_tool(session, "explain_policy", {"policy_id": selected_policy_id}, stream_emit=stream_emit)
                simulation = self._call_tool(session, "simulate_policy", {"policy_id": selected_policy_id}, stream_emit=stream_emit)
                session.policy_id = selected_policy_id
                session.pending_option_selection = False
                session.pending_confirmation = bool(simulation.get("feasible"))
                response = _selected_option_response(policy, simulation)
                return self._finish(
                    session,
                    response,
                    action="select_policy_option",
                    artifacts={"policy": policy, "simulation": simulation},
                    stream_emit=stream_emit,
                )
            response = _option_selection_help_response(session.policy_options)
            return self._finish(
                session,
                response,
                action="await_policy_option_selection",
                artifacts={"policy_options": dict(session.policy_options)},
                stream_emit=stream_emit,
            )

        if session.policy_id and _looks_like_feedback(text):
            optimized = self._call_tool(
                session,
                "optimize_policy_from_feedback",
                {"policy_id": session.policy_id, "instruction": text},
                stream_emit=stream_emit,
            )
            policy = optimized["policy"]
            session.policy_id = policy["policy_id"]
            simulation = self._call_tool(session, "simulate_policy", {"policy_id": session.policy_id}, stream_emit=stream_emit)
            session.pending_confirmation = bool(simulation.get("feasible"))
            response = _policy_response(policy, simulation, prefix="已根据你的反馈生成优化策略。")
            return self._finish(
                session,
                response,
                action="optimize_policy",
                artifacts={"policy": policy, "simulation": simulation, "optimization": optimized},
                stream_emit=stream_emit,
            )

        if session.policy_id and _is_confirm(text):
            response = (
                "我不能通过聊天文本直接下发任务。请检查当前策略摘要和仿真风险后，"
                "点击「正式下发」按钮完成任务下发。"
            )
            session.pending_confirmation = True
            return self._finish(
                session,
                response,
                action="await_user_button_commit",
                artifacts={"policy_id": session.policy_id, "requires_user_button": True},
                stream_emit=stream_emit,
            )

        if session.requirement_session_id is None:
            overrides = self._llm_dialogue_state_overrides(
                session=session,
                message=text,
                current_requirement_session=None,
                stream_emit=stream_emit,
            )
            payload: dict[str, Any] = {"message": text}
            if overrides:
                payload["overrides"] = overrides
            requirement_session = self._call_tool(session, "start_requirement_dialogue", payload, stream_emit=stream_emit)
            session.requirement_session_id = requirement_session["session_id"]
        else:
            current_requirement_session = self.control_plane.get_requirement_session(session.requirement_session_id)
            overrides = self._llm_dialogue_state_overrides(
                session=session,
                message=text,
                current_requirement_session=current_requirement_session,
                stream_emit=stream_emit,
            )
            payload = {"session_id": session.requirement_session_id, "message": text}
            if overrides:
                payload["overrides"] = overrides
            requirement_session = self._call_tool(session, "continue_requirement_dialogue", payload, stream_emit=stream_emit)

        availability_response = _region_availability_response(requirement_session)
        if availability_response:
            return self._finish(
                session,
                availability_response,
                action="clarify_region_availability",
                artifacts={"requirement_session": requirement_session},
                stream_emit=stream_emit,
            )

        if requirement_session["status"] == "needs_clarification":
            response = _questions_response(requirement_session)
            return self._finish(
                session,
                response,
                action="clarify_requirement",
                artifacts={"requirement_session": requirement_session},
                stream_emit=stream_emit,
            )

        drafted = self._call_tool(
            session,
            "compare_policy_options",
            {"session_id": session.requirement_session_id},
            stream_emit=stream_emit,
        )
        session.policy_options = {
            str(option.get("label", "")).upper(): str(option.get("policy_id"))
            for option in drafted.get("options", [])
            if option.get("label") and option.get("policy_id")
        }
        for option in drafted.get("options", []):
            profile = str(option.get("profile") or "")
            if option.get("policy_id"):
                session.policy_options[profile] = str(option["policy_id"])
        recommended = drafted.get("recommended_option") or {}
        if recommended.get("policy_id"):
            session.policy_options["recommended"] = str(recommended["policy_id"])
        session.policy_id = None
        session.pending_confirmation = False
        session.pending_option_selection = bool(session.policy_options)
        response = _policy_options_response(drafted)
        return self._finish(
            session,
            response,
            action="compare_policy_options",
            artifacts={"requirement_session": requirement_session, "policy_options": drafted},
            stream_emit=stream_emit,
        )

    def _finish(
        self,
        session: ChatSession,
        response: str,
        *,
        action: str,
        artifacts: dict[str, Any] | None = None,
        stream_emit: StreamEmit | None = None,
    ) -> dict[str, Any]:
        artifacts = artifacts or {}
        if stream_emit:
            stream_emit({"type": "artifacts", "artifacts": artifacts})
        final_response = response
        if stream_emit:
            for delta in _response_stream_chunks(final_response):
                stream_emit({"type": "assistant_delta", "delta": delta})
        session.turns.append(ChatTurn(role="assistant", content=final_response))
        session.updated_at = time.time()
        return {
            "session": session.to_dict(include_tool_payload=False),
            "message": final_response,
            "action": action,
            "artifacts": artifacts,
            "chat_runtime": self.describe(),
            "requires_user_button": bool(session.pending_confirmation and session.policy_id),
            "commit_policy_id": session.policy_id if session.pending_confirmation else None,
        }

    def _call_tool(self, session: ChatSession, name: str, arguments: dict[str, Any], *, stream_emit: StreamEmit | None = None) -> dict[str, Any]:
        if stream_emit:
            stream_emit({"type": "tool_start", "tool": name, "label": _tool_label(name)})
        result = self.tools.run(name, arguments)
        trace = {
            "tool": name,
            "arguments": arguments,
            "result_summary": _short_result(result),
        }
        session.tool_trace.append(trace)
        session.turns.append(ChatTurn(role="tool", content=trace["result_summary"], tool_name=name, tool_payload=result))
        if stream_emit:
            stream_emit({"type": "tool_done", "tool": name, "label": _tool_label(name), "summary": trace["result_summary"]})
        return result

    def _llm_dialogue_state_overrides(
        self,
        *,
        session: ChatSession,
        message: str,
        current_requirement_session: dict[str, Any] | None,
        stream_emit: StreamEmit | None = None,
    ) -> dict[str, Any] | None:
        """Use the LLM as a dialogue-state tracker and return schema-safe overrides.

        This is not a hard-coded "yes means Chengdu" rule. The LLM sees the
        current structured state, recent turns and outstanding questions, then
        proposes a compact slot update. Tianjun still validates the result
        against the known schema before letting the deterministic policy engine
        consume it. If the LLM is unavailable, normal parser/fallback behavior is
        used.
        """
        if not self.llm_client or not self.llm_client.is_enabled():
            return None
        self._begin_llm_operation("requirement_understanding", stream_emit=stream_emit)
        known_regions = self._known_regions()
        available_regions = self._available_regions()
        prompt = {
            "task": "Update Tianjun compute-network requirement slots from the latest user message and dialogue context.",
            "rules": [
                "Return JSON only; no markdown.",
                "Use previous turns to resolve short confirmations such as 是的/确认/直接调度.",
                "Do not invent resources. Only update fields directly implied by the dialogue.",
                "If the user confirms a pending slot question, copy the value already stated in the dialogue.",
                "Use canonical region codes from known_regions.",
                "Capture a requested region even when it is absent from available_regions; Hermes performs the inventory check and must not silently substitute it.",
                "If the user says to clear previous constraints or only keep a new simple demand, set clear_prior_constraints=true.",
            ],
            "schema": {
                "clear_prior_constraints": "boolean",
                "updates": {
                    "workload_type": "inference|training|streaming|analytics|batch|null",
                    "region_preference": ["canonical region codes"],
                    "cpu_cores": "number|null",
                    "memory_gb": "number|null",
                    "gpu_count": "integer|null",
                    "latency_target_ms": "number|null",
                    "bandwidth_mbps": "number|null",
                    "budget_limit": "number|null",
                    "security_level": "low|medium|high|null",
                    "priority": "latency|cost|quality|balanced|security|null",
                    "priority_vector": {
                        "latency": "0..1",
                        "cost": "0..1",
                        "quality": "0..1",
                        "security": "0..1",
                        "balance": "0..1",
                        "fragmentation": "0..1",
                        "locality": "0..1",
                        "network": "0..1",
                    },
                },
                "confirmed_slots": ["slot names confirmed by the latest user message"],
                "ready_intent": "boolean; true only when the user asks to proceed/directly schedule and required slots are inferable",
                "confidence": "0..1",
            },
            "known_regions": known_regions,
            "available_regions": available_regions,
            "current_requirement_session": _compact_requirement_session(current_requirement_session),
            "recent_turns": [turn.to_dict(include_tool_payload=False) for turn in session.turns[-8:]],
            "latest_user_message": message,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是任务型对话的 Dialogue State Tracking 模块。"
                    "你只输出严格 JSON，用于更新算网需求槽位；不要生成用户可见回复。"
                    "你必须根据上下文解析省略、确认、纠正和覆盖语义。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
        try:
            raw = self.llm_client.chat(messages, timeout_seconds=self.llm_client.settings.timeout_seconds)
            payload = _loads_json_object(raw)
        except Exception as exc:  # noqa: BLE001 - deterministic parser remains the fallback
            self._finish_llm_operation("requirement_understanding", succeeded=False, stream_emit=stream_emit, detail=str(exc))
            return None
        result = self._validated_state_overrides(payload, known_regions=known_regions)
        self._finish_llm_operation("requirement_understanding", succeeded=True, stream_emit=stream_emit)
        return result

    def _known_regions(self) -> list[str]:
        regions = {str(region) for region in REGION_ALIASES.values()}
        try:
            report = self.control_plane.build_report()
            for node in report.get("nodes", []) or []:
                region = node.get("service_region") or node.get("location") or node.get("region")
                if region:
                    regions.add(str(region))
        except Exception:  # noqa: BLE001
            pass
        return sorted(regions)

    def _available_regions(self) -> list[str]:
        try:
            report = self.control_plane.build_report()
            return sorted(
                {
                    str(node.get("service_region") or node.get("location") or node.get("region"))
                    for node in report.get("nodes", []) or []
                    if (node.get("service_region") or node.get("location") or node.get("region")) and node.get("online", True)
                }
            )
        except Exception:  # noqa: BLE001
            return []

    def _validated_state_overrides(self, payload: dict[str, Any], *, known_regions: list[str]) -> dict[str, Any] | None:
        updates = payload.get("updates") if isinstance(payload.get("updates"), dict) else payload
        if not isinstance(updates, dict):
            return None
        allowed_workloads = {"inference", "training", "streaming", "analytics", "batch"}
        allowed_security = {"low", "medium", "high"}
        allowed_priority = {"latency", "cost", "quality", "balanced", "security"}
        safe: dict[str, Any] = {}
        workload = updates.get("workload_type")
        if isinstance(workload, str) and workload in allowed_workloads:
            safe["workload_type"] = workload
        security = updates.get("security_level")
        if isinstance(security, str) and security in allowed_security:
            safe["security_level"] = security
        priority = updates.get("priority")
        if isinstance(priority, str) and priority in allowed_priority:
            safe["priority"] = priority
        vector = updates.get("priority_vector")
        if isinstance(vector, dict):
            safe_vector = {
                str(key): value
                for key, raw in vector.items()
                if str(key) in {"latency", "cost", "quality", "security", "balance", "fragmentation", "locality", "network"}
                and (value := _safe_float(raw)) is not None
                and 0.0 <= value <= 1.0
            }
            if safe_vector:
                safe["priority_vector"] = safe_vector
        regions = updates.get("region_preference")
        if isinstance(regions, str):
            regions = [regions]
        if isinstance(regions, list):
            normalized = []
            region_set = set(known_regions)
            alias_map = {str(k).lower(): str(v) for k, v in REGION_ALIASES.items()}
            for item in regions:
                region = str(item).strip()
                canonical = alias_map.get(region.lower(), region.lower())
                if canonical in region_set and canonical not in normalized:
                    normalized.append(canonical)
            if normalized:
                safe["region_preference"] = normalized
        for key in ("cpu_cores", "memory_gb", "latency_target_ms", "bandwidth_mbps", "budget_limit"):
            value = _safe_float(updates.get(key))
            if value is not None and value >= 0:
                safe[key] = value
        gpu = _safe_float(updates.get("gpu_count"))
        if gpu is not None and gpu >= 0:
            safe["gpu_count"] = int(gpu)
        if bool(payload.get("clear_prior_constraints")):
            safe["__clear_prior_constraints"] = True
        return safe or None

    def _general_chat_response(
        self,
        *,
        session: ChatSession,
        message: str,
        stream_emit: StreamEmit | None,
    ) -> str:
        """Answer non-scheduling chat with the configured LLM instead of forcing intent parsing."""
        if not self.llm_client or not self.llm_client.is_enabled():
            return (
                "我当前以本地规则模式运行，主要负责算力调度、节点状态、策略生成和执行反馈。"
                "如果你想聊一般问题，请先配置并启用 DeepSeek；如果要调度任务，可以直接描述地域、资源、预算或时延目标。"
            )
        self._begin_llm_operation("general_chat", stream_emit=stream_emit)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是天钧 Hermes 智能体的对话层。"
                    "你可以简洁回答一般知识或闲聊问题，但要保持项目助手身份。"
                    "如果用户问题与算力调度无关，先直接回答，再用一句话提示可继续回到调度任务。"
                    "不要编造当前集群库存、节点状态、策略结果或执行状态；这些只能来自工具。"
                    "回复使用中文，结构清晰，避免过长。"
                ),
            },
            *[
                {"role": turn.role, "content": turn.content}
                for turn in session.turns[-7:-1]
                if turn.role in {"user", "assistant"}
            ],
            {"role": "user", "content": message},
        ]
        try:
            response = self.llm_client.chat(messages, timeout_seconds=self.llm_client.settings.timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            self._finish_llm_operation("general_chat", succeeded=False, stream_emit=stream_emit, detail=str(exc))
            return (
                "DeepSeek 通用对话这次没有完成，我先保持调度助手模式。"
                "你可以继续描述算力任务需求，或稍后检查 LLM 连接后再试。"
            )
        self._finish_llm_operation("general_chat", succeeded=True, stream_emit=stream_emit)
        return response

    def _begin_llm_operation(self, operation: str, *, stream_emit: StreamEmit | None) -> None:
        self.llm_activity["requests"] += 1
        self.llm_activity["last_operation"] = operation
        self.llm_activity["last_status"] = "running"
        self.llm_activity["last_used_at"] = round(time.time(), 4)
        self.llm_activity["last_error"] = None
        if stream_emit:
            stream_emit({"type": "llm_start", "operation": operation})

    def _finish_llm_operation(
        self,
        operation: str,
        *,
        succeeded: bool,
        stream_emit: StreamEmit | None,
        detail: str | None = None,
    ) -> None:
        key = "successes" if succeeded else "failures"
        self.llm_activity[key] += 1
        self.llm_activity["last_operation"] = operation
        self.llm_activity["last_status"] = "success" if succeeded else "fallback"
        self.llm_activity["last_used_at"] = round(time.time(), 4)
        reason = None if succeeded else _llm_failure_reason(detail)
        self.llm_activity["last_error"] = reason
        if stream_emit:
            event = {"type": "llm_done" if succeeded else "llm_fallback", "operation": operation}
            if detail and not succeeded:
                event["detail"] = detail
            if reason:
                event["reason"] = reason
            stream_emit(event)

    def _session_or_raise(self, session_id: str) -> ChatSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"Unknown chat session: {session_id}") from exc

    @staticmethod
    def _new_chat_id() -> str:
        return f"chat_{uuid.uuid4().hex[:12]}"



def _loads_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM state tracker returned a non-object JSON value")
    return data


def _llm_failure_reason(detail: str | None) -> str:
    message = str(detail or "").lower()
    if "timed out" in message or "timeout" in message:
        return "接口响应超时"
    if "401" in message or "unauthorized" in message or "authentication" in message:
        return "接口鉴权失败"
    if "429" in message or "rate limit" in message:
        return "接口请求受限"
    if "http 5" in message or "server error" in message:
        return "模型服务暂时不可用"
    if "json" in message or "response format" in message:
        return "意图字段格式校验失败"
    return "接口请求未完成"


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact_requirement_session(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not session:
        return None
    requirement = session.get("requirement") or {}
    return {
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "questions": session.get("questions") or [],
        "requirement": requirement,
        "turns": (session.get("turns") or [])[-8:],
    }


def _is_greeting_only(text: str) -> bool:
    normalized = text.strip().lower().replace("！", "").replace("!", "").replace("。", "")
    greetings = {"你好", "您好", "hi", "hello", "在吗", "哈喽", "嗨"}
    return normalized in greetings or (len(normalized) <= 8 and any(item in normalized for item in greetings))

def _is_confirm(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in _CONFIRM_WORDS if word.isascii()) or any(
        word in text for word in _CONFIRM_WORDS if not word.isascii()
    )


def _is_cancel(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in _CANCEL_WORDS if word.isascii()) or any(
        word in text for word in _CANCEL_WORDS if not word.isascii()
    )


def _looks_like_feedback(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in _FEEDBACK_WORDS if word.isascii()) or any(
        word in text for word in _FEEDBACK_WORDS if not word.isascii()
    )


def _selected_policy_option(text: str, options: dict[str, str]) -> str | None:
    if not options:
        return None
    compact = re.sub(r"\s+", "", text).lower()
    if len(compact) == 1 and compact.upper() in options:
        return options[compact.upper()]
    label_patterns = {
        "A": ("方案a", "选a", "选择a", "用a", "第一个", "方案一"),
        "B": ("方案b", "选b", "选择b", "用b", "第二个", "方案二"),
        "C": ("方案c", "选c", "选择c", "用c", "第三个", "方案三"),
        "D": ("方案d", "选d", "选择d", "用d", "第四个", "方案四"),
    }
    for label, patterns in label_patterns.items():
        if label in options and any(pattern in compact for pattern in patterns):
            return options[label]
    profile_patterns = {
        "latency_first": ("低时延", "低延迟", "最快", "时延优先", "延迟优先", "latency"),
        "cost_first": ("低成本", "便宜", "省钱", "成本优先", "cost"),
        "balanced_reliability": ("均衡", "可靠", "高可靠", "稳定", "sla", "quality"),
        "recommended": ("推荐", "默认", "你建议", "最优", "best"),
    }
    for key, patterns in profile_patterns.items():
        if key in options and any(pattern in compact for pattern in patterns):
            return options[key]
    return None


def _looks_like_cluster_inventory_question(text: str) -> bool:
    lower = text.lower()
    subject_terms = ("节点", "实例", "机器", "资源", "集群", "node", "instance", "cluster")
    question_terms = (
        "有没有",
        "是否有",
        "有无",
        "有节点吗",
        "有节点么",
        "哪些节点",
        "多少节点",
        "几个节点",
        "节点吗",
        "节点么",
        "节点？",
        "节点?",
        "可用吗",
        "在线吗",
        "节点状态",
        "节点列表",
        "当前节点",
        "现有节点",
        "available node",
        "any node",
        "which node",
    )
    return any(term in lower for term in subject_terms) and any(term in lower for term in question_terms)


def _looks_like_general_chat_question(text: str) -> bool:
    lower = text.lower()
    knowledge_terms = (
        "读过",
        "看过",
        "听过",
        "知道",
        "了解",
        "是什么",
        "为什么",
        "怎么理解",
        "讲讲",
        "介绍",
        "解释",
        "论文",
        "书",
        "小说",
        "作者",
        "paper",
        "article",
        "book",
        "novel",
        "what is",
        "why",
        "explain",
        "tell me",
    )
    task_action_terms = (
        "部署",
        "调度",
        "下发",
        "提交",
        "运行",
        "创建",
        "分配",
        "推荐节点",
        "选节点",
        "生成策略",
        "仿真",
        "预算",
        "时延",
        "延迟",
        "cpu",
        "gpu",
        "内存",
        "带宽",
        "lease",
        "schedule",
        "deploy",
        "commit",
    )
    return (
        any(term in lower or term in text for term in knowledge_terms)
        and not any(term in lower or term in text for term in task_action_terms)
    )


def _looks_like_scheduling_request(text: str) -> bool:
    lower = text.lower()
    scheduling_terms = (
        "部署",
        "调度",
        "任务",
        "节点",
        "资源",
        "算力",
        "cpu",
        "gpu",
        "内存",
        "时延",
        "延迟",
        "预算",
        "成本",
        "安全",
        "带宽",
        "推理",
        "训练",
        "分析",
        "批处理",
        "流式",
        "在线服务",
        "服务",
        "lease",
        "schedule",
        "deploy",
        "node",
        "cluster",
        "latency",
        "budget",
        "inference",
        "training",
        "analytics",
    )
    resource_patterns = (
        r"\d+(\.\d+)?\s*(核|core|cores|cpu)",
        r"\d+(\.\d+)?\s*(gb|g|mb)\s*(内存|memory|ram)?",
        r"\d+(\.\d+)?\s*(ms|毫秒)",
    )
    return (
        any(term in lower or term in text for term in scheduling_terms)
        or bool(_mentioned_regions(text))
        or any(re.search(pattern, lower, re.IGNORECASE) for pattern in resource_patterns)
    )


def _mentioned_regions(text: str) -> list[str]:
    lower = text.lower()
    regions: list[str] = []
    for raw, region in REGION_ALIASES.items():
        haystack = lower if raw.isascii() else text
        needle = raw.lower() if raw.isascii() else raw
        if needle in haystack and region not in regions:
            regions.append(region)
    return regions


def _cluster_inventory_response(text: str, report: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    nodes = list(report.get("nodes") or [])
    regions: dict[str, dict[str, int]] = {}
    for node in nodes:
        region = str(node.get("service_region") or node.get("location") or node.get("region") or "unknown")
        summary = regions.setdefault(region, {"registered": 0, "online": 0})
        summary["registered"] += 1
        if bool(node.get("online")):
            summary["online"] += 1
    requested_regions = _mentioned_regions(text)
    inventory = {
        "requested_regions": requested_regions,
        "node_count": len(nodes),
        "online_nodes": sum(item["online"] for item in regions.values()),
        "regions": regions,
    }
    if not nodes:
        return "当前控制面没有已注册节点，因此也没有可确认存在的地域节点。请先手动启动 CloudSimPlus、sim-backend 或节点 Agent 上报节点。", inventory

    available = "、".join(
        f"{_REGION_LABELS.get(region, region)} {counts['online']} 个在线"
        for region, counts in sorted(regions.items())
        if counts["online"] > 0
    ) or "暂无在线节点"
    if requested_regions:
        statements = []
        for region in requested_regions:
            counts = regions.get(region)
            label = _REGION_LABELS.get(region, region)
            if counts is None:
                statements.append(f"当前没有已注册的{label}节点")
            elif counts["online"] == 0:
                statements.append(f"{label}有 {counts['registered']} 个已注册节点，但当前都不在线")
            else:
                statements.append(f"{label}有 {counts['registered']} 个已注册节点，其中 {counts['online']} 个在线")
        response = "；".join(statements) + f"。当前在线地域为：{available}。"
    else:
        response = f"当前已注册 {len(nodes)} 个节点，在线地域为：{available}。"
    return response + " 是否满足某个任务的调度要求，还需要结合资源规格和约束继续检查。", inventory


def _region_availability_response(requirement_session: dict[str, Any]) -> str | None:
    availability = requirement_session.get("region_availability") or {}
    missing = availability.get("unregistered_regions") or []
    offline = availability.get("offline_regions") or []
    if not missing and not offline:
        return None
    registered = availability.get("registered_regions") or {}
    online = availability.get("online_regions") or {}
    available_labels = [
        f"{_display_region(region)}（{online_count} 个在线）"
        for region, online_count in sorted(online.items())
        if online_count > 0
    ]
    lines = ["### 节点库存核验", "", "**结论**"]
    for region in missing:
        lines.append(f"- 当前控制面没有已注册的 `{_display_region(region)}` 节点，无法按该地域生成可执行推荐。")
    for region in offline:
        lines.append(
            f"- `{_display_region(region)}` 已注册 {registered.get(region, 0)} 个节点，但当前没有在线节点，暂不可调度。"
        )
    lines.extend(
        [
            "",
            "**当前可选地域**",
            f"- {'、'.join(available_labels) if available_labels else '当前没有在线可调度地域。'}",
            "",
            "**如何继续**",
            "- 你可以指定一个当前可选地域，或回复 `地域不限`，让我在现有节点池中继续推荐。",
            "- 任务类型、资源规格、预算和安全约束都可选补充，不是继续推荐的前置条件。",
        ]
    )
    return "\n".join(lines)


def _questions_response(requirement_session: dict[str, Any]) -> str:
    questions = requirement_session.get("questions") or []
    requirement = requirement_session.get("requirement") or {}
    recognized: list[str] = []
    pending: list[str] = []
    optional: list[str] = []
    workload = requirement.get("workload_type")
    workload_unspecified = bool((requirement.get("deployment") or {}).get("workload_type_unspecified"))
    if workload_unspecified:
        recognized.append("任务类型 `未指定`（将采用通用资源画像估算，可选补充）")
    elif workload and "workload_type" not in (requirement.get("missing_fields") or []):
        recognized.append(f"任务类型 `{_WORKLOAD_LABELS.get(str(workload).lower(), workload)}`")
    else:
        pending.append("任务类型：推理 / 训练 / 流式 / 分析 / 批处理")
    regions = requirement.get("region_preference") or []
    if regions:
        recognized.append("部署地域 " + "、".join(f"`{_display_region(region)}`" for region in regions))
    elif (requirement.get("deployment") or {}).get("region_unspecified"):
        recognized.append("部署地域 `不限`（将在当前在线节点池中择优）")
    else:
        pending.append("部署地域或数据驻留要求：例如上海、北京、深圳、成都，或回复 `地域不限`")
    resource_parts = []
    for field, label, suffix in (("cpu_cores", "CPU", " 核"), ("memory_gb", "内存", " GB"), ("gpu_count", "GPU", " 张")):
        value = requirement.get(field)
        if value is not None:
            resource_parts.append(f"{label} `{_format_number(value) + suffix}`")
    if resource_parts:
        recognized.append("，".join(resource_parts))
    else:
        optional.append("资源规格：CPU 核数、内存 GB、GPU 数量；不填时会使用任务类型默认画像")
    latency = requirement.get("latency_target_ms")
    if latency is not None:
        recognized.append(f"时延目标 `{_format_number(latency)} ms`")
    else:
        optional.append("时延目标：例如 `50ms`；没有硬性要求可说明 `无硬性时延`")
    bandwidth = requirement.get("bandwidth_mbps")
    if bandwidth is not None:
        recognized.append(f"带宽要求 `{_format_number(bandwidth)} Mbps`")
    else:
        optional.append("网络带宽：例如 `100Mbps`；不填则根据节点网络画像预估")
    budget = requirement.get("budget_limit")
    if budget is not None:
        recognized.append(f"预算上限 `{_format_number(budget)}`")
    else:
        optional.append("预算上限：例如 `30`；如果只要求低成本，也可以说 `优先省钱`")
    security = requirement.get("security_level")
    if security:
        recognized.append(f"安全等级 `{_security_label(security)}`")
    else:
        optional.append("安全等级：低 / 中 / 高；高安全会启用更强隔离和加密约束")
    priority = requirement.get("priority")
    priority_vector = requirement.get("priority_vector") or {}
    if priority and priority != "balanced":
        recognized.append(f"优化倾向 `{_priority_label(priority)}`")
    elif priority_vector:
        recognized.append("优化倾向 " + "、".join(f"`{_priority_label(key)}={_format_number(value, 2)}`" for key, value in priority_vector.items()))
    else:
        optional.append("优化目标：低时延 / 低成本 / 高可靠 / 高安全 / 均衡")
    if not questions:
        questions = ["请补充业务类型、部署地域、时延目标、预算或安全等级中的关键信息。"]
    lines = ["### 需求理解", ""]
    if recognized:
        lines.extend(["**已识别**", *(f"- {item}" for item in recognized), ""])
    if pending:
        lines.extend(["**仍缺少的关键条件**", *(f"- {item}" for item in pending), ""])
    lines.extend(["**还需确认**", *(f"- {question}" for question in questions)])
    if optional:
        lines.extend(["", "**可选补充条件**", *(f"- {item}" for item in optional)])
    availability = requirement_session.get("region_availability") or {}
    online_regions = availability.get("online_regions") or {}
    if online_regions:
        region_text = "、".join(
            f"{_display_region(region)} {count} 个在线"
            for region, count in sorted(online_regions.items())
            if count
        )
        if region_text:
            lines.extend(["", "**当前库存提示**", f"- 当前可见在线地域：{region_text}。"])
    lines.append("\n补充以上信息后，我会生成可审计的调度推荐。")
    return "\n".join(lines)


def _policy_options_response(comparison: dict[str, Any]) -> str:
    options = comparison.get("options") or []
    recommended = comparison.get("recommended_option") or {}
    lines = [
        "### 多方案策略对比",
        "",
        "我基于同一份需求生成了多个可选策略。底层调度仍由确定性评分完成，Hermes 负责组织方案、解释取舍，并等待你选择。",
        "",
        "| 方案 | 策略取向 | 推荐节点 | 稳定时延 | 预计成本 | SLA 概率 | 结论 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for option in options:
        conclusion = "可选"
        if not option.get("feasible"):
            conclusion = "不可下发"
        elif recommended.get("policy_id") == option.get("policy_id"):
            conclusion = "推荐"
        lines.append(
            "| {label} | {profile} | `{node}` | `{latency} ms` | `{cost}` | `{sla}` | **{conclusion}** |".format(
                label=option.get("label", "--"),
                profile=option.get("profile_name", option.get("profile", "--")),
                node=option.get("selected_node") or "--",
                latency=_format_number(option.get("expected_latency_ms")),
                cost=_format_number(option.get("expected_cost")),
                sla=_format_percent(option.get("sla_probability")),
                conclusion=conclusion,
            )
        )
    lines.extend(["", "**推荐判断**", f"- {comparison.get('explanation') or '请根据业务目标选择一个方案。'}"])
    lines.extend(["", "**怎么选择**"])
    for option in options:
        if not option.get("feasible"):
            reason = "当前约束下没有可执行候选"
        elif option.get("profile") == "latency_first":
            reason = "适合线上推理、交互式服务或对响应速度更敏感的场景"
        elif option.get("profile") == "cost_first":
            reason = "适合预算敏感、可接受略高时延的批处理或离线分析场景"
        else:
            reason = "适合希望兼顾稳定性、SLA 和成本的默认生产型选择"
        lines.append(f"- **方案 {option.get('label')}（{option.get('profile_name')}）**：{reason}。")
    lines.extend(
        [
            "",
            "**下一步**",
            "- 回复 `选 A`、`选 B`、`选 C`，或直接说 `用低成本方案`、`用推荐方案`。",
            "- 选择后我会锁定对应策略，并开启 **正式下发** 按钮；聊天文本仍不会直接提交任务。",
        ]
    )
    return "\n".join(lines)


def _selected_option_response(policy: dict[str, Any], simulation: dict[str, Any]) -> str:
    return _policy_response(policy, simulation, prefix="已选择该候选方案，策略已锁定。")


def _option_selection_help_response(options: dict[str, str]) -> str:
    labels = sorted(label for label in options if len(label) == 1 and label.isalpha())
    choices = "、".join(f"`选 {label}`" for label in labels) or "`用推荐方案`"
    return (
        "### 等待选择方案\n\n"
        f"我已经生成了多方案对比，请先回复 {choices}，"
        "或说明 `用低成本方案`、`用低时延方案`、`用推荐方案`。\n\n"
        "选择具体方案后，我会锁定对应策略，并开启 **正式下发** 按钮。"
    )


def _policy_response(policy: dict[str, Any], simulation: dict[str, Any], *, prefix: str | None = None) -> str:
    effect = policy["expected_effect"]
    compute = policy["selected_compute"]
    network = policy["selected_network"]
    explanation = policy.get("explanation") or {}
    risks = _dedupe_text(simulation.get("risks") or explanation.get("risks") or [])
    diagnostics = simulation.get("diagnostics") or {}
    node_id = compute.get("node_id")
    feasible = bool(simulation.get("feasible") and node_id)
    region = _display_region(compute.get("region") or network.get("target_region"))
    source_region = _display_region(network.get("source_region"))
    target_region = _display_region(network.get("target_region"))
    requirement = policy.get("requirement") or {}
    resource_config = policy.get("resource_config") or {}
    workload_type = requirement.get("workload_type")
    workload_unspecified = bool((requirement.get("deployment") or {}).get("workload_type_unspecified"))
    workload = "未指定（通用资源画像估算）" if workload_unspecified else _WORKLOAD_LABELS.get(str(workload_type or "").lower(), workload_type or "未标明")
    title = "### 优化后的调度推荐" if prefix else ("### 调度推荐" if feasible else "### 调度预演结果")
    lines = [title, ""]
    if prefix:
        lines.extend([f"**说明：** {prefix}", ""])
    lines.append(f"**策略编号：** `{policy['policy_id']}`")
    if not feasible:
        if workload_unspecified:
            lines.append("**任务类型：** 未指定（使用通用资源画像估算，后续可选补充）")
        lines.extend(["**结果：** 当前没有可正式下发的候选节点。", "", "**原因**"])
        lines.extend(f"- {risk}" for risk in (risks or ["当前资源、地域、网络或安全约束下没有可用候选。"]))
        lines.extend(["", "**调整建议**"])
        suggestions = explanation.get("questions") or ["请调整地域、资源规格、时延目标或安全约束后重新预演。"]
        lines.extend(f"- {suggestion}" for suggestion in suggestions)
        lines.extend(["", "本次仅为预演结果，**不会进入待调度队列**，也不可正式下发。"])
        return "\n".join(lines)

    lines.extend(
        [
            f"**推荐节点：** `{node_id}`（{region}）",
            f"**任务类型：** {workload}",
            f"**申请资源：** {_resource_config_text(resource_config)}",
            f"**网络路径：** {source_region} -> {target_region}",
            "",
            "**关键指标**",
            f"- 稳定时延：`{_format_number(network.get('stable_latency_ms'))} ms`",
            f"- 提交后负载：`{_format_percent(effect['load'].get('projected_load'))}`",
            f"- 预计成本：`{_format_number(effect['cost'].get('expected_cost'))}`",
            f"- SLA 概率：`{_format_percent(effect['service_quality'].get('sla_probability'))}`",
            f"- 安全评分：`{_format_percent(effect['security'].get('security_score'))}`",
            "",
            "**推荐理由**",
        ]
    )
    basis = _factor_summary(explanation.get("factors") or [])
    estimate = "，本次采用通用资源画像估算" if workload_unspecified else ""
    lines.append(
        f"- `{node_id}` 在当前约束下综合排序最优，主要依据为{basis}，"
        f"稳定时延 `{_format_number(network.get('stable_latency_ms'))} ms`、"
        f"提交后负载 `{_format_percent(effect['load'].get('projected_load'))}`{estimate}。"
    )
    recommendation = diagnostics.get("commit_recommendation", simulation.get("status"))
    lines.extend(["", "**下发建议**", f"- {_commit_recommendation_text(recommendation)}"])
    lines.extend(f"- 风险：{risk}" for risk in risks)
    lines.extend(f"- 可选补充：{question}" for question in (explanation.get("questions") or []))
    lines.extend(
        [
            "",
            "确认指标和风险可接受后，请点击 **「正式下发」**；聊天文本不会直接下发任务。",
            "需要调整时，可继续说明希望优化的时延、成本、安全或服务质量目标。",
        ]
    )
    return "\n".join(lines)


def _commit_response(committed: dict[str, Any]) -> str:
    task = committed.get("submitted_task") or {}
    policy = committed.get("policy") or {}
    resources = policy.get("resource_config") or task.get("demand") or {}
    return (
        "### 正式下发成功\n\n"
        f"**策略编号：** `{policy.get('policy_id')}`\n"
        f"**任务编号：** `{task.get('task_id')}`\n"
        f"**申请资源：** {_resource_config_text(resources)}\n\n"
        "任务已进入控制面待调度队列，节点 Agent 领取租约后会执行并回传结果。"
    )


def _display_region(value: Any) -> str:
    text = str(value or "--")
    return _REGION_LABELS.get(text.lower(), text)


def _security_label(value: Any) -> str:
    return {"low": "低", "medium": "中", "high": "高"}.get(str(value).lower(), str(value))


def _priority_label(value: Any) -> str:
    return {
        "latency": "低时延",
        "cost": "低成本",
        "quality": "高可靠",
        "balanced": "均衡",
        "security": "高安全",
        "network": "网络稳定",
        "balance": "负载均衡",
        "fragmentation": "资源碎片",
        "locality": "局部性",
    }.get(str(value).lower(), str(value))


def _format_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _format_percent(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _resource_config_text(resources: dict[str, Any]) -> str:
    cpu = resources.get("cpu_cores", resources.get("cpu"))
    memory = resources.get("memory_gb", resources.get("memory"))
    gpu = resources.get("gpu_count", resources.get("gpu"))
    storage = resources.get("storage_gb", resources.get("storage"))
    return (
        f"`{_format_number(cpu)} CPU` / `{_format_number(memory)} GB 内存` / "
        f"`{_format_number(gpu, 0)} GPU` / `{_format_number(storage)} GB 存储`"
    )


def _factor_summary(factors: list[Any]) -> str:
    labels: list[str] = []
    for factor in factors[:3]:
        metric = str(factor).split(":", 1)[0].strip().lower()
        label = _FACTOR_LABELS.get(metric)
        if label and label not in labels:
            labels.append(label)
    return "、".join(labels) if labels else "综合评分"


def _response_stream_chunks(response: str) -> list[str]:
    chunks: list[str] = []
    for block in response.splitlines(keepends=True):
        if block:
            chunks.append(block)
    if not chunks and response:
        chunks.append(response)
    return chunks


def _dedupe_text(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _commit_recommendation_text(recommendation: Any) -> str:
    labels = {
        "safe_to_commit": "**可正式下发。** 当前仿真未识别到需先确认的风险。",
        "review_before_commit": "**可正式下发，但建议先确认风险。** 下方列出了触发该建议的具体原因。",
        "do_not_commit": "**不可正式下发。** 当前没有满足约束的可执行候选。",
    }
    return labels.get(str(recommendation), "**请先确认仿真结果后再决定是否正式下发。**")


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
    }



def _tool_label(name: str) -> str:
    labels = {
        "get_cluster_state": "查询集群状态",
        "start_requirement_dialogue": "需求澄清",
        "continue_requirement_dialogue": "更新需求",
        "draft_compute_network_policy": "生成策略",
        "compare_policy_options": "多方案对比",
        "explain_policy": "锁定方案",
        "simulate_policy": "策略仿真",
        "commit_policy": "正式下发",
        "optimize_policy_from_feedback": "优化策略",
    }
    return labels.get(name, name)

def _short_result(result: dict[str, Any]) -> str:
    if "nodes" in result and "totals" in result:
        regions = sorted(
            {
                str(node.get("service_region") or node.get("location") or node.get("region"))
                for node in result.get("nodes", [])
                if node.get("service_region") or node.get("location") or node.get("region")
            }
        )
        online = sum(1 for node in result.get("nodes", []) if node.get("online"))
        return f"cluster nodes={len(result.get('nodes', []))} online={online} regions={','.join(regions) or '--'}"
    if "policy_id" in result:
        return f"policy={result.get('policy_id')} status={result.get('status')}"
    if result.get("status") == "compared" and "options" in result:
        feasible = sum(1 for option in result.get("options", []) if option.get("feasible"))
        recommended = result.get("recommended_option") or {}
        return f"policy_options={len(result.get('options', []))} feasible={feasible} recommended={recommended.get('label', '--')}"
    if "session_id" in result:
        summary = f"requirement_session={result.get('session_id')} status={result.get('status')} questions={len(result.get('questions') or [])}"
        availability = result.get("region_availability") or {}
        unavailable = availability.get("unregistered_regions") or []
        offline = availability.get("offline_regions") or []
        if unavailable:
            summary += " unavailable_regions=" + ",".join(_display_region(region) for region in unavailable)
        if offline:
            summary += " offline_regions=" + ",".join(_display_region(region) for region in offline)
        return summary
    if "policy" in result:
        policy = result["policy"]
        return f"policy={policy.get('policy_id')} status={policy.get('status')}"
    if "submitted_task" in result:
        return f"commit status={result.get('status')} task={result['submitted_task'].get('task_id')}"
    if "feasible" in result:
        return f"simulation feasible={result.get('feasible')} status={result.get('status')}"
    return json.dumps(result, ensure_ascii=False)[:300]


def _compact_artifacts(artifacts: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in artifacts.items():
        if key == "policy" and isinstance(value, dict):
            compact[key] = _policy_summary(value)
        elif key == "simulation" and isinstance(value, dict):
            compact[key] = {
                "feasible": value.get("feasible"),
                "status": value.get("status"),
                "diagnostics": value.get("diagnostics"),
            }
        elif key == "policy_options" and isinstance(value, dict):
            compact[key] = {
                "status": value.get("status"),
                "recommended_policy_id": value.get("recommended_policy_id"),
                "explanation": value.get("explanation"),
                "options": [
                    {
                        "label": option.get("label"),
                        "profile": option.get("profile"),
                        "profile_name": option.get("profile_name"),
                        "policy_id": option.get("policy_id"),
                        "feasible": option.get("feasible"),
                        "selected_node": option.get("selected_node"),
                        "expected_latency_ms": option.get("expected_latency_ms"),
                        "expected_cost": option.get("expected_cost"),
                        "sla_probability": option.get("sla_probability"),
                    }
                    for option in value.get("options", [])
                ],
            }
        elif key == "requirement_session" and isinstance(value, dict):
            compact[key] = {
                "session_id": value.get("session_id"),
                "status": value.get("status"),
                "questions": value.get("questions"),
                "requirement": value.get("requirement"),
                "region_availability": value.get("region_availability"),
            }
        elif key == "commit" and isinstance(value, dict):
            submitted = dict(value.get("submitted_task") or {})
            compact[key] = {
                "status": value.get("status"),
                "policy": _policy_summary(dict(value.get("policy") or {})),
                "submitted_task": {
                    "task_id": submitted.get("task_id"),
                    "task_type": submitted.get("task_type"),
                    "status": submitted.get("status"),
                    "demand": submitted.get("demand"),
                    "target_node_id": submitted.get("target_node_id"),
                    "execution_mode": dict(submitted.get("execution") or {}).get("mode"),
                },
            }
        else:
            compact[key] = value
    return compact
