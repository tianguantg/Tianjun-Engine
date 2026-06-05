from __future__ import annotations

import time

from tianjun.application.control_plane import CentralControlPlane
from tianjun.domain import ResourceVector, Task


def test_relative_deadline_is_anchored_at_submit_tick() -> None:
    control_plane = CentralControlPlane()
    control_plane.started_at = time.monotonic() - 2500
    task = Task(
        task_id="deadline-relative",
        task_type="batch",
        demand=ResourceVector(cpu=1),
        estimated_duration=10,
        deadline=100,
    )

    payload = control_plane.submit_task(task)

    assert payload["deadline"] - payload["submit_tick"] == 100
    assert control_plane.tasks["deadline-relative"].effective_deadline_tick() == payload["deadline"]


def test_sla_reason_uses_elapsed_ticks_not_system_clock() -> None:
    task = Task(
        task_id="sla-elapsed",
        task_type="batch",
        demand=ResourceVector(cpu=1),
        estimated_duration=10,
        deadline=2600,
        submit_tick=2500,
    )

    reason = CentralControlPlane._sla_reason(
        task=task,
        tick=2601,
        cost=1.0,
        sla_met=False,
        within_budget=True,
    )

    assert "提交后耗时 101 ticks" in reason
    assert "时限 100 ticks" in reason
