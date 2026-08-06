from datetime import date

from scheduler.forecast import forecast_completion
from scheduler.progress import pace_status, weighted_progress


def test_weighted_progress_example_is_30_percent():
    tasks = [
        {"estimated_hours": 2, "progress_percent": 100},
        {"estimated_hours": 8, "progress_percent": 50},
        {"estimated_hours": 10, "progress_percent": 0},
    ]
    assert weighted_progress(tasks) == 30.0


def test_pace_status_boundaries():
    assert pace_status(0.9) == "normal"
    assert pace_status(0.8999) == "warning"
    assert pace_status(0.75) == "warning"
    assert pace_status(0.7499) == "risk"
    assert pace_status(1.0, delay_days=3) == "warning"
    assert pace_status(1.0, delay_days=4) == "risk"
    assert pace_status(1.0, dependency_blocked=True) == "critical"


def test_forecast_requires_three_work_days():
    result = forecast_completion(
        remaining_hours=12,
        work_logs={date(2026, 8, 5): 4, date(2026, 8, 6): 4},
        today=date(2026, 8, 6),
        work_days=[0, 1, 2, 3, 4],
    )
    assert result["status"] == "insufficient_data"
    assert result["estimated_completion_date"] is None


def test_forecast_uses_recent_velocity_and_working_days():
    result = forecast_completion(
        remaining_hours=8,
        work_logs={date(2026, 8, 4): 4, date(2026, 8, 5): 4, date(2026, 8, 6): 4},
        today=date(2026, 8, 6),
        work_days=[0, 1, 2, 3, 4],
    )
    assert result["status"] == "ok"
    assert result["velocity_hours_per_day"] == 4
    assert result["remaining_work_days"] == 2
    assert result["estimated_completion_date"] == "2026-08-10"
