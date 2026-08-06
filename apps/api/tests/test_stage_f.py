from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Project, ScheduleVersion, Task
from app.services import calculate_pace
from seed_demo import seed_demo


def test_project_can_be_created_with_name_only_and_owner_can_be_patched(client):
    today = date.today()
    response = client.post("/api/projects", json={"name": "간단 프로젝트", "owner": "이정현"})
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["owner"] == "이정현"
    assert project["start_date"] == today.isoformat()
    assert project["target_date"] == (today + timedelta(days=30)).isoformat()
    assert project["work_days"] == [0, 1, 2, 3, 4]
    assert project["daily_capacity_hours"] == 4.0
    assert project["buffer_ratio"] == 0.2
    assert project["excluded_dates"] == []

    patched = client.patch(f"/api/projects/{project['id']}", json={"owner": "김서연"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["owner"] == "김서연"

    cleared = client.patch(f"/api/projects/{project['id']}", json={"owner": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["owner"] is None


def test_existing_full_project_payload_remains_supported(client, project_payload):
    response = client.post("/api/projects", json={**project_payload, "owner": "기존 담당자"})
    assert response.status_code == 201, response.text
    assert response.json()["start_date"] == project_payload["start_date"]
    assert response.json()["daily_capacity_hours"] == project_payload["daily_capacity_hours"]


def test_seed_demo_creates_five_projects_with_tasks_and_schedules():
    created = seed_demo()
    assert len(created) == 5
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project).order_by(Project.id)))
        assert len(projects) == 5
        assert all(project.owner for project in projects)
        assert db.scalar(select(func.count(Task.id))) == 37
        assert db.scalar(select(func.count(ScheduleVersion.id))) == 5
        assert all(
            db.scalar(select(func.count(ScheduleVersion.id)).where(ScheduleVersion.project_id == project.id)) == 1
            for project in projects
        )
        by_name = {project.name: project for project in projects}
        statuses = {
            name: Counter(db.scalars(select(Task.status).where(Task.project_id == project.id)))
            for name, project in by_name.items()
        }
        assert statuses["PacePM 베타 출시"]["completed"] == 4
        assert sum(statuses["고객사 A SI 구축"].values()) == 10
        assert statuses["고객사 A SI 구축"]["completed"] == 2
        assert statuses["고객사 A SI 구축"]["in_progress"] == 1
        assert statuses["고객사 A SI 구축"]["blocked"] == 1
        assert statuses["사내 문서 자동화 PoC"]["completed"] == 1
        assert statuses["홈페이지 리뉴얼"] == Counter({"completed": 7, "in_progress": 1})
        assert all(
            task.progress_percent == 0
            for task in db.scalars(select(Task).where(Task.project_id == by_name["데이터 파이프라인 개선"].id))
        )
        assert calculate_pace(db, by_name["고객사 A SI 구축"], date(2026, 8, 6))["status"] == "warning"

    assert seed_demo() == []
