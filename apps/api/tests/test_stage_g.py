from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, inspect, select, text

from ai.document_analyzer import build_document_prompt
from ai.schemas import (
    DailyTaskItem,
    HierarchicalTaskSet,
    MonthlyTaskItem,
    ProjectAnalysis,
    SourceReference,
    TaskCandidate,
    WeeklyTaskItem,
)
from ai.task_generator import build_hierarchical_task_prompt
from app import database
from app.database import SessionLocal
from app.models import Project, Task
from app.services import calculate_forecast, calculate_pace, generate_schedule


def _project(client, *, start_date: date | None = None, target_date: date | None = None):
    start_date = start_date or date(2099, 1, 1)
    target_date = target_date or date(2099, 2, 28)
    response = client.post(
        "/api/projects",
        json={
            "name": "Stage G 프로젝트",
            "start_date": start_date.isoformat(),
            "target_date": target_date.isoformat(),
            "work_days": [0, 1, 2, 3, 4, 5, 6],
            "daily_capacity_hours": 8,
            "buffer_ratio": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_mock_analysis_creates_approved_three_level_hierarchy(client, monkeypatch):
    monkeypatch.delenv("AI_REVIEW_GATE", raising=False)
    project = _project(client)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={
            "file_name": "hierarchy.md",
            "text": (
                "# 업무\n"
                "- 요구사항 정리 2시간 2099-01-09\n"
                "- API 구현 3시간 2099-01-10"
            ),
        },
    )
    assert source.status_code == 201, source.text
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 202

    tasks = client.get(f"/api/projects/{project['id']}/tasks").json()
    monthly = [task for task in tasks if task["cadence"] == "monthly"]
    weekly = [task for task in tasks if task["cadence"] == "weekly"]
    daily = [task for task in tasks if task["cadence"] == "daily"]
    assert len(monthly) == len(weekly) == 1
    assert len(daily) == 2
    assert weekly[0]["parent_task_id"] == monthly[0]["id"]
    assert {task["parent_task_id"] for task in daily} == {weekly[0]["id"]}
    assert monthly[0]["estimated_hours"] == weekly[0]["estimated_hours"] == 5
    # 자동 승인 후 자동 일정 배치까지 이어지므로 배치된 리프는 scheduled가 될 수 있다.
    assert all(task["status"] in {"approved", "scheduled"} and task["ai_generated"] for task in tasks)
    assert len(monthly[0]["source_links"]) == len(weekly[0]["source_links"]) == 1
    assert all(task["source_links"] for task in daily)

    milestones = client.get(f"/api/projects/{project['id']}/milestones").json()
    assert len(milestones) == 1
    assert milestones[0]["title"] == monthly[0]["title"]
    assert {task["milestone_id"] for task in tasks} == {milestones[0]["id"]}


def test_hierarchy_progress_forecast_and_schedule_use_only_leaves():
    with SessionLocal() as db:
        project = Project(
            name="리프 집계",
            start_date=date(2099, 1, 1),
            target_date=date(2099, 1, 31),
            work_days=[0, 1, 2, 3, 4, 5, 6],
            daily_capacity_hours=8,
            buffer_ratio=0,
            excluded_dates=[],
        )
        db.add(project)
        db.flush()
        monthly = Task(
            project_id=project.id,
            cadence="monthly",
            title="1월 업무",
            status="approved",
            estimated_hours=10,
            progress_percent=100,
        )
        db.add(monthly)
        db.flush()
        weekly = Task(
            project_id=project.id,
            parent_task_id=monthly.id,
            cadence="weekly",
            title="1주차 업무",
            status="approved",
            estimated_hours=10,
            progress_percent=100,
        )
        db.add(weekly)
        db.flush()
        completed = Task(
            project_id=project.id,
            parent_task_id=weekly.id,
            cadence="daily",
            title="완료 리프",
            status="completed",
            estimated_hours=4,
            progress_percent=100,
            actual_hours=4,
            actual_end_date=date(2099, 1, 2),
        )
        remaining = Task(
            project_id=project.id,
            parent_task_id=weekly.id,
            cadence="daily",
            title="남은 리프",
            status="approved",
            estimated_hours=6,
            progress_percent=0,
        )
        db.add_all([completed, remaining])
        db.commit()

        pace = calculate_pace(db, project, date(2099, 1, 3))
        forecast = calculate_forecast(db, project, date(2099, 1, 3))
        version = generate_schedule(db, project, "리프 전용 검증", as_of=date(2099, 1, 3))
        placement_ids = {item["task_id"] for item in version.schedule_snapshot["placements"]}
        assert pace["actual_progress_percent"] == 40
        assert forecast["remaining_hours"] == 6
        assert placement_ids == {remaining.id}
        assert monthly.id not in placement_ids and weekly.id not in placement_ids


def test_parent_complete_is_conflict_and_parent_delete_removes_descendants(client):
    project = _project(client)
    with SessionLocal() as db:
        monthly = Task(
            project_id=project["id"], cadence="monthly", title="월간", status="approved", estimated_hours=1
        )
        db.add(monthly)
        db.flush()
        weekly = Task(
            project_id=project["id"], parent_task_id=monthly.id, cadence="weekly", title="주간", status="approved", estimated_hours=1
        )
        db.add(weekly)
        db.flush()
        daily = Task(
            project_id=project["id"], parent_task_id=weekly.id, cadence="daily", title="일간", status="approved", estimated_hours=1
        )
        db.add(daily)
        db.commit()
        monthly_id = monthly.id

    completed = client.post(f"/api/tasks/{monthly_id}/complete", json={})
    assert completed.status_code == 409
    assert completed.json()["detail"] == "하위 업무를 완료하세요"
    with SessionLocal() as db:
        db.get(Task, monthly_id).status = "completed"
        db.commit()
    reopened = client.post(f"/api/tasks/{monthly_id}/reopen", json={})
    assert reopened.status_code == 409
    assert reopened.json()["detail"] == "하위 업무를 완료하세요"
    assert client.delete(f"/api/tasks/{monthly_id}").status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Task.id)).where(Task.project_id == project["id"])) == 0


def test_generate_schedule_clamps_past_project_start_to_today(client):
    today = date.today()
    project = _project(client, start_date=today - timedelta(days=30), target_date=today + timedelta(days=10))
    task = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "오늘부터 배치", "estimated_hours": 2},
    )
    assert task.status_code == 201, task.text
    generated = client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    assert generated.status_code == 201, generated.text
    placements = generated.json()["schedule_snapshot"]["placements"]
    assert placements
    assert min(date.fromisoformat(item["date"]) for item in placements) >= today


def test_hierarchical_and_document_prompts_require_korean_output():
    analysis = ProjectAnalysis(
        task_candidates=[
            TaskCandidate(
                title="Build API",
                description="Implement endpoints",
                source_references=[SourceReference(source_id=1, block_id=2)],
            )
        ]
    )
    hierarchy_prompt = build_hierarchical_task_prompt(analysis)
    document_prompt = build_document_prompt(1, [{"id": 2, "content": "Build API", "block_order": 0}])
    assert "먼저 월간 업무를 정하고" in hierarchy_prompt
    assert "일간 업무에만 estimated_hours와 source_references" in hierarchy_prompt
    assert "모든 title과 description은 반드시 한국어" in hierarchy_prompt
    assert "모든 출력은 반드시 한국어" in document_prompt
    assert "한국어로 요약·번역" in document_prompt


def test_hierarchical_schema_forbids_extra_fields_and_enforces_limits():
    daily = DailyTaskItem(
        title="일간",
        source_references=[SourceReference(source_id=1, block_id=1)],
    )
    weekly = WeeklyTaskItem(title="주간", daily=[daily])
    monthly = MonthlyTaskItem(title="월간", target_month="2099-01", weekly=[weekly])
    with pytest.raises(ValidationError):
        DailyTaskItem.model_validate(
            {
                "title": "일간",
                "source_references": [{"source_id": 1, "block_id": 1}],
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        HierarchicalTaskSet(monthly=[monthly] * 13)
    with pytest.raises(ValidationError):
        WeeklyTaskItem(title="빈 주간", daily=[])


def test_ensure_schema_adds_cadence_to_legacy_sqlite_tasks(monkeypatch):
    legacy_engine = create_engine("sqlite://")
    with legacy_engine.begin() as connection:
        connection.execute(text("CREATE TABLE tasks (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(database, "engine", legacy_engine)
    database.ensure_schema()
    assert "cadence" in {column["name"] for column in inspect(legacy_engine).get_columns("tasks")}
    legacy_engine.dispose()
