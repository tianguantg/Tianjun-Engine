from __future__ import annotations

from statistics import mean, pstdev
from typing import Iterable

from ..domain import ExecutionRecord, Node, PolicyState, normalize_weights


class PolicyOptimizer:
    def __init__(self, history_window: int = 12, adjustment_step: float = 0.08) -> None:
        self.history_window = history_window
        self.adjustment_step = adjustment_step

    def update_policy(
        self,
        policy_state: PolicyState,
        recent_records: Iterable[ExecutionRecord],
        nodes: Iterable[Node],
        tick: int,
        context: dict[str, float] | None = None,
    ) -> list[str]:
        records = list(recent_records)[-self.history_window :]
        nodes = list(nodes)
        if not records:
            return []

        target = dict(policy_state.current_weights())
        reasons: list[str] = []

        sla_rate = mean(1.0 if record.sla_met else 0.0 for record in records)
        failure_rate = mean(1.0 if not record.success else 0.0 for record in records)

        budget_records = [record for record in records if record.within_budget is not None]
        budget_violation_rate = (
            mean(1.0 if not record.within_budget else 0.0 for record in budget_records)
            if budget_records
            else 0.0
        )

        utilizations = [node.dominant_utilization() for node in nodes if node.online]
        imbalance = pstdev(utilizations) if len(utilizations) > 1 else 0.0

        if sla_rate < 0.85:
            target["performance"] += self.adjustment_step
            target["completion"] += self.adjustment_step
            target["reliability"] += self.adjustment_step / 2.0
            reasons.append("SLA fulfillment dropped, so completion, performance and reliability weights were increased.")

        if failure_rate > 0.12:
            target["reliability"] += self.adjustment_step
            reasons.append("Failure rate increased, so reliability weight was increased.")

        if budget_violation_rate > 0.20:
            target["cost"] += self.adjustment_step
            reasons.append("Budget overruns increased, so cost weight was increased.")

        if imbalance > 0.18:
            target["balance"] += self.adjustment_step
            reasons.append("Cluster load became imbalanced, so balance weight was increased.")

        if context is not None:
            if context.get("gpu_wait_ratio", 0.0) > 0.25:
                target["fragmentation"] += self.adjustment_step / 2.0
                reasons.append("GPU waiting pressure increased, so fragmentation weight was increased.")
            if context.get("locality_miss_rate", 0.0) > 0.20:
                target["locality"] += self.adjustment_step / 2.0
                reasons.append("Cross-region scheduling increased, so locality weight was increased.")
            if context.get("network_instability", 0.0) > 0.33:
                target["network"] += self.adjustment_step
                target["reliability"] += self.adjustment_step / 2.0
                reasons.append("Network uncertainty increased, so network and reliability weights were increased.")
            if context.get("network_pressure", 0.0) > 0.25:
                target["network"] += self.adjustment_step / 2.0
                target["performance"] += self.adjustment_step / 3.0
                target["completion"] += self.adjustment_step / 3.0
                reasons.append("Network delay pressure increased, so network, completion and performance weights were increased.")

        if not reasons:
            return []

        target = normalize_weights(target)
        smoothed = {}
        for key, current_value in policy_state.current_weights().items():
            smoothed[key] = (
                (1.0 - policy_state.learning_rate) * current_value
                + (policy_state.learning_rate * target[key])
            )
        policy_state.update(
            tick=tick,
            new_weights=smoothed,
            reasons=reasons,
            affected_records=len(records),
            metrics={
                "sla_rate": sla_rate,
                "failure_rate": failure_rate,
                "budget_violation_rate": budget_violation_rate,
                "load_imbalance": imbalance,
            },
        )
        return reasons
