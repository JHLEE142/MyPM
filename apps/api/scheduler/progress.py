from __future__ import annotations

from datetime import date
from typing import Any, Iterable


def weighted_progress(tasks: Iterable[Any]) -> float:
    total = 0.0
    completed = 0.0
    for task in tasks:
        hours = float(_value(task, "estimated_hours", 0) or 0)
        percent = min(100.0, max(0.0, float(_value(task, "progress_percent", 0) or 0)))
        total += hours
        completed += hours * percent / 100
    return round(completed / total * 100, 2) if total else 0.0


def planned_progress(placements: Iterable[dict[str, Any]], total_hours: float, as_of: date) -> float:
    if total_hours <= 0:
        return 0.0
    planned = sum(float(item["hours"]) for item in placements if date.fromisoformat(str(item["date"])) <= as_of)
    return round(min(100.0, planned / total_hours * 100), 2)


def pace_ratio(actual_percent: float, planned_percent: float) -> float | None:
    if planned_percent <= 0:
        return None
    return round(actual_percent / planned_percent, 4)


def pace_status(
    ratio: float | None,
    delay_days: int = 0,
    *,
    critical_milestone_failed: bool = False,
    dependency_blocked: bool = False,
) -> str:
    if critical_milestone_failed or dependency_blocked:
        return "critical"
    if ratio is not None and ratio < 0.75 or delay_days >= 4:
        return "risk"
    if ratio is not None and ratio < 0.9 or 1 <= delay_days <= 3:
        return "warning"
    return "normal"


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
