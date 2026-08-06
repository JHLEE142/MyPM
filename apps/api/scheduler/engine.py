from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from .capacity import effective_daily_capacity, is_working_day, next_working_day
from .dependencies import topological_sort


PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _get(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


class ScheduleEngine:
    def generate(
        self,
        tasks: Iterable[Any],
        *,
        start_date: date,
        target_date: date,
        work_days: list[int],
        daily_capacity_hours: float,
        buffer_ratio: float,
        excluded_dates: Iterable[date] = (),
        reserved_placements: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        task_list = list(tasks)
        excluded = set(excluded_dates)
        capacity = effective_daily_capacity(daily_capacity_hours, buffer_ratio)
        by_id = {int(_get(task, "id")): task for task in task_list}
        edges: list[tuple[int, int]] = []
        dependencies_by_task: dict[int, list[int]] = {}
        for task in task_list:
            task_id = int(_get(task, "id"))
            dep_ids = _get(task, "dependency_ids")
            if dep_ids is None:
                dep_ids = [int(_get(dep, "depends_on_task_id")) for dep in _get(task, "dependencies", [])]
            dependencies_by_task[task_id] = sorted({int(dep_id) for dep_id in dep_ids})
            edges.extend((task_id, dep_id) for dep_id in dependencies_by_task[task_id] if dep_id in by_id)

        def order_key(task_id: int) -> tuple[Any, ...]:
            task = by_id[task_id]
            due = _get(task, "due_date")
            return (
                0 if due else 1,
                due or date.max,
                PRIORITY_ORDER.get(str(_get(task, "priority", "medium")), 2),
                task_id,
            )

        ordered_ids = topological_sort(by_id, edges, order_key)
        usage: dict[date, float] = defaultdict(float)
        placements: list[dict[str, Any]] = []
        task_end: dict[int, date] = {}
        for reserved in reserved_placements:
            day = reserved["date"]
            if isinstance(day, str):
                day = date.fromisoformat(day)
            hours = float(reserved["hours"])
            usage[day] += hours
            item = {**reserved, "date": day.isoformat(), "hours": round(hours, 6), "protected": True}
            placements.append(item)
            task_end[int(reserved["task_id"])] = max(day, task_end.get(int(reserved["task_id"]), day))

        unscheduled: list[dict[str, Any]] = []
        for task_id in ordered_ids:
            task = by_id[task_id]
            hours_left = float(_get(task, "estimated_hours", 0) or 0)
            deps = dependencies_by_task.get(task_id, [])
            earliest = start_date
            if deps:
                dependency_ends = [task_end[dep] for dep in deps if dep in task_end]
                if len(dependency_ends) != len(deps):
                    unscheduled.append({"task_id": task_id, "remaining_hours": hours_left, "reason": "dependency_unscheduled"})
                    continue
                earliest = max(dependency_ends) + timedelta(days=1)
            cursor = next_working_day(max(start_date, earliest), work_days, excluded)
            due = _get(task, "due_date")
            limit = min(target_date, due) if due else target_date
            task_placements: list[dict[str, Any]] = []
            while hours_left > 1e-9 and cursor <= limit:
                available = max(0.0, capacity - usage[cursor])
                if available > 1e-9:
                    assigned = min(hours_left, available)
                    task_placements.append(
                        {"task_id": task_id, "date": cursor.isoformat(), "hours": round(assigned, 6), "protected": False}
                    )
                    usage[cursor] += assigned
                    hours_left -= assigned
                cursor = next_working_day(cursor, work_days, excluded, include=False)
            placements.extend(task_placements)
            if task_placements:
                task_end[task_id] = date.fromisoformat(task_placements[-1]["date"])
            if hours_left > 1e-9:
                unscheduled.append(
                    {
                        "task_id": task_id,
                        "remaining_hours": round(hours_left, 6),
                        "reason": "deadline_capacity_exceeded",
                    }
                )

        placements.sort(key=lambda item: (item["date"], int(item["task_id"]), bool(item.get("protected", False))))
        daily_loads = [
            {"date": day.isoformat(), "assigned_hours": round(hours, 6), "effective_capacity_hours": capacity}
            for day, hours in sorted(usage.items())
        ]
        warnings: list[str] = []
        if unscheduled:
            warnings.append("현재 조건으로 목표일 준수 불가")
        if any(item["assigned_hours"] > capacity + 1e-9 for item in daily_loads):
            warnings.append("하루 가용시간 초과")
        return {
            "placements": placements,
            "daily_loads": daily_loads,
            "effective_daily_capacity_hours": capacity,
            "buffer_hours": round(daily_capacity_hours - capacity, 6),
            "infeasible": bool(unscheduled),
            "unscheduled": unscheduled,
            "warnings": warnings,
        }


def schedule_tasks(tasks: Iterable[Any], **kwargs: Any) -> dict[str, Any]:
    return ScheduleEngine().generate(tasks, **kwargs)
