from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import Mapping

from .capacity import add_working_days, is_working_day


INSUFFICIENT_MESSAGE = "예측 데이터 부족 — 최소 3개 작업일의 데이터가 필요합니다"


def forecast_completion(
    remaining_hours: float,
    work_logs: Mapping[date, float],
    today: date,
    work_days: list[int] | set[int],
    excluded_dates: list[date] | set[date] = frozenset(),
) -> dict[str, object]:
    eligible: list[date] = []
    cursor = today
    searched = 0
    while len(eligible) < 7 and searched < 3700:
        if is_working_day(cursor, work_days, excluded_dates):
            eligible.append(cursor)
        cursor -= timedelta(days=1)
        searched += 1
    samples = [(day, float(work_logs.get(day, 0))) for day in eligible if float(work_logs.get(day, 0)) > 0]
    if len(samples) < 3:
        return {
            "status": "insufficient_data",
            "message": INSUFFICIENT_MESSAGE,
            "work_days_observed": len(samples),
            "velocity_hours_per_day": None,
            "estimated_completion_date": None,
        }
    velocity = sum(hours for _, hours in samples) / len(samples)
    remaining_days = ceil(max(0.0, remaining_hours) / velocity) if velocity else 0
    completion = add_working_days(today, remaining_days, work_days, excluded_dates)
    return {
        "status": "ok",
        "message": None,
        "work_days_observed": len(samples),
        "velocity_hours_per_day": round(velocity, 2),
        "remaining_work_days": remaining_days,
        "estimated_completion_date": completion.isoformat(),
    }


def required_daily_velocity(remaining_hours: float, today: date, target_date: date, work_days: list[int], excluded_dates: set[date]) -> float | None:
    count = 0
    cursor = today
    while cursor <= target_date:
        if is_working_day(cursor, work_days, excluded_dates):
            count += 1
        cursor += timedelta(days=1)
    return round(remaining_hours / count, 2) if count else None
