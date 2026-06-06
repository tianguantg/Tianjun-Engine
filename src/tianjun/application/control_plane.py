from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from math import ceil
from statistics import mean
from typing import Any

from ..core import ComputeNetworkPolicy, UserFeedback, UserRequirement
from ..domain import ExecutionRecord, NetworkPathProfile, Node, PhysicalTopology, PolicyAdjustment, PolicyState, RunningTask, SchedulingDecision, Task, TaskStatus, clamp, normalize_weights
from ..policy.optimizer import PolicyOptimizer
from ..policy.clarifier import ConversationTurn, RequirementSession, clarification_questions, session_status
from ..policy.feedback import parse_feedback_instruction
from ..policy.generator import ComputeNetworkPolicyGenerator
from ..policy.simulator import simulate_policy
from ..storage.sqlite_state_store import SQLiteStateStore
from ..scheduling.engine import ClosedLoopAdaptiveScheduler
from ..ml.runtime import TrainedModelRuntime
from ..scenarios import execution_from_dict, node_from_dict, task_from_dict


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@dataclass(slots=True)
class TaskLease:
    task_id: str
    node_id: str
    issued_tick: int
    predicted_finish_tick: int
    predicted_cost: float
    explanation: str
    task: Task
    decision: SchedulingDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "issued_tick": self.issued_tick,
            "predicted_finish_tick": self.predicted_finish_tick,
            "predicted_cost": round(self.predicted_cost, 4),
            "explanation": self.explanation,
            "task": self.task.to_dict(),
            "decision": self.decision.to_dict(),
        }


class CentralControlPlane:
    def __init__(
        self,
        policy_state: PolicyState | None = None,
        policy_update_interval: int = 2,
        heartbeat_timeout_seconds: float = 15.0,
        state_store: SQLiteStateStore | None = None,
        scheduler: ClosedLoopAdaptiveScheduler | None = None,
        model_runtime: TrainedModelRuntime | None = None,
    ) -> None:
        self.policy_state = policy_state or PolicyState()
        self.scheduler = scheduler or ClosedLoopAdaptiveScheduler(
            self.policy_state,
            model_runtime=model_runtime,
        )
        self.optimizer = PolicyOptimizer()
        self.policy_generator = ComputeNetworkPolicyGenerator()
        self.policy_update_interval = policy_update_interval
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.state_store = state_store

        self.lock = threading.RLock()
        self.started_at = time.monotonic()
        self.nodes: dict[str, Node] = {}
        self.tasks: dict[str, Task] = {}
        self.pending_queue: list[str] = []
        self.leases: dict[str, TaskLease] = {}
        self.decision_log: list[SchedulingDecision] = []
        self.execution_history: list[ExecutionRecord] = []
        self.task_progress: dict[str, dict[str, Any]] = {}
        self.progress_events: list[dict[str, Any]] = []
        self.last_heartbeat_at: dict[str, float] = {}
        self.policies: dict[str, ComputeNetworkPolicy] = {}
        self.policy_tasks: dict[str, Task] = {}
        self.user_feedback: list[UserFeedback] = []
        self.requirement_sessions: dict[str, RequirementSession] = {}
        self.physical_topology: PhysicalTopology | None = None

        if self.state_store is not None:
            self._restore_from_store()
            self.state_store.set_control_value("policy_weights", self.policy_state.current_weights())

    def register_node(self, node: Node) -> dict[str, Any]:
        with self.lock:
            current = self.nodes.get(node.node_id)
            if current is not None:
                node.running_tasks = current.running_tasks
                node.reliability_score = current.reliability_score
                node.health_score = current.health_score
            node.online = True
            node.telemetry_tick = self.current_tick()
            self.nodes[node.node_id] = node
            self.last_heartbeat_at[node.node_id] = time.monotonic()
            self._persist_node(node)
            return node.to_dict()

    def register_topology(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            topology = PhysicalTopology.from_dict(payload)
            self.physical_topology = topology
            self.scheduler.set_physical_topology(topology)
            if self.state_store is not None:
                self.state_store.set_control_value("physical_topology", topology.to_dict())
            return topology.to_dict()

    def submit_task(self, task: Task) -> dict[str, Any]:
        with self.lock:
            if task.task_id in self.tasks:
                raise ValueError(f"Task {task.task_id} already exists.")
            task.submit_tick = self.current_tick()
            if task.deadline is not None and task.deadline <= task.submit_tick:
                task.deadline = task.submit_tick + task.deadline
            self.tasks[task.task_id] = task
            self.pending_queue.append(task.task_id)
            self._persist_task(task)
            return task.to_dict()

    def preview_task(self, task: Task) -> dict[str, Any] | None:
        with self.lock:
            self._expire_stale_nodes()
            decision = self.scheduler.select_node(
                task,
                self.nodes.values(),
                current_tick=self.current_tick(),
                topology_nodes=self.nodes.values(),
            )
            return None if decision is None else decision.to_dict()

    def schedule_pending_task(self, task_id: str) -> dict[str, Any]:
        """Assign one already-submitted task to its best eligible node."""
        with self.lock:
            self._expire_stale_nodes()
            task = self.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task {task_id}.")
            if task.status == TaskStatus.RUNNING and task_id in self.leases:
                lease = self.leases[task_id]
                return {
                    "status": "already_scheduled",
                    "task_id": task_id,
                    "node_id": lease.node_id,
                    "total_score": lease.decision.total_score,
                    "preview_decision": lease.decision.to_dict(),
                    "lease": lease.to_dict(),
                }
            if task.status != TaskStatus.PENDING:
                raise ValueError(f"Task {task_id} is {task.status.value}, not pending.")

            tick = self.current_tick()
            decision = self.scheduler.select_node(
                task,
                self.nodes.values(),
                current_tick=tick,
                topology_nodes=self.nodes.values(),
            )
            if decision is None:
                return {
                    "status": "rejected",
                    "task_id": task_id,
                    "node_id": "",
                    "total_score": 0.0,
                    "preview_decision": None,
                    "lease": None,
                    "reason": "no feasible online node",
                    "task": task.to_dict(),
                }
            node = self.nodes[decision.node_id]
            lease = self._activate_task_lease(
                task=task,
                node=node,
                decision=decision,
                tick=tick,
                remove_from_pending=True,
            )
            return {
                "status": "committed",
                "task_id": task_id,
                "node_id": lease.node_id,
                "total_score": decision.total_score,
                "preview_decision": decision.to_dict(),
                "lease": lease.to_dict(),
            }

    def parse_requirement(
        self,
        message: str,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            requirement = self.policy_generator.parse_requirement(message, overrides=overrides)
            payload = requirement.to_dict()
            payload["questions"] = clarification_questions(requirement, self.nodes.values())
            payload["dialogue_status"] = session_status(requirement, payload["questions"])
            return payload

    def start_requirement_session(
        self,
        message: str,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            requirement = self.policy_generator.parse_requirement(message, overrides=overrides)
            questions = clarification_questions(requirement, self.nodes.values())
            session = RequirementSession(
                session_id=self._new_session_id(),
                requirement=requirement,
                turns=[ConversationTurn(role="user", content=str(message))],
                questions=questions,
                status=session_status(requirement, questions),
            )
            if questions:
                session.turns.append(ConversationTurn(role="assistant", content="\n".join(questions)))
            self.requirement_sessions[session.session_id] = session
            return self._requirement_session_payload(session)

    def continue_requirement_session(
        self,
        session_id: str,
        message: str,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            session = self._session_or_raise(session_id)
            requirement = self.policy_generator.merge_requirement_update(
                session.requirement,
                message,
                overrides=overrides,
            )
            questions = clarification_questions(requirement, self.nodes.values())
            session.requirement = requirement
            session.questions = questions
            session.status = session_status(requirement, questions)
            session.updated_at = time.time()
            session.turns.append(ConversationTurn(role="user", content=str(message)))
            if questions:
                session.turns.append(ConversationTurn(role="assistant", content="\n".join(questions)))
            else:
                session.turns.append(ConversationTurn(role="assistant", content="需求槽位已完整，可以生成算网策略草案。"))
            return self._requirement_session_payload(session)

    def get_requirement_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            return self._requirement_session_payload(self._session_or_raise(session_id))

    def _requirement_session_payload(self, session: RequirementSession) -> dict[str, Any]:
        self._expire_stale_nodes()
        payload = session.to_dict()
        requested_regions = list(session.requirement.region_preference)
        registered_regions: dict[str, int] = {}
        online_regions: dict[str, int] = {}
        for node in self.nodes.values():
            service_region = node.service_region or node.location or node.region
            registered_regions[service_region] = registered_regions.get(service_region, 0) + 1
            if node.online:
                online_regions[service_region] = online_regions.get(service_region, 0) + 1
        payload["region_availability"] = {
            "requested_regions": requested_regions,
            "registered_regions": registered_regions,
            "online_regions": online_regions,
            "unregistered_regions": [region for region in requested_regions if region not in registered_regions],
            "offline_regions": [
                region
                for region in requested_regions
                if region in registered_regions and region not in online_regions
            ],
        }
        return payload

    def draft_policy_from_session(
        self,
        session_id: str,
        *,
        execution_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            session = self._session_or_raise(session_id)
            result = self.draft_policy(
                session.requirement.to_dict(),
                execution_payload=execution_payload,
            )
            result["requirement_session"] = {
                "session_id": session.session_id,
                "status": session.status,
                "questions": list(session.questions),
            }
            return result

    def compare_policy_options_from_session(
        self,
        session_id: str,
        *,
        execution_payload: dict[str, Any] | None = None,
        option_profiles: list[str] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            session = self._session_or_raise(session_id)
            return self.compare_policy_options(
                session.requirement.to_dict(),
                execution_payload=execution_payload,
                option_profiles=option_profiles,
                requirement_session={
                    "session_id": session.session_id,
                    "status": session.status,
                    "questions": list(session.questions),
                },
            )

    def draft_policy(
        self,
        requirement_payload: dict[str, Any],
        *,
        execution_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            self._expire_stale_nodes()
            requirement = UserRequirement.from_dict(requirement_payload)
            execution = None if execution_payload is None else execution_from_dict(execution_payload)
            policy, task = self.policy_generator.draft_policy(
                requirement,
                scheduler=self.scheduler,
                nodes=self.nodes.values(),
                current_tick=self.current_tick(),
                execution=execution,
            )
            self.policies[policy.policy_id] = policy
            self.policy_tasks[policy.policy_id] = task
            return policy.to_dict()

    def compare_policy_options(
        self,
        requirement_payload: dict[str, Any],
        *,
        execution_payload: dict[str, Any] | None = None,
        option_profiles: list[str] | None = None,
        requirement_session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            self._expire_stale_nodes()
            base_requirement = UserRequirement.from_dict(requirement_payload)
            execution = None if execution_payload is None else execution_from_dict(execution_payload)
            profiles = self._normalize_option_profiles(option_profiles)
            options: list[dict[str, Any]] = []
            for index, profile in enumerate(profiles):
                requirement = self._requirement_for_option_profile(base_requirement, profile)
                policy, task = self.policy_generator.draft_policy(
                    requirement,
                    scheduler=self.scheduler,
                    nodes=self.nodes.values(),
                    current_tick=self.current_tick(),
                    policy_id=f"{self.policy_generator._new_policy_id()}_{profile}",
                    execution=execution,
                )
                self.policies[policy.policy_id] = policy
                self.policy_tasks[policy.policy_id] = task
                policy_payload = policy.to_dict()
                simulation = simulate_policy(policy).to_dict()
                options.append(
                    self._policy_option_payload(
                        label=chr(ord("A") + index),
                        profile=profile,
                        policy=policy_payload,
                        simulation=simulation,
                    )
                )
            recommended = self._recommended_policy_option(options)
            return {
                "status": "compared",
                "requirement": base_requirement.to_dict(),
                "requirement_session": requirement_session,
                "option_profiles": profiles,
                "options": options,
                "recommended_option": recommended,
                "recommended_policy_id": None if recommended is None else recommended["policy_id"],
                "explanation": self._policy_options_explanation(options, recommended),
            }

    @staticmethod
    def _normalize_option_profiles(option_profiles: list[str] | None) -> list[str]:
        aliases = {
            "latency": "latency_first",
            "latency_first": "latency_first",
            "low_latency": "latency_first",
            "cost": "cost_first",
            "cost_first": "cost_first",
            "low_cost": "cost_first",
            "balanced": "balanced_reliability",
            "reliability": "balanced_reliability",
            "reliability_first": "balanced_reliability",
            "quality": "balanced_reliability",
            "quality_first": "balanced_reliability",
            "balanced_reliability": "balanced_reliability",
        }
        requested = option_profiles or ["latency_first", "cost_first", "balanced_reliability"]
        profiles: list[str] = []
        for item in requested:
            profile = aliases.get(str(item).strip().lower())
            if profile and profile not in profiles:
                profiles.append(profile)
        return profiles[:4] or ["latency_first", "cost_first", "balanced_reliability"]

    @staticmethod
    def _requirement_for_option_profile(requirement: UserRequirement, profile: str) -> UserRequirement:
        data = requirement.to_dict()
        vector = dict(data.get("priority_vector") or {})
        if profile == "latency_first":
            data["priority"] = "latency"
            vector.update({
                "latency": max(vector.get("latency", 0.0), 1.0),
                "network": max(vector.get("network", 0.0), 0.78),
            })
        elif profile == "cost_first":
            data["priority"] = "cost"
            vector.update({
                "cost": max(vector.get("cost", 0.0), 1.0),
                "balance": max(vector.get("balance", 0.0), 0.45),
            })
        elif profile == "balanced_reliability":
            data["priority"] = "quality"
            vector.update({
                "quality": max(vector.get("quality", 0.0), 0.9),
                "network": max(vector.get("network", 0.0), 0.55),
                "balance": max(vector.get("balance", 0.0), 0.45),
            })
        data["priority_vector"] = vector
        data["objective"] = f"{requirement.objective} | option_profile={profile}"
        data["missing_fields"] = []
        data["confidence"] = max(requirement.confidence, 0.72)
        return UserRequirement.from_dict(data)

    @staticmethod
    def _policy_option_payload(
        *,
        label: str,
        profile: str,
        policy: dict[str, Any],
        simulation: dict[str, Any],
    ) -> dict[str, Any]:
        effect = policy.get("expected_effect") or {}
        latency = effect.get("latency") or {}
        cost = effect.get("cost") or {}
        quality = effect.get("service_quality") or {}
        security = effect.get("security") or {}
        compute = policy.get("selected_compute") or {}
        network = policy.get("selected_network") or {}
        return {
            "label": label,
            "profile": profile,
            "profile_name": {
                "latency_first": "低时延优先",
                "cost_first": "低成本优先",
                "balanced_reliability": "均衡 / 高可靠优先",
            }.get(profile, profile),
            "policy_id": policy.get("policy_id"),
            "status": policy.get("status"),
            "feasible": bool(simulation.get("feasible") and compute.get("node_id")),
            "selected_node": compute.get("node_id"),
            "selected_region": compute.get("region") or network.get("target_region"),
            "expected_latency_ms": latency.get("expected_ms"),
            "expected_cost": cost.get("expected_cost"),
            "budget_margin": cost.get("budget_margin"),
            "sla_probability": quality.get("sla_probability"),
            "security_score": security.get("security_score"),
            "total_score": compute.get("score"),
            "risks": list(simulation.get("risks") or (policy.get("explanation") or {}).get("risks") or []),
            "policy": policy,
            "simulation": simulation,
        }

    @staticmethod
    def _recommended_policy_option(options: list[dict[str, Any]]) -> dict[str, Any] | None:
        feasible = [option for option in options if option.get("feasible")]
        if not feasible:
            return options[0] if options else None

        def score(option: dict[str, Any]) -> float:
            total = float(option.get("total_score") or 0.0)
            sla = float(option.get("sla_probability") or 0.0)
            security = float(option.get("security_score") or 0.0)
            cost = float(option.get("expected_cost") or 0.0)
            latency = float(option.get("expected_latency_ms") or 0.0)
            return (total * 0.46) + (sla * 0.24) + (security * 0.12) - (cost * 0.006) - (latency * 0.0015)

        return max(feasible, key=score)

    @staticmethod
    def _policy_options_explanation(options: list[dict[str, Any]], recommended: dict[str, Any] | None) -> str:
        if not options:
            return "没有生成可对比的策略候选。"
        if recommended is None or not recommended.get("feasible"):
            return "当前多个策略取向下都缺少可正式下发的候选节点，建议放宽地域、资源、预算、网络或安全约束。"
        return (
            f"推荐优先选择方案 {recommended['label']}（{recommended['profile_name']}），"
            "因为它在可行性、SLA 概率、成本和综合评分之间取得了当前最稳的平衡。"
        )

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        with self.lock:
            return self._policy_or_raise(policy_id).to_dict()

    def simulate_policy(self, policy_id: str) -> dict[str, Any]:
        with self.lock:
            policy = self._policy_or_raise(policy_id)
            result = simulate_policy(policy)
            return result.to_dict()

    def commit_policy(self, policy_id: str) -> dict[str, Any]:
        with self.lock:
            policy = self._policy_or_raise(policy_id)
            if policy.status == "failed" or policy.selected_compute.node_id is None:
                raise ValueError(f"Policy {policy_id} has no feasible candidate to commit.")
            task = self.policy_tasks.get(policy_id)
            if task is None:
                task = self.policy_generator.task_from_requirement(
                    policy.requirement,
                    task_id=policy.task_id or f"task_{policy_id}",
                )
                self.policy_tasks[policy_id] = task
            if policy.selected_compute.node_id:
                task.target_node_id = policy.selected_compute.node_id
            # A user-approved policy should not silently execute on a different node or
            # create duplicate attempts unless the future policy explicitly asks for retries.
            task.max_retries = 0
            if task.task_id in self.tasks:
                submitted = self.tasks[task.task_id].to_dict()
                status = "already_committed"
            else:
                submitted = self.submit_task(task_from_dict(task.to_dict()))
                status = "committed"
            policy.status = "committed"
            return {
                "status": status,
                "policy": policy.to_dict(),
                "submitted_task": submitted,
            }

    def update_policy_weights(self, weights: dict[str, Any], *, reason: str = "用户手动提交多维策略权重。") -> dict[str, Any]:
        with self.lock:
            normalized = normalize_weights({str(key): float(value) for key, value in dict(weights or {}).items()})
            if not normalized:
                raise ValueError("weights are required")
            self.policy_state.update(
                tick=self.current_tick(),
                new_weights=normalized,
                reasons=[reason],
                affected_records=0,
                metrics={},
            )
            if self.state_store is not None:
                latest_adjustment = self.policy_state.adjustment_history[-1]
                self.state_store.append_policy_adjustment(latest_adjustment.to_dict())
                self.state_store.set_control_value("policy_weights", self.policy_state.current_weights())
            return {
                "status": "updated",
                "policy_weights": {key: round(value, 4) for key, value in self.policy_state.current_weights().items()},
                "adjustment": self.policy_state.adjustment_history[-1].to_dict(),
            }

    def parse_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            policy_id = str(feedback_payload.get("policy_id", ""))
            if policy_id:
                self._policy_or_raise(policy_id)
            normalized = self._normalize_feedback_payload(feedback_payload)
            return normalized

    def record_user_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = self._normalize_feedback_payload(feedback_payload)
            feedback = UserFeedback.from_dict(normalized)
            self._policy_or_raise(feedback.policy_id)
            self.user_feedback.append(feedback)
            return {
                "status": "recorded",
                "feedback": feedback.to_dict(),
            }

    def optimize_policy_from_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = self._normalize_feedback_payload(feedback_payload)
            feedback = UserFeedback.from_dict(normalized)
            base_policy = self._policy_or_raise(feedback.policy_id)
            self.user_feedback.append(feedback)
            # Feedback can be a full constraint update, not only a preference delta.
            # Always merge explicit fields first; apply the lightweight optimizer only for terse preference feedback.
            requirement = self.policy_generator.merge_requirement_update(base_policy.requirement, feedback.instruction)
            if feedback.target in {
                "latency",
                "cost",
                "security",
                "qos",
                "balance",
                "fragmentation",
                "locality",
                "network",
            } and len(feedback.instruction) < 80:
                requirement = self.policy_generator.apply_feedback(requirement, feedback)
            base_task = self.policy_tasks.get(feedback.policy_id)
            policy, task = self.policy_generator.draft_policy(
                requirement,
                scheduler=self.scheduler,
                nodes=self.nodes.values(),
                current_tick=self.current_tick(),
                execution=None if base_task is None else base_task.execution,
            )
            self.policies[policy.policy_id] = policy
            self.policy_tasks[policy.policy_id] = task
            return {
                "status": "optimized",
                "feedback": feedback.to_dict(),
                "base_policy_id": feedback.policy_id,
                "policy": policy.to_dict(),
            }

    def record_heartbeat(
        self,
        node_id: str,
        *,
        health_score: float | None = None,
        online: bool | None = None,
        reliability_score: float | None = None,
        cost_per_tick: float | None = None,
        region: str | None = None,
        location: str | None = None,
        service_region: str | None = None,
        labels: set[str] | None = None,
        performance_factors: dict[str, float] | None = None,
        network_paths: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            self._expire_stale_nodes()
            node = self.nodes[node_id]
            node.telemetry_tick = self.current_tick()
            node.online = True if online is None else online
            if health_score is not None:
                node.health_score = health_score
            if reliability_score is not None:
                node.reliability_score = reliability_score
            if cost_per_tick is not None:
                node.cost_per_tick = cost_per_tick
            if region is not None:
                node.region = region
            if location is not None:
                node.location = location
            if service_region is not None:
                node.service_region = service_region
            if labels is not None:
                node.labels = set(labels)
            if performance_factors is not None:
                node.performance_factors.update(performance_factors)
            if network_paths is not None:
                for source_region, profile_updates in network_paths.items():
                    profile = node.network_paths.get(str(source_region))
                    if profile is None:
                        profile = NetworkPathProfile()
                        node.network_paths[str(source_region)] = profile
                    for key, value in profile_updates.items():
                        if hasattr(profile, key):
                            setattr(profile, key, float(value))
            self.last_heartbeat_at[node_id] = time.monotonic()
            heartbeat_payload = {
                "node_id": node_id,
                "tick": node.telemetry_tick,
                "running_tasks": sorted(node.running_tasks.keys()),
                "pending_tasks": len(self.pending_queue),
                "online": node.online,
                "network_paths": {
                    region: profile.to_dict()
                    for region, profile in sorted(node.network_paths.items(), key=lambda item: item[0])
                },
            }
            self._persist_node(node)
            if node.online is False:
                self._recover_leases_for_stale_nodes({node_id})
            if self.state_store is not None:
                self.state_store.record_heartbeat(node_id, heartbeat_payload)
            return heartbeat_payload

    def request_lease(self, node_id: str) -> dict[str, Any] | None:
        with self.lock:
            self._expire_stale_nodes()
            node = self.nodes.get(node_id)
            if node is None or not node.online:
                return None

            tick = self.current_tick()
            ordered_task_ids = sorted(
                self.pending_queue,
                key=lambda task_id: self._task_sort_key(self.tasks[task_id]),
            )
            for task_id in ordered_task_ids:
                task = self.tasks[task_id]
                if task.status != TaskStatus.PENDING:
                    continue
                if task.target_node_id and task.target_node_id != node_id:
                    continue
                candidates = [self.nodes[task.target_node_id]] if task.target_node_id and task.target_node_id in self.nodes else list(self.nodes.values())
                decision = self.scheduler.select_node(
                    task,
                    candidates,
                    current_tick=tick,
                    topology_nodes=self.nodes.values(),
                )
                if decision is None or decision.node_id != node_id:
                    continue

                lease = self._activate_task_lease(
                    task=task,
                    node=node,
                    decision=decision,
                    tick=tick,
                    remove_from_pending=True,
                )
                return lease.to_dict()
            return None

    def report_task_progress(
        self,
        *,
        node_id: str,
        task_id: str,
        stage: str,
        status: str = "running",
        progress: float | None = None,
        message: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an in-flight task lifecycle update from a real or simulated agent."""
        with self.lock:
            self._expire_stale_nodes()
            lease = self.leases.get(task_id)
            if lease is None:
                raise ValueError(f"Task {task_id} does not have an active lease.")
            if lease.node_id != node_id:
                raise ValueError(f"Task {task_id} is leased to {lease.node_id}, not {node_id}.")
            tick = self.current_tick()
            payload = {
                "task_id": task_id,
                "node_id": node_id,
                "stage": str(stage),
                "status": str(status),
                "progress": round(clamp(float(progress if progress is not None else 0.0)), 4),
                "message": message or "",
                "metrics": dict(metrics or {}),
                "tick": tick,
                "updated_at": round(time.time(), 3),
            }
            self.task_progress[task_id] = payload
            self.progress_events.append(payload)
            if len(self.progress_events) > 64:
                self.progress_events = self.progress_events[-64:]
            node = self.nodes.get(node_id)
            if node is not None:
                node.telemetry_tick = tick
                self.last_heartbeat_at[node_id] = time.monotonic()
                self._persist_node(node)
            return payload

    def report_task_result(
        self,
        *,
        node_id: str,
        task_id: str,
        success: bool,
        duration_seconds: float,
        stdout: str = "",
        stderr: str = "",
        failure_reason: str | None = None,
        returncode: int | None = None,
        cost: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            self._expire_stale_nodes()
            lease = self.leases.get(task_id)
            if lease is None:
                raise ValueError(f"Task {task_id} does not have an active lease.")
            if lease.node_id != node_id:
                raise ValueError(f"Task {task_id} is leased to {lease.node_id}, not {node_id}.")
            if node_id not in self.nodes:
                raise ValueError(f"Unknown node {node_id}.")
            self.leases.pop(task_id)

            tick = self.current_tick()
            node = self.nodes[node_id]
            node.running_tasks.pop(task_id, None)
            task = self.tasks[task_id]

            actual_duration = max(1, int(ceil(duration_seconds)))
            actual_cost = cost if cost is not None else (actual_duration * node.cost_per_tick)
            within_budget = None if task.budget is None else actual_cost <= task.budget
            deadline_tick = task.effective_deadline_tick()
            sla_met = deadline_tick is None or tick <= deadline_tick
            record = ExecutionRecord(
                task_id=task.task_id,
                task_type=task.task_type,
                node_id=node_id,
                start_tick=lease.issued_tick,
                end_tick=tick,
                predicted_duration=max(1, lease.predicted_finish_tick - lease.issued_tick),
                actual_duration=actual_duration,
                success=success,
                cost=actual_cost,
                sla_met=sla_met,
                within_budget=within_budget,
                retry_count=max(0, task.attempts - 1),
                failure_reason=failure_reason or (None if success else f"returncode_{returncode or -1}"),
                stdout_excerpt=_truncate(stdout),
                stderr_excerpt=_truncate(stderr),
                network_delay_ticks=int(round(lease.decision.network_snapshot.get("transfer_ticks", 0.0))),
                network_risk=float(lease.decision.network_snapshot.get("uncertainty", 0.0)),
                effective_bandwidth_mbps=float(
                    lease.decision.network_snapshot.get("guaranteed_bandwidth_mbps", 0.0)
                ),
                delivery_probability=float(lease.decision.network_snapshot.get("delivery_probability", 1.0)),
                sla_reason=self._sla_reason(
                    task=task,
                    tick=tick,
                    cost=actual_cost,
                    sla_met=sla_met,
                    within_budget=within_budget,
                ),
                metadata=dict(metadata or {}),
            )
            self.execution_history.append(record)
            self.task_progress.pop(task_id, None)
            node.update_after_record(task, record)

            if success:
                task.status = TaskStatus.SUCCEEDED
            elif task.attempts <= task.max_retries:
                task.status = TaskStatus.PENDING
                self.pending_queue.append(task.task_id)
            else:
                task.status = TaskStatus.FAILED

            if self.state_store is not None:
                self.state_store.delete_lease(task_id)
                self.state_store.append_execution_record(record.to_dict())
            self._persist_task(task)
            self._persist_node(node)

            if len(self.execution_history) % self.policy_update_interval == 0:
                previous_adjustment_count = len(self.policy_state.adjustment_history)
                self.optimizer.update_policy(
                    policy_state=self.policy_state,
                    recent_records=self.execution_history,
                    nodes=self.nodes.values(),
                    tick=tick,
                    context=self._feedback_context(),
                )
                if (
                    self.state_store is not None
                    and len(self.policy_state.adjustment_history) > previous_adjustment_count
                ):
                    latest_adjustment = self.policy_state.adjustment_history[-1]
                    self.state_store.append_policy_adjustment(latest_adjustment.to_dict())
                    self.state_store.set_control_value("policy_weights", self.policy_state.current_weights())
            return record.to_dict()

    def cancel_task_run(self, *, task_id: str, requeue: bool = False) -> dict[str, Any]:
        with self.lock:
            self._expire_stale_nodes()
            lease = self.leases.pop(task_id, None)
            released_nodes: list[str] = []
            if lease is not None:
                released_nodes.append(lease.node_id)
            for node in self.nodes.values():
                if task_id not in node.running_tasks:
                    continue
                node.running_tasks.pop(task_id, None)
                if node.node_id not in released_nodes:
                    released_nodes.append(node.node_id)
                self._persist_node(node)
            self.task_progress.pop(task_id, None)
            if self.state_store is not None:
                self.state_store.delete_lease(task_id)

            task = self.tasks.get(task_id)
            if task is None and lease is None and not released_nodes:
                raise ValueError(f"Task {task_id} does not have an active lease or resource allocation.")
            if task is not None and task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.PENDING if requeue else TaskStatus.FAILED
                if requeue and task_id not in self.pending_queue:
                    self.pending_queue.append(task_id)
                self._persist_task(task)

            return {
                "status": "requeued" if requeue else "cancelled",
                "task_id": task_id,
                "node_id": released_nodes[0] if released_nodes else "",
                "released_nodes": released_nodes,
                "released": True,
            }

    def has_work(self) -> bool:
        with self.lock:
            return bool(self.pending_queue or self.leases)

    @staticmethod
    def _sla_reason(
        *,
        task: Task,
        tick: int,
        cost: float,
        sla_met: bool,
        within_budget: bool | None,
    ) -> str:
        reasons: list[str] = []
        deadline_tick = task.effective_deadline_tick()
        if deadline_tick is not None and tick > deadline_tick:
            elapsed = max(0, tick - task.submit_tick)
            allowed = max(0, deadline_tick - task.submit_tick)
            reasons.append(f"提交后耗时 {elapsed} ticks 超过时限 {allowed} ticks")
        if within_budget is False and task.budget is not None:
            reasons.append(f"成本 {round(cost, 4)} 超过预算 {round(task.budget, 4)}")
        if reasons:
            return "；".join(reasons)
        if sla_met:
            return "SLA 达标"
        return "未满足 SLA 目标"

    def build_report(self) -> dict[str, Any]:
        with self.lock:
            self._expire_stale_nodes()
            succeeded = [record for record in self.execution_history if record.success]
            failed = [record for record in self.execution_history if not record.success]
            avg_wait = self._average_wait_time()
            avg_cost = mean(record.cost for record in self.execution_history) if self.execution_history else 0.0
            avg_network_delay = (
                mean(record.network_delay_ticks for record in self.execution_history)
                if self.execution_history
                else 0.0
            )
            avg_network_risk = (
                mean(record.network_risk for record in self.execution_history)
                if self.execution_history
                else 0.0
            )
            stable_latencies = [
                float(decision.network_snapshot.get("stable_latency_ms", decision.network_snapshot.get("robust_latency_ms", 0.0)))
                for decision in self.decision_log
            ]
            fusion_scores = [
                float(decision.network_snapshot.get("feature_fusion_score", 0.0))
                for decision in self.decision_log
            ]
            deterministic_confidences = [
                float(decision.network_snapshot.get("deterministic_confidence", 0.0))
                for decision in self.decision_log
            ]
            model_predictions = [
                decision.network_snapshot.get("model_prediction", {})
                for decision in self.decision_log
                if decision.network_snapshot.get("model_prediction")
            ]
            latest_model_prediction = model_predictions[-1] if model_predictions else {}
            model_runtime = self.scheduler.model_runtime.describe()
            loaded_models = set(model_runtime.get("loaded_models", []))
            active_model_features = []
            if "lstm" in loaded_models:
                active_model_features.append("lstm_latency_prediction")
            if "gnn" in loaded_models:
                active_model_features.append("graphsage_topology_score")
            return {
                "tick": self.current_tick(),
                "totals": {
                    "tasks": len(self.tasks),
                    "completed_attempts": len(self.execution_history),
                    "succeeded_attempts": len(succeeded),
                    "failed_attempts": len(failed),
                    "completed": len(self.execution_history),
                    "succeeded": len(succeeded),
                    "failed": len(failed),
                    "pending_tasks": len(self.pending_queue),
                    "leased_tasks": len(self.leases),
                    "running_tasks": len(self.leases),
                    "pending": len(self.pending_queue),
                    "running": len(self.leases),
                    "sla_met": sum(1 for record in self.execution_history if record.sla_met),
                    "sla_missed": sum(1 for record in self.execution_history if not record.sla_met),
                },
                "metrics": {
                    "success_rate": round((len(succeeded) / len(self.execution_history)) if self.execution_history else 0.0, 4),
                    "average_wait_ticks": round(avg_wait, 4),
                    "average_cost": round(avg_cost, 4),
                    "average_network_delay_ticks": round(avg_network_delay, 4),
                    "average_network_risk": round(avg_network_risk, 4),
                    "average_stable_latency_ms": round(mean(stable_latencies) if stable_latencies else 0.0, 4),
                    "average_fusion_score": round(mean(fusion_scores) if fusion_scores else 0.0, 4),
                    "average_deterministic_confidence": round(
                        mean(deterministic_confidences) if deterministic_confidences else 0.0,
                        4,
                    ),
                    "sla_rate": round(
                        mean(1.0 if record.sla_met else 0.0 for record in self.execution_history)
                        if self.execution_history
                        else 0.0,
                        4,
                    ),
                },
                "policy_weights": {key: round(value, 4) for key, value in self.policy_state.current_weights().items()},
                "policy_history": [entry.to_dict() for entry in self.policy_state.adjustment_history],
                "nodes": [
                    self._node_report_payload(node)
                    for node in self.nodes.values()
                ],
                "physical_topology": None if self.physical_topology is None else self.physical_topology.to_dict(),
                "recent_decisions": [decision.to_dict() for decision in self.decision_log[-8:]],
                "active_runs": self._active_runs_payload(),
                "recent_progress_events": list(self.progress_events[-16:]),
                "recent_records": [record.to_dict() for record in self.execution_history[-8:]],
                "execution_records": [record.to_dict() for record in self.execution_history],
                "task_statuses": {
                    task_id: task.status.value for task_id, task in sorted(self.tasks.items(), key=lambda item: item[0])
                },
                "pending_task_queue": [
                    self.tasks[task_id].to_dict()
                    for task_id in self.pending_queue
                    if task_id in self.tasks and self.tasks[task_id].status == TaskStatus.PENDING
                ],
                "policies": [policy.to_dict() for policy in list(self.policies.values())[-8:]],
                "user_feedback": [feedback.to_dict() for feedback in self.user_feedback[-8:]],
                "model_runtime": {
                    **model_runtime,
                    "latest_prediction": latest_model_prediction,
                },
                "algorithm_profile": {
                    "name": "deterministic_compute_network_policy_engine",
                    "features": [
                        "resource_fit",
                        "deadline_completion",
                        "cost",
                        "reliability",
                        "load_balance",
                        "locality",
                        "jitter",
                        "node_load",
                        "bandwidth_utilization",
                        "security_policy",
                        *active_model_features,
                    ],
                    "model_status": model_runtime["status"],
                    "objective": "以确定性调度评分为主，按实际加载的模型状态增强时延和拓扑稳定性预测。",
                    "paper_adaptations": [
                        "多特征融合",
                        "时延确定化预测",
                        "闭环反馈调权",
                        "安全约束评分",
                    ],
                },
                "data_gaps": {
                    "latency_history": "当前 runtime 使用链路画像合成序列；模型是否参与预测以 model_runtime.status 为准。",
                    "lstm_latency_prediction": (
                        "LSTM 模型已参与 predicted_latency_ms，作为 EWMA 的增强预测器。"
                        if "lstm" in loaded_models
                        else "LSTM 模型未加载，predicted_latency_ms 使用 EWMA fallback。"
                    ),
                    "bandwidth_utilization": "当前由带宽波动与丢包估计，后续需要接入交换机端口或云监控链路利用率。",
                    "security_policy": "安全维度已进入策略对象和调度评分；企业级身份、审计和密钥管理仍需后续接入。",
                    "gnn_topology_embedding": (
                        "GraphSAGE 模型已参与 gnn_topology 评分；当前已按物理链路传播时延加权聚合仿真算力邻居特征。"
                        if "gnn" in loaded_models and self.physical_topology is not None
                        else "GraphSAGE 模型已参与 gnn_topology 评分；未注册物理拓扑时邻居特征使用自嵌入兜底。"
                        if "gnn" in loaded_models
                        else "GraphSAGE 模型未加载，gnn_topology 使用中性兜底分。"
                    ),
                },
            }


    def _node_report_payload(self, node: Node) -> dict[str, Any]:
        payload = {
            **node.to_dict(),
            "last_heartbeat_age": round(time.monotonic() - self.last_heartbeat_at.get(node.node_id, self.started_at), 3),
        }
        runtime_utilization = {"cpu": 0.0, "memory": 0.0, "gpu": 0.0, "storage": 0.0}
        active_task_ids: list[str] = []
        active_stages: list[str] = []
        for progress in self.task_progress.values():
            if progress.get("node_id") != node.node_id:
                continue
            task_id = str(progress.get("task_id") or "")
            if task_id and task_id not in self.leases:
                continue
            active_task_ids.append(task_id)
            active_stages.append(str(progress.get("stage") or "running"))
            util = dict(dict(progress.get("metrics") or {}).get("simulated_utilization") or {})
            for key in runtime_utilization:
                try:
                    runtime_utilization[key] = max(runtime_utilization[key], float(util.get(key, 0.0)))
                except (TypeError, ValueError):
                    pass
        payload["runtime_utilization"] = {key: round(clamp(value), 4) for key, value in runtime_utilization.items()}
        payload["active_task_ids"] = active_task_ids
        payload["active_stages"] = active_stages
        return payload

    def current_tick(self) -> int:
        return int(max(0.0, ceil(time.monotonic() - self.started_at)))

    def _active_runs_payload(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for task_id, lease in sorted(self.leases.items(), key=lambda item: item[0]):
            task = self.tasks.get(task_id)
            progress = dict(self.task_progress.get(task_id, {}))
            if not progress:
                progress = {
                    "task_id": task_id,
                    "node_id": lease.node_id,
                    "stage": "leased",
                    "status": "running",
                    "progress": 0.0,
                    "message": "lease acquired; waiting for agent progress",
                    "metrics": {},
                    "tick": self.current_tick(),
                }
            progress["task"] = None if task is None else task.to_dict()
            progress["lease"] = {
                "issued_tick": lease.issued_tick,
                "predicted_finish_tick": lease.predicted_finish_tick,
                "predicted_cost": round(lease.predicted_cost, 4),
            }
            runs.append(progress)
        return runs

    def _policy_or_raise(self, policy_id: str) -> ComputeNetworkPolicy:
        policy = self.policies.get(policy_id)
        if policy is None:
            raise ValueError(f"Unknown policy {policy_id}.")
        return policy

    def _session_or_raise(self, session_id: str) -> RequirementSession:
        session = self.requirement_sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown requirement session {session_id}.")
        return session

    def _normalize_feedback_payload(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        policy_id = str(feedback_payload.get("policy_id", ""))
        instruction = str(feedback_payload.get("instruction", ""))
        if not policy_id:
            raise ValueError("feedback policy_id is required")
        return parse_feedback_instruction(
            policy_id=policy_id,
            instruction=instruction,
            target=feedback_payload.get("target"),
            sentiment=feedback_payload.get("sentiment"),
            preference_delta=feedback_payload.get("preference_delta"),
        )

    def _new_session_id(self) -> str:
        return f"sess_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}"

    def _expire_stale_nodes(self) -> None:
        now = time.monotonic()
        stale_node_ids: set[str] = set()
        for node_id, node in self.nodes.items():
            if self._is_cloudsim_snapshot_node(node):
                if not node.online:
                    node.online = True
                    self.last_heartbeat_at[node_id] = now
                    self._persist_node(node)
                continue
            last_seen = self.last_heartbeat_at.get(node_id, self.started_at)
            if now - last_seen > self.heartbeat_timeout_seconds:
                stale_node_ids.add(node_id)
                if node.online:
                    node.online = False
                    self._persist_node(node)
        if stale_node_ids:
            self._recover_leases_for_stale_nodes(stale_node_ids)

    @staticmethod
    def _is_cloudsim_snapshot_node(node: Node) -> bool:
        labels = {str(label).lower() for label in node.labels}
        return "cloudsim" in labels or "cloudsimplus" in labels

    def _recover_leases_for_stale_nodes(self, stale_node_ids: set[str]) -> None:
        """Release leases held by offline agents so tasks can be retried elsewhere."""
        for task_id, lease in list(self.leases.items()):
            if lease.node_id not in stale_node_ids:
                continue
            self.leases.pop(task_id, None)
            node = self.nodes.get(lease.node_id)
            if node is not None:
                node.running_tasks.pop(task_id, None)
                self._persist_node(node)
            if self.state_store is not None:
                self.state_store.delete_lease(task_id)

            task = self.tasks.get(task_id)
            if task is None or task.status != TaskStatus.RUNNING:
                continue
            if task.attempts <= task.max_retries:
                task.status = TaskStatus.PENDING
                if task_id not in self.pending_queue:
                    self.pending_queue.append(task_id)
            else:
                task.status = TaskStatus.FAILED
            self._persist_task(task)

    def _feedback_context(self) -> dict[str, float]:
        gpu_pending = [
            self.tasks[task_id]
            for task_id in self.pending_queue
            if self.tasks[task_id].demand.gpu > 0
        ]
        locality_records = [record for record in self.execution_history if self.tasks[record.task_id].data_region]
        locality_miss_rate = 0.0
        if locality_records:
            locality_miss_rate = mean(
                1.0
                if self.tasks[record.task_id].data_region != self.nodes[record.node_id].region
                else 0.0
                for record in locality_records[-12:]
            )
        recent_records = self.execution_history[-12:]
        return {
            "gpu_wait_ratio": (len(gpu_pending) / len(self.pending_queue)) if self.pending_queue else 0.0,
            "locality_miss_rate": locality_miss_rate,
            "network_instability": (
                mean(record.network_risk for record in recent_records)
                if recent_records
                else 0.0
            ),
            "network_pressure": (
                mean(
                    min(1.0, record.network_delay_ticks / max(1, record.actual_duration))
                    for record in recent_records
                )
                if recent_records
                else 0.0
            ),
        }

    def _average_wait_time(self) -> float:
        wait_times = []
        first_start: dict[str, int] = {}
        for record in self.execution_history:
            if record.task_id not in first_start:
                first_start[record.task_id] = record.start_tick
        for task_id, start_tick in first_start.items():
            wait_times.append(start_tick - self.tasks[task_id].submit_tick)
        return mean(wait_times) if wait_times else 0.0

    def _task_sort_key(self, task: Task) -> tuple[float, int, int, str]:
        deadline_sort = task.deadline if task.deadline is not None else 10**9
        return (-task.priority, deadline_sort, task.submit_tick, task.task_id)

    def _activate_task_lease(
        self,
        *,
        task: Task,
        node: Node,
        decision: SchedulingDecision,
        tick: int,
        remove_from_pending: bool,
    ) -> TaskLease:
        predicted_duration = max(1, decision.predicted_finish_tick - tick)
        network_delay_ticks = int(round(decision.network_snapshot.get("transfer_ticks", 0.0)))
        node.running_tasks[task.task_id] = RunningTask(
            task_id=task.task_id,
            node_id=node.node_id,
            allocation=task.demand,
            start_tick=tick,
            predicted_duration=predicted_duration,
            actual_duration=0,
            finish_tick=decision.predicted_finish_tick,
            success_probability=1.0,
            network_delay_ticks=network_delay_ticks,
            network_risk=float(decision.network_snapshot.get("uncertainty", 0.0)),
            effective_bandwidth_mbps=float(
                decision.network_snapshot.get("guaranteed_bandwidth_mbps", 0.0)
            ),
            delivery_probability=float(decision.network_snapshot.get("delivery_probability", 1.0)),
        )
        task.status = TaskStatus.RUNNING
        task.last_scheduled_node = node.node_id
        task.attempts += 1
        if remove_from_pending and task.task_id in self.pending_queue:
            self.pending_queue.remove(task.task_id)
        self.decision_log.append(decision)

        lease = TaskLease(
            task_id=task.task_id,
            node_id=node.node_id,
            issued_tick=tick,
            predicted_finish_tick=decision.predicted_finish_tick,
            predicted_cost=decision.predicted_cost,
            explanation=decision.explanation,
            task=task,
            decision=decision,
        )
        self.leases[task.task_id] = lease
        self._persist_task(task)
        self._persist_node(node)
        if self.state_store is not None:
            self.state_store.append_decision(decision.to_dict())
            self.state_store.save_lease(lease.to_dict())
        return lease

    def _persist_node(self, node: Node) -> None:
        if self.state_store is None:
            return
        last_seen = self.last_heartbeat_at.get(node.node_id, time.monotonic())
        self.state_store.save_node(node.to_dict(), last_seen)

    def _persist_task(self, task: Task) -> None:
        if self.state_store is None:
            return
        self.state_store.save_task(task.to_dict())

    def _restore_from_store(self) -> None:
        if self.state_store is None:
            return
        snapshot = self.state_store.load_state()

        restored_weights = snapshot["control_state"].get("policy_weights")
        if restored_weights:
            self.policy_state.weights = restored_weights

        restored_topology = snapshot["control_state"].get("physical_topology")
        if restored_topology:
            self.physical_topology = PhysicalTopology.from_dict(restored_topology)
            self.scheduler.set_physical_topology(self.physical_topology)

        self.policy_state.adjustment_history = [
            PolicyAdjustment(
                tick=int(payload["tick"]),
                weights={str(key): float(value) for key, value in payload["weights"].items()},
                reasons=list(payload["reasons"]),
                affected_records=int(payload.get("affected_records", 0)),
                metrics={str(key): float(value) for key, value in dict(payload.get("metrics") or {}).items()},
            )
            for payload in snapshot["policy_adjustments"]
        ]

        for node_entry in snapshot["nodes"]:
            node = node_from_dict(node_entry["payload"])
            node.running_tasks = {}
            self.nodes[node.node_id] = node
            self.last_heartbeat_at[node.node_id] = float(node_entry["last_heartbeat_at"])

        for payload in snapshot["tasks"]:
            task = task_from_dict(payload)
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.PENDING
            self.tasks[task.task_id] = task
            if task.status == TaskStatus.PENDING and task.task_id not in self.pending_queue:
                self.pending_queue.append(task.task_id)

        self.decision_log = [
            SchedulingDecision(**payload)
            for payload in snapshot["decisions"]
        ]
        self.execution_history = [
            ExecutionRecord(**payload)
            for payload in snapshot["execution_records"]
        ]

        for lease_payload in snapshot["leases"]:
            task_id = lease_payload["task_id"]
            if task_id in self.tasks and self.tasks[task_id].status != TaskStatus.SUCCEEDED:
                self.tasks[task_id].status = TaskStatus.PENDING
                if task_id not in self.pending_queue:
                    self.pending_queue.append(task_id)
            self.state_store.delete_lease(task_id)

        for task in self.tasks.values():
            self._persist_task(task)
        for node in self.nodes.values():
            self._persist_node(node)
