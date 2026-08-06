from datetime import date

import pytest

from scheduler.capacity import capacity_warning, effective_daily_capacity
from scheduler.dependencies import DependencyCycleError, assert_acyclic
from scheduler.engine import schedule_tasks


BASE = {
    "start_date": date(2026, 8, 3),
    "target_date": date(2026, 8, 14),
    "work_days": [0, 1, 2, 3, 4],
    "daily_capacity_hours": 4,
    "buffer_ratio": 0.2,
}


def test_schedule_is_deterministic_and_applies_buffer_and_split():
    tasks = [
        {"id": 2, "title": "B", "priority": "medium", "estimated_hours": 2, "dependency_ids": [1]},
        {"id": 1, "title": "A", "priority": "high", "estimated_hours": 4, "dependency_ids": []},
    ]
    first = schedule_tasks(tasks, **BASE)
    second = schedule_tasks(tasks, **BASE)
    assert first == second
    assert effective_daily_capacity(4, 0.2) == 3.2
    a = [item for item in first["placements"] if item["task_id"] == 1]
    b = [item for item in first["placements"] if item["task_id"] == 2]
    assert [item["hours"] for item in a] == [3.2, 0.8]
    assert min(item["date"] for item in b) > max(item["date"] for item in a)


def test_infeasible_is_explicit_and_capacity_warning_works():
    result = schedule_tasks(
        [{"id": 1, "priority": "high", "estimated_hours": 20, "dependency_ids": []}],
        start_date=date(2026, 8, 3),
        target_date=date(2026, 8, 4),
        work_days=[0, 1, 2, 3, 4],
        daily_capacity_hours=4,
        buffer_ratio=0.2,
    )
    assert result["infeasible"] is True
    assert "현재 조건으로 목표일 준수 불가" in result["warnings"]
    assert capacity_warning(4.5, 4)["over_capacity"] is True
    assert capacity_warning(4.5, 4)["excess_hours"] == 0.5


def test_dependency_cycle_detection():
    with pytest.raises(DependencyCycleError):
        assert_acyclic([1, 2], [(1, 2), (2, 1)])
