from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import METRIC_KEYS, normalize_weights


@dataclass(slots=True)
class PolicyAdjustment:
    tick: int
    weights: dict[str, float]
    reasons: list[str]
    affected_records: int = 0
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "weights": {key: round(value, 4) for key, value in self.weights.items()},
            "reasons": list(self.reasons),
            "affected_records": self.affected_records,
            "metrics": {key: round(value, 4) for key, value in self.metrics.items()},
        }


@dataclass(slots=True)
class PolicyState:
    weights: dict[str, float] = field(
        default_factory=lambda: normalize_weights(
            {
                "performance": 0.20,
                "completion": 0.16,
                "cost": 0.12,
                "reliability": 0.16,
                "balance": 0.14,
                "fragmentation": 0.07,
                "locality": 0.06,
                "network": 0.09,
                "security": 0.08,
            }
        )
    )
    learning_rate: float = 0.28
    adjustment_history: list[PolicyAdjustment] = field(default_factory=list)

    def current_weights(self) -> dict[str, float]:
        complete_weights = {key: 0.0 for key in METRIC_KEYS}
        complete_weights.update(self.weights)
        return normalize_weights(complete_weights)

    def update(
        self,
        tick: int,
        new_weights: dict[str, float],
        reasons: list[str],
        *,
        affected_records: int = 0,
        metrics: dict[str, float] | None = None,
    ) -> None:
        self.weights = normalize_weights(new_weights)
        self.adjustment_history.append(
            PolicyAdjustment(
                tick=tick,
                weights=self.current_weights(),
                reasons=list(reasons),
                affected_records=affected_records,
                metrics=dict(metrics or {}),
            )
        )
