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


def test_reorder_tasks_changes_list_and_schedule_order(client):
    project = _project(client)
    ids = [
        client.post(f"/api/projects/{project['id']}/tasks", json={"title": f"업무 {i}", "estimated_hours": 8}).json()["id"]
        for i in range(3)
    ]
    reversed_ids = list(reversed(ids))
    response = client.post(f"/api/projects/{project['id']}/tasks/reorder", json={"ordered_ids": reversed_ids})
    assert response.status_code == 200
    assert response.json()["updated"] == 3
    listed = [task["id"] for task in client.get(f"/api/projects/{project['id']}/tasks").json()]
    assert listed == reversed_ids
    generated = client.post(f"/api/projects/{project['id']}/schedule/generate", json={}).json()
    placements = generated["schedule_snapshot"]["placements"]
    first_by_task = {}
    for item in placements:
        first_by_task.setdefault(item["task_id"], item["date"])
    ordered_by_first_date = sorted(first_by_task, key=lambda task_id: first_by_task[task_id])
    assert ordered_by_first_date == reversed_ids


def test_reorder_rejects_foreign_and_duplicate_ids(client):
    project_a = _project(client)
    project_b = client.post("/api/projects", json={"name": "다른 프로젝트"}).json()
    task_a = client.post(f"/api/projects/{project_a['id']}/tasks", json={"title": "A", "estimated_hours": 1}).json()
    task_b = client.post(f"/api/projects/{project_b['id']}/tasks", json={"title": "B", "estimated_hours": 1}).json()
    foreign = client.post(
        f"/api/projects/{project_a['id']}/tasks/reorder", json={"ordered_ids": [task_a["id"], task_b["id"]]}
    )
    assert foreign.status_code == 422
    duplicate = client.post(
        f"/api/projects/{project_a['id']}/tasks/reorder", json={"ordered_ids": [task_a["id"], task_a["id"]]}
    )
    assert duplicate.status_code == 422


def _hierarchy(client):
    project = _project(client)
    with SessionLocal() as db:
        m1 = Task(project_id=project["id"], cadence="monthly", title="1월", status="approved", estimated_hours=0)
        m2 = Task(project_id=project["id"], cadence="monthly", title="2월", status="approved", estimated_hours=0)
        db.add_all([m1, m2]); db.flush()
        w1 = Task(project_id=project["id"], parent_task_id=m1.id, cadence="weekly", title="1주", status="approved", estimated_hours=0)
        w2 = Task(project_id=project["id"], parent_task_id=m2.id, cadence="weekly", title="2월 1주", status="approved", estimated_hours=0)
        db.add_all([w1, w2]); db.flush()
        d1 = Task(project_id=project["id"], parent_task_id=w1.id, cadence="daily", title="일간 A", status="approved", estimated_hours=2)
        d2 = Task(project_id=project["id"], parent_task_id=w2.id, cadence="daily", title="일간 B", status="approved", estimated_hours=3)
        db.add_all([d1, d2]); db.commit()
        return project, m1.id, m2.id, w1.id, w2.id, d1.id, d2.id


def test_move_daily_to_other_weekly_and_to_root(client):
    project, m1, m2, w1, w2, d1, d2 = _hierarchy(client)
    moved = client.post(f"/api/tasks/{d1}/move", json={"new_parent_id": w2, "before_task_id": d2})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["parent_task_id"] == w2 and body["cadence"] == "daily"
    order = [t["id"] for t in client.get(f"/api/projects/{project['id']}/tasks").json() if t["parent_task_id"] == w2]
    assert order == [d1, d2]

    to_root = client.post(f"/api/tasks/{d1}/move", json={"new_parent_id": None})
    assert to_root.status_code == 200
    assert to_root.json()["parent_task_id"] is None and to_root.json()["cadence"] is None


def test_move_weekly_to_other_monthly_and_invalid_moves(client):
    project, m1, m2, w1, w2, d1, d2 = _hierarchy(client)
    moved = client.post(f"/api/tasks/{w1}/move", json={"new_parent_id": m2, "before_task_id": w2})
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_task_id"] == m2
    # 월간은 이동 불가
    assert client.post(f"/api/tasks/{m1}/move", json={"new_parent_id": m2}).status_code == 422
    # 주간을 주간 아래로 이동 불가
    assert client.post(f"/api/tasks/{w1}/move", json={"new_parent_id": w2}).status_code == 422
    # 일간을 월간 바로 아래로 이동 불가
    assert client.post(f"/api/tasks/{d2}/move", json={"new_parent_id": m1}).status_code == 422


def test_emptied_weekly_container_is_not_scheduled(client):
    project, m1, m2, w1, w2, d1, d2 = _hierarchy(client)
    # w1의 유일한 일간을 다른 곳으로 이동해 w1을 비운다
    assert client.post(f"/api/tasks/{d1}/move", json={"new_parent_id": w2}).status_code == 200
    generated = client.post(f"/api/projects/{project['id']}/schedule/generate", json={}).json()
    placement_ids = {item["task_id"] for item in generated["schedule_snapshot"]["placements"]}
    assert w1 not in placement_ids and m1 not in placement_ids
    assert {d1, d2} <= placement_ids


def test_daily_window_schedules_task_in_its_week():
    from scheduler.engine import schedule_tasks

    result = schedule_tasks(
        [
            {"id": 1, "priority": "medium", "estimated_hours": 2, "dependency_ids": [],
             "due_date": date(2099, 1, 23), "not_before": date(2099, 1, 17)},
            {"id": 2, "priority": "medium", "estimated_hours": 2, "dependency_ids": []},
        ],
        start_date=date(2099, 1, 1),
        target_date=date(2099, 1, 31),
        work_days=[0, 1, 2, 3, 4, 5, 6],
        daily_capacity_hours=8,
        buffer_ratio=0,
    )
    dates_1 = [date.fromisoformat(p["date"]) for p in result["schedule_snapshot" if "schedule_snapshot" in result else "placements"] ] if False else [
        date.fromisoformat(p["date"]) for p in result["placements"] if p["task_id"] == 1
    ]
    dates_2 = [date.fromisoformat(p["date"]) for p in result["placements"] if p["task_id"] == 2]
    assert min(dates_1) >= date(2099, 1, 17) and max(dates_1) <= date(2099, 1, 23)
    assert min(dates_2) == date(2099, 1, 1)  # 창 없는 업무는 기존대로 앞에서부터


def test_weekly_anchor_assigns_due_dates_and_windows(client, monkeypatch):
    from ai.schemas import DailyTaskItem, HierarchicalTaskSet, MonthlyTaskItem, SourceReference, WeeklyTaskItem
    from ai.router import AiRouter

    monkeypatch.delenv("AI_REVIEW_GATE", raising=False)
    project = _project(client)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "meeting.md", "text": "# 업무\n- 자료 요청 리스트 작성 2시간"},
    )
    block_id = source.json()["blocks"][0]["id"]

    def fake_generate(self, analysis, context=None):
        assert context and context.get("project_name")  # 컨텍스트가 전달되는지
        return HierarchicalTaskSet(monthly=[
            MonthlyTaskItem(
                title="1월: 자료 준비", target_month="2099-01",
                weekly=[WeeklyTaskItem(
                    title="1월 3주차: 자료 요청", target_week_start=date(2099, 1, 12),
                    daily=[DailyTaskItem(
                        title="자료 요청 리스트 작성", estimated_hours=2,
                        source_references=[SourceReference(source_id=source.json()["id"], block_id=block_id)],
                    )],
                )],
            )
        ])

    monkeypatch.setattr(AiRouter, "generate_hierarchical_tasks", fake_generate)
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 202
    tasks = client.get(f"/api/projects/{project['id']}/tasks").json()
    daily = next(t for t in tasks if t["cadence"] == "daily")
    weekly = next(t for t in tasks if t["cadence"] == "weekly")
    assert daily["due_date"] == "2099-01-16"   # 주차 앵커(월요일)+4일 = 금요일
    assert weekly["due_date"] == "2099-01-16"  # max(일간 due)
    schedule = client.get(f"/api/projects/{project['id']}/schedule").json()
    daily_dates = [p["date"] for p in schedule["schedule_snapshot"]["placements"] if p["task_id"] == daily["id"]]
    assert daily_dates and min(daily_dates) >= "2099-01-10"  # due-6일 이후에만 배치
