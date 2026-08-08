"""일일 보고 초안 생성 테스트."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from app.reports import (  # noqa: E402
    build_daily_report,
    month_bounds,
    previous_workday,
    render_report_text,
    week_bounds,
)


def create_project(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_previous_workday_skips_weekend():
    # 2026-08-10은 월요일 → 직전 근무일은 8월 7일 금요일
    assert previous_workday(date(2026, 8, 10), {0, 1, 2, 3, 4}) == date(2026, 8, 7)
    # 근무일 제한이 없으면 그냥 전날
    assert previous_workday(date(2026, 8, 10), set()) == date(2026, 8, 9)


def test_week_and_month_bounds():
    assert week_bounds(date(2026, 8, 6)) == (date(2026, 8, 3), date(2026, 8, 9))
    assert month_bounds(date(2026, 8, 6)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert month_bounds(date(2026, 12, 15)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_report_has_all_sections_and_lists_active_projects(client, project_payload, db_session):
    project = create_project(client, project_payload)
    client.post(f"/api/projects/{project['id']}/tasks", json={"title": "설계", "estimated_hours": 4})
    report = build_daily_report(db_session, date(2026, 8, 8))
    assert list(report["sections"]) == ["전일", "금일", "주간 목표", "월간 목표"]
    for lines in report["sections"].values():
        assert [line["project"] for line in lines] == [project["name"]]


def test_completed_tasks_are_not_listed_as_upcoming_goals(client, project_payload, db_session):
    project = create_project(client, project_payload)
    done = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "끝난 업무", "estimated_hours": 4}).json()
    client.post(f"/api/projects/{project['id']}/tasks", json={"title": "남은 업무", "estimated_hours": 4})
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    assert client.post(f"/api/tasks/{done['id']}/complete", json={}).status_code == 200

    report = build_daily_report(db_session, date.today())
    for section in ("금일", "주간 목표", "월간 목표"):
        items = report["sections"][section][0]["items"]
        assert "끝난 업무" not in items, f"{section}에 완료된 업무가 목표로 남아 있다"
        assert "남은 업무" in items


def test_yesterday_uses_events_not_stale_schedule(client, project_payload, db_session):
    from app.models import TaskEvent

    project = create_project(client, project_payload)
    task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "어제 한 일", "estimated_hours": 4}).json()
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})

    today = date(2026, 8, 12)  # 수요일
    yesterday = previous_workday(today, {0, 1, 2, 3, 4})
    db_session.add(
        TaskEvent(
            task_id=task["id"],
            event_type="progress_updated",
            previous_value='{"progress_percent": 0}',
            new_value='{"progress_percent": 60}',
            created_at=datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9),
        )
    )
    db_session.commit()

    report = build_daily_report(db_session, today)
    line = report["sections"]["전일"][0]
    assert line["items"] == ["어제 한 일"]
    assert line["percent"] == 60


def test_yesterday_is_blank_without_events(client, project_payload, db_session):
    project = create_project(client, project_payload)
    client.post(f"/api/projects/{project['id']}/tasks", json={"title": "배치만 된 업무", "estimated_hours": 4})
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    line = build_daily_report(db_session, date(2026, 8, 12))["sections"]["전일"][0]
    assert line["percent"] is None
    assert line["items"] == []
    assert "특이사항 없음" in line["text"]


def test_render_text_matches_slack_format(client, project_payload, db_session):
    create_project(client, project_payload)
    text = render_report_text(build_daily_report(db_session, date(2026, 8, 8)))
    assert text.startswith("% - 각 기간별 대비 진행률>")
    for section in ("전일", "금일", "주간 목표", "월간 목표"):
        assert f"\n{section}\n" in text


def test_daily_report_endpoint(client, project_payload):
    create_project(client, project_payload)
    response = client.get("/api/reports/daily?date_=2026-08-08")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == "2026-08-08"
    assert "전일" in body["text"]
    assert client.get("/api/reports/daily?date_=엉터리").status_code == 422


def test_slack_history_filters_to_report_shaped_messages():
    import daily_report

    messages = [
        {"ts": "1", "user": "U1", "text": "점심 뭐 먹죠"},
        {"ts": "2", "user": "U1", "text": "전일\n(100%)A : 완료\n금일\n(50%)A : 진행"},
    ]
    picked = daily_report.my_recent_reports(messages)
    assert len(picked) == 1
    assert "전일" in picked[0]


def test_slack_token_read_from_environment_only(monkeypatch):
    import daily_report

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)
    assert daily_report.slack_token() is None
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    assert daily_report.slack_token() == "xoxb-test"


def test_schedule_plist_runs_on_weekdays_without_posting():
    import daily_report

    plist = daily_report.schedule_plist(8, 40)
    assert [entry["Weekday"] for entry in plist["StartCalendarInterval"]] == [1, 2, 3, 4, 5]
    assert all(entry["Hour"] == 8 and entry["Minute"] == 40 for entry in plist["StartCalendarInterval"])
    # 자동 실행은 초안 저장·알림까지만 한다. Slack 전송 옵션이 들어가면 안 된다.
    assert "--post" not in plist["ProgramArguments"]
    assert "--save" in plist["ProgramArguments"]
