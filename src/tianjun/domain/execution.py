from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionMode(str, Enum):
    NOOP = "noop"
    PROCESS = "process"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    SIMULATION = "simulation"


@dataclass(slots=True)
class TaskExecutionSpec:
    mode: ExecutionMode = ExecutionMode.NOOP
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    workdir: str | None = None
    timeout_seconds: int | None = None
    shell: bool = False
    image: str | None = None
    volumes: list[str] = field(default_factory=list)
    namespace: str = "default"
    job_name_prefix: str = "sched-agent"
    cleanup: bool = True
    image_pull_policy: str | None = None
    service_account_name: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    # Free-form, config-driven execution payload used by SimulationExecutor.
    # It intentionally stays generic so real backends and simulated backends can share
    # the same TaskExecutionSpec without hard-coded cloud/vendor assumptions.
    simulation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "command": list(self.command),
            "env": dict(self.env),
            "workdir": self.workdir,
            "timeout_seconds": self.timeout_seconds,
            "shell": self.shell,
            "image": self.image,
            "volumes": list(self.volumes),
            "namespace": self.namespace,
            "job_name_prefix": self.job_name_prefix,
            "cleanup": self.cleanup,
            "image_pull_policy": self.image_pull_policy,
            "service_account_name": self.service_account_name,
            "labels": dict(self.labels),
            "simulation": dict(self.simulation),
        }


@dataclass(slots=True)
class ExecutionRecord:
    task_id: str
    task_type: str
    node_id: str
    start_tick: int
    end_tick: int
    predicted_duration: int
    actual_duration: int
    success: bool
    cost: float
    sla_met: bool
    within_budget: bool | None
    retry_count: int
    failure_reason: str | None = None
    stdout_excerpt: str | None = None
    stderr_excerpt: str | None = None
    network_delay_ticks: int = 0
    network_risk: float = 0.0
    effective_bandwidth_mbps: float = 0.0
    delivery_probability: float = 1.0
    sla_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "node_id": self.node_id,
            "start_tick": self.start_tick,
            "end_tick": self.end_tick,
            "predicted_duration": self.predicted_duration,
            "actual_duration": self.actual_duration,
            "success": self.success,
            "cost": round(self.cost, 4),
            "sla_met": self.sla_met,
            "within_budget": self.within_budget,
            "retry_count": self.retry_count,
            "failure_reason": self.failure_reason,
            "stdout_excerpt": self.stdout_excerpt,
            "stderr_excerpt": self.stderr_excerpt,
            "network_delay_ticks": self.network_delay_ticks,
            "network_risk": round(self.network_risk, 4),
            "effective_bandwidth_mbps": round(self.effective_bandwidth_mbps, 4),
            "delivery_probability": round(self.delivery_probability, 4),
            "sla_reason": self.sla_reason,
            "metadata": dict(self.metadata),
        }
