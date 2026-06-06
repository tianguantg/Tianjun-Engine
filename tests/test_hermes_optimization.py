from __future__ import annotations

from types import SimpleNamespace

from tianjun.core import UserFeedback
from tianjun.application.control_plane import CentralControlPlane
from tianjun.chat.runtime import ChatRuntime
from tianjun.domain import Node, ResourceVector
from tianjun.policy.clarifier import clarification_questions
from tianjun.policy.feedback import parse_feedback_instruction
from tianjun.policy.generator import ComputeNetworkPolicyGenerator


def generator() -> ComputeNetworkPolicyGenerator:
    return ComputeNetworkPolicyGenerator()


def test_implicit_workload_language_is_classified_without_llm() -> None:
    policy = generator()
    assert policy.parse_requirement("上海搞个在线问答服务").workload_type == "inference"
    assert policy.parse_requirement("北京帮我跑个 embedding").workload_type == "inference"
    assert policy.parse_requirement("深圳做一批批量打标签").workload_type == "analytics"


def test_region_aliases_short_codes_and_typo_recovery_are_supported() -> None:
    policy = generator()
    assert policy.parse_requirement("苏州部署在线服务").region_preference == ["east"]
    assert policy.parse_requirement("在 cd 部署在线服务").region_preference == ["west"]
    assert policy.parse_requirement("部署在成嘟的在线服务").region_preference == ["west"]
    assert policy.parse_requirement("武汉部署在线服务").region_preference == ["wuhan"]


def test_multi_objective_priority_vector_reaches_scheduler_metrics() -> None:
    policy = generator()
    requirement = policy.parse_requirement("上海在线问答服务，低时延但不能超预算，预算 20 万元")
    assert requirement.priority == "latency"
    assert requirement.priority_vector["latency"] > 0
    assert requirement.priority_vector["cost"] > 0
    task = policy.task_from_requirement(requirement, task_id="intent-vector")
    assert task.intent_weights["performance"] > 0
    assert task.intent_weights["network"] > 0
    assert task.intent_weights["cost"] > 0


def test_weighted_slot_confidence_reflects_missing_inference_latency() -> None:
    policy = generator()
    missing_latency = policy.parse_requirement("上海部署低时延在线问答服务")
    explicit_latency = policy.parse_requirement("上海部署低时延在线问答服务，响应低于 50ms")
    assert "latency_target_ms" in missing_latency.missing_fields
    assert explicit_latency.confidence > missing_latency.confidence
    assert missing_latency.slot_confidence["latency_target_ms"] == 0.0
    assert explicit_latency.slot_confidence["latency_target_ms"] == 1.0


def test_feedback_uses_real_metric_keys_and_intensity() -> None:
    slight = parse_feedback_instruction(policy_id="p", instruction="响应稍微慢了一点")
    severe = parse_feedback_instruction(policy_id="p", instruction="响应完全无法接受，太慢了")
    assert set(slight["preference_delta"]).issubset({"performance", "completion", "network"})
    assert severe["preference_delta"]["performance"] > slight["preference_delta"]["performance"]
    locality = parse_feedback_instruction(policy_id="p", instruction="数据不在本地，需要就近调度")
    assert locality["preference_delta"]["locality"] > 0


def test_feedback_metric_preferences_reach_task_weights() -> None:
    policy = generator()
    requirement = policy.parse_requirement("上海分析任务，4 核 CPU")
    payload = parse_feedback_instruction(policy_id="p", instruction="节点负载太高了，需要更均衡")
    feedback = UserFeedback.from_dict(payload)
    optimized = policy.apply_feedback(requirement, feedback)
    task = policy.task_from_requirement(optimized, task_id="feedback-vector")
    assert optimized.metric_preferences["balance"] > 0
    assert task.intent_weights["balance"] > 0


def test_clarification_prioritizes_unavailable_region_relaxation() -> None:
    policy = generator()
    requirement = policy.parse_requirement("武汉部署低时延在线问答服务")
    node = Node(
        node_id="east-a",
        region="dc1",
        location="shanghai",
        capacity=ResourceVector(cpu=8, memory=32, storage=100),
    )
    questions = clarification_questions(requirement, [node])
    assert questions[0].startswith("当前在线节点无法满足地域")


def _control_plane_with_nodes() -> CentralControlPlane:
    control = CentralControlPlane()
    control.register_node(
        Node(
            node_id="south-fast",
            region="dc3",
            location="shenzhen",
            service_region="south",
            capacity=ResourceVector(cpu=16, memory=64, gpu=0, storage=200),
            cost_per_tick=3.2,
            base_reliability=0.985,
            performance_factors={"analytics": 1.45, "batch_cpu": 1.35},
        )
    )
    control.register_node(
        Node(
            node_id="south-cheap",
            region="dc3",
            location="guangzhou",
            service_region="south",
            capacity=ResourceVector(cpu=16, memory=64, gpu=0, storage=200),
            cost_per_tick=0.7,
            base_reliability=0.96,
            performance_factors={"analytics": 0.9, "batch_cpu": 0.9},
        )
    )
    control.register_node(
        Node(
            node_id="south-stable",
            region="dc3",
            location="guangzhou",
            service_region="south",
            capacity=ResourceVector(cpu=20, memory=96, gpu=0, storage=300),
            cost_per_tick=1.4,
            base_reliability=0.995,
            performance_factors={"analytics": 1.1, "batch_cpu": 1.1},
        )
    )
    return control


def test_control_plane_generates_comparable_policy_options() -> None:
    control = _control_plane_with_nodes()
    requirement = control.parse_requirement("华南分析任务，4 核 8GB，不需要 GPU，预算 30，时延 80ms，安全中等")
    comparison = control.compare_policy_options(requirement)
    assert comparison["status"] == "compared"
    assert [option["label"] for option in comparison["options"]] == ["A", "B", "C"]
    assert {option["profile"] for option in comparison["options"]} == {
        "latency_first",
        "cost_first",
        "balanced_reliability",
    }
    assert comparison["recommended_policy_id"]
    assert all(option["policy_id"] in control.policies for option in comparison["options"])


def test_chat_runtime_outputs_markdown_options_and_requires_selection() -> None:
    chat = ChatRuntime(_control_plane_with_nodes())
    result = chat.start("华南分析任务，4 核 8GB，不需要 GPU，预算 30，时延 80ms，安全中等")
    assert result["action"] == "compare_policy_options"
    assert "### 多方案策略对比" in result["message"]
    assert "| 方案 | 策略取向 | 推荐节点 |" in result["message"]
    assert result["requires_user_button"] is False
    session = result["session"]
    assert session["pending_option_selection"] is True

    selected = chat.continue_session(session["session_id"], "选 B")
    assert selected["action"] == "select_policy_option"
    assert selected["requires_user_button"] is True
    assert selected["commit_policy_id"]
    assert "### 优化后的调度推荐" in selected["message"]


class FakeLLMClient:
    settings = SimpleNamespace(timeout_seconds=1, describe=lambda: {"model": "fake-deepseek"})

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    def is_enabled(self) -> bool:
        return True

    def chat(self, messages: list[dict[str, str]], *, timeout_seconds: float | None = None) -> str:
        self.calls.append(messages)
        return "看过。《百年孤独》是加西亚·马尔克斯的代表作，也可以随时回到天钧调度演示。"


def test_general_chat_uses_llm_instead_of_requirement_parser() -> None:
    fake_llm = FakeLLMClient()
    chat = ChatRuntime(_control_plane_with_nodes(), llm_client=fake_llm)
    result = chat.start("你看过百年孤独吗")
    assert result["action"] == "general_chat"
    assert "百年孤独" in result["message"]
    assert result["session"]["requirement_session_id"] is None
    assert fake_llm.calls


def test_domain_knowledge_question_uses_llm_not_scheduler_dialogue() -> None:
    fake_llm = FakeLLMClient()
    chat = ChatRuntime(_control_plane_with_nodes(), llm_client=fake_llm)
    result = chat.start("你读过算力网络的论文吗")
    assert result["action"] == "general_chat"
    assert result["session"]["requirement_session_id"] is None
    assert fake_llm.calls


def test_requirement_clarification_shows_full_condition_checklist() -> None:
    chat = ChatRuntime(_control_plane_with_nodes())
    result = chat.start("帮我部署一个任务")
    assert result["action"] == "clarify_requirement"
    message = result["message"]
    assert "**仍缺少的关键条件**" in message
    assert "**可选补充条件**" in message
    assert "资源规格" in message
    assert "时延目标" in message
    assert "预算上限" in message
    assert "安全等级" in message
    assert "优化目标" in message
