from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .common import clamp
from .execution import TaskExecutionSpec
from .resource import ResourceVector


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(slots=True)
class Task:
    task_id: str
    task_type: str
    demand: ResourceVector
    estimated_duration: int
    priority: int = 5
    budget: float | None = None
    deadline: int | None = None
    data_region: str | None = None
    source_region: str | None = None
    input_size_gb: float | None = None
    max_latency_ms: float | None = None
    min_bandwidth_mbps: float | None = None
    network_sensitivity: float = 0.5
    intent_weights: dict[str, float] = field(default_factory=dict)
    preferred_labels: set[str] = field(default_factory=set)
    security_level: str = "medium"
    isolation_level: str = "process"
    allowed_regions: set[str] = field(default_factory=set)
    forbidden_nodes: set[str] = field(default_factory=set)
    require_encrypted_transport: bool = True
    max_retries: int = 1
    execution: TaskExecutionSpec | None = None
    submit_tick: int = 0
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    last_scheduled_node: str | None = None
    target_node_id: str | None = None

    def effective_deadline_tick(self) -> int | None:
        if self.deadline is None:
            return None
        if self.submit_tick > 0 and self.deadline <= self.submit_tick:
            return self.submit_tick + self.deadline
        return self.deadline

    def urgency_score(self, current_tick: int) -> float:
        base = clamp(self.priority / 10.0)
        deadline_tick = self.effective_deadline_tick()
        if deadline_tick is None:
            return base
        slack = deadline_tick - current_tick - self.estimated_duration
        if slack <= 0:
            return 1.0
        return clamp(max(base, 1.0 - (slack / max(2.0, self.estimated_duration * 4.0))))

    def network_source(self) -> str | None:
        return self.source_region or self.data_region

    def estimated_input_size_gb(self) -> float:
        if self.input_size_gb is not None:
            return max(0.1, float(self.input_size_gb))
        inferred = max(0.2, min((self.demand.storage * 0.04) + (self.demand.memory * 0.01), 12.0))
        return round(inferred, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "demand": self.demand.to_dict(),
            "estimated_duration": self.estimated_duration,
            "priority": self.priority,
            "budget": self.budget,
            "deadline": self.deadline,
            "data_region": self.data_region,
            "source_region": self.source_region,
            "input_size_gb": self.input_size_gb,
            "max_latency_ms": self.max_latency_ms,
            "min_bandwidth_mbps": self.min_bandwidth_mbps,
            "network_sensitivity": self.network_sensitivity,
            "intent_weights": dict(self.intent_weights),
            "preferred_labels": sorted(self.preferred_labels),
            "security_level": self.security_level,
            "isolation_level": self.isolation_level,
            "allowed_regions": sorted(self.allowed_regions),
            "forbidden_nodes": sorted(self.forbidden_nodes),
            "require_encrypted_transport": self.require_encrypted_transport,
            "max_retries": self.max_retries,
            "submit_tick": self.submit_tick,
            "status": self.status.value,
            "attempts": self.attempts,
            "last_scheduled_node": self.last_scheduled_node,
            "target_node_id": self.target_node_id,
            "execution": None if self.execution is None else self.execution.to_dict(),
        }


@dataclass(slots=True)
class RunningTask:
    task_id: str
    node_id: str
    allocation: ResourceVector
    start_tick: int
    predicted_duration: int
    actual_duration: int
    finish_tick: int
    success_probability: float
    network_delay_ticks: int = 0
    network_risk: float = 0.0
    effective_bandwidth_mbps: float = 0.0
    delivery_probability: float = 1.0
