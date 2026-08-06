from __future__ import annotations

from datetime import date, timedelta
from math import ceil


def effective_daily_capacity(daily_capacity_hours: float, buffer_ratio: float) -> float:
    if daily_capacity_hours < 0 or not 0 <= buffer_ratio < 1:
        raise ValueError("capacity must be non-negative and buffer_ratio must be in [0, 1)")
    return round(daily_capacity_hours * (1 - buffer_ratio), 6)


def is_working_day(day: date, work_days: list[int] | set[int], excluded_dates: list[date] | set[date]) -> bool:
    return day.weekday() in set(work_days) and day not in set(excluded_dates)


def working_days_between(
    start: date, end: date, work_days: list[int] | set[int], excluded_dates: list[date] | set[date]
) -> list[date]:
    if end < start:
        return []
    result: list[date] = []
    cursor = start
    while cursor <= end:
        if is_working_day(cursor, work_days, excluded_dates):
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def next_working_day(day: date, work_days: list[int] | set[int], excluded_dates: list[date] | set[date], *, include: bool = True) -> date:
    cursor = day if include else day + timedelta(days=1)
    for _ in range(3700):
        if is_working_day(cursor, work_days, excluded_dates):
            return cursor
        cursor += timedelta(days=1)
    raise ValueError("no working day found within ten years")


def add_working_days(day: date, count: int, work_days: list[int] | set[int], excluded_dates: list[date] | set[date]) -> date:
    if count <= 0:
        return day
    cursor = day
    remaining = count
    while remaining:
        cursor = next_working_day(cursor, work_days, excluded_dates, include=False)
        remaining -= 1
    return cursor


def workdays_needed(hours: float, daily_hours: float) -> int:
    if daily_hours <= 0:
        raise ValueError("daily_hours must be positive")
    return max(0, ceil(hours / daily_hours))


def capacity_warning(assigned_hours: float, daily_capacity_hours: float) -> dict[str, float | bool | str]:
    excess = max(0.0, assigned_hours - daily_capacity_hours)
    return {
        "over_capacity": excess > 1e-9,
        "excess_hours": round(excess, 2),
        "message": f"가용시간보다 {excess:.2f}시간 초과 배정되었습니다" if excess else "가용시간 내 배정",
    }
