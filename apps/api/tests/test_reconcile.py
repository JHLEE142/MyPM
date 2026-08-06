from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ProjectFact, ScheduleVersion, SourceBlock, Task, TaskSourceLink
from scheduler.dependencies import find_cycle
from scheduler.engine import schedule_tasks


def create_project(client, payload, **overrides):
    response = client.post("/api/projects", json={**payload, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def create_task(client, project_id: int, **values):
    response = client.post(f"/api/projects/{project_id}/tasks", json={"title": "업무", **values})
    assert response.status_code == 201, response.text
    return response.json()


def test_analysis_failure_keeps_existing_tasks_and_facts(client, project_payload, monkeypatch):
    project = create_project(client, project_payload)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "brief.md", "text": "- 새 업무 1시간"},
    ).json()
    with SessionLocal() as db:
        existing_task = Task(
            project_id=project["id"],
            title="기존 AI 업무",
            status="pending_review",
            ai_generated=True,
            confidence=0.8,
        )
        existing_fact = ProjectFact(
            project_id=project["id"],
            fact_type="requirement",
            content="기존 요구사항",
            confidence=0.8,
            review_status="pending_review",
            source_block_id=source["blocks"][0]["id"],
        )
        db.add_all([existing_task, existing_fact])
        db.commit()
        task_id, fact_id = existing_task.id, existing_fact.id

    class FailingProvider:
        name = "failing"

        def analyze_document(self, source_id, blocks):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.services.get_provider", lambda: FailingProvider())
    response = client.post(f"/api/projects/{project['id']}/analysis")
    assert response.status_code == 202
    status = client.get(f"/api/projects/{project['id']}/analysis/status").json()
    assert status["status"] == "failed"
    assert status["error_message"].startswith("RuntimeError:")
    with SessionLocal() as db:
        assert db.get(Task, task_id).title == "기존 AI 업무"
        assert db.get(ProjectFact, fact_id).content == "기존 요구사항"


def test_dashboard_warns_when_assignment_exceeds_effective_capacity(client, project_payload):
    project = create_project(client, project_payload, daily_capacity_hours=4, buffer_ratio=0)
    create_task(client, project["id"], estimated_hours=3.5)
    generated = client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    assert generated.status_code == 201
    updated = client.patch(f"/api/projects/{project['id']}", json={"buffer_ratio": 0.25})
    assert updated.status_code == 200
    dashboard = client.get(f"/api/projects/{project['id']}/dashboard?as_of=2099-01-05").json()
    assert dashboard["today"]["raw_daily_capacity_hours"] == 4
    assert dashboard["today"]["effective_daily_capacity_hours"] == 3
    assert dashboard["today"]["assigned_hours"] == 3.5
    assert dashboard["today"]["over_capacity"] is True
    assert dashboard["today"]["excess_hours"] == 0.5


def test_generate_after_prerequisite_completion_schedules_dependent(client, project_payload):
    project = create_project(client, project_payload)
    prerequisite = create_task(client, project["id"], title="A", estimated_hours=2)
    dependent = create_task(
        client,
        project["id"],
        title="B",
        estimated_hours=2,
        dependency_ids=[prerequisite["id"]],
    )
    completed = client.post(
        f"/api/tasks/{prerequisite['id']}/complete",
        json={"completed_date": "2099-01-05"},
    )
    assert completed.status_code == 200
    snapshot = client.post(f"/api/projects/{project['id']}/schedule/generate", json={}).json()["schedule_snapshot"]
    assert dependent["id"] in {item["task_id"] for item in snapshot["placements"]}
    assert dependent["id"] not in {item["task_id"] for item in snapshot["unscheduled"]}
    assert min(item["date"] for item in snapshot["placements"] if item["task_id"] == dependent["id"]) > "2099-01-05"


def test_replan_reports_or_places_locked_and_due_tasks_without_previous_placement(client, project_payload):
    project = create_project(client, project_payload)
    initial = client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    assert initial.status_code == 201
    locked = create_task(client, project["id"], title="잠금", estimated_hours=2, locked=True)
    due = create_task(client, project["id"], title="마감", estimated_hours=2, due_date="2099-01-06")
    response = client.post(
        f"/api/projects/{project['id']}/schedule/replan",
        json={"reason": "보호 업무 회귀", "strategy": "redistribute"},
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()["schedule_snapshot"]
    reported_ids = {item["task_id"] for item in snapshot["placements"] + snapshot["unscheduled"]}
    assert {locked["id"], due["id"]} <= reported_ids


def test_partial_prerequisite_blocks_dependent_and_zero_hour_prerequisite_does_not():
    base = {
        "start_date": date(2026, 8, 3),
        "target_date": date(2026, 8, 3),
        "work_days": [0, 1, 2, 3, 4],
        "daily_capacity_hours": 4,
        "buffer_ratio": 0,
    }
    partial = schedule_tasks(
        [
            {"id": 1, "estimated_hours": 6, "dependency_ids": []},
            {"id": 2, "estimated_hours": 1, "dependency_ids": [1]},
        ],
        **base,
    )
    assert {item["task_id"]: item["reason"] for item in partial["unscheduled"]} == {
        1: "deadline_capacity_exceeded",
        2: "dependency_unscheduled",
    }
    zero = schedule_tasks(
        [
            {"id": 1, "estimated_hours": 0, "dependency_ids": []},
            {"id": 2, "estimated_hours": 1, "dependency_ids": [1]},
        ],
        **{**base, "target_date": date(2026, 8, 4)},
    )
    dependent_dates = [item["date"] for item in zero["placements"] if item["task_id"] == 2]
    assert dependent_dates == ["2026-08-04"]
    assert not zero["unscheduled"]


def test_replan_assigns_only_remaining_effort_for_in_progress_task(client, project_payload):
    project = create_project(client, project_payload)
    task = create_task(client, project["id"], estimated_hours=10)
    updated = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "in_progress", "progress_percent": 40},
    )
    assert updated.status_code == 200
    response = client.post(
        f"/api/projects/{project['id']}/schedule/replan",
        json={"reason": "잔여공수", "strategy": "redistribute"},
    )
    snapshot = response.json()["schedule_snapshot"]
    placements = [item for item in snapshot["placements"] if item["task_id"] == task["id"]]
    assert sum(item["hours"] for item in placements) == 6
    assert {item["original_estimated_hours"] for item in placements} == {10}
    assert {item["remaining_estimated_hours"] for item in placements} == {6}


def test_regeneration_does_not_change_planned_progress_baseline(client, project_payload):
    project = create_project(client, project_payload)
    task = create_task(client, project["id"], estimated_hours=4)
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    client.post(f"/api/tasks/{task['id']}/complete", json={"completed_date": "2099-01-06"})
    before = client.get(f"/api/projects/{project['id']}/pace?as_of=2099-01-06").json()
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    after = client.get(f"/api/projects/{project['id']}/pace?as_of=2099-01-06").json()
    assert after["planned_progress_percent"] == before["planned_progress_percent"]


def test_patch_cannot_approve_pending_ai_task(client, project_payload):
    project = create_project(client, project_payload)
    with SessionLocal() as db:
        task = Task(project_id=project["id"], title="AI 업무", status="pending_review", ai_generated=True)
        db.add(task)
        db.commit()
        task_id = task.id
    response = client.patch(f"/api/tasks/{task_id}", json={"status": "approved"})
    assert response.status_code == 422


def test_cross_project_source_block_is_rejected(client, project_payload):
    first = create_project(client, project_payload, name="첫 프로젝트")
    second = create_project(client, project_payload, name="둘째 프로젝트")
    source = client.post(
        f"/api/projects/{second['id']}/sources",
        json={"file_name": "second.md", "text": "둘째 프로젝트 근거"},
    ).json()
    response = client.post(
        f"/api/projects/{first['id']}/tasks",
        json={"title": "잘못된 참조", "source_block_ids": [source["blocks"][0]["id"]]},
    )
    assert response.status_code == 422


def test_source_delete_is_blocked_when_approved_ai_task_references_it(client, project_payload):
    project = create_project(client, project_payload)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "evidence.md", "text": "승인 근거"},
    ).json()
    with SessionLocal() as db:
        task = Task(project_id=project["id"], title="승인 AI 업무", status="approved", ai_generated=True)
        db.add(task)
        db.flush()
        db.add(TaskSourceLink(task_id=task.id, source_block_id=source["blocks"][0]["id"]))
        db.commit()
    response = client.delete(f"/api/sources/{source['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["tasks"][0]["title"] == "승인 AI 업무"


def test_reopen_restores_state_progress_and_actual_hours(client, project_payload):
    project = create_project(client, project_payload)
    task = create_task(client, project["id"], estimated_hours=5, actual_hours=1.5, progress_percent=30)
    completed = client.post(
        f"/api/tasks/{task['id']}/complete",
        json={"actual_hours": 4, "completed_date": "2099-01-06"},
    )
    assert completed.status_code == 200
    reopened = client.post(f"/api/tasks/{task['id']}/reopen")
    assert reopened.status_code == 200
    body = reopened.json()
    assert body["status"] == "approved"
    assert body["progress_percent"] == 30
    assert body["actual_hours"] == 1.5
    assert body["actual_end_date"] is None


def test_iterative_cycle_search_handles_long_chains():
    nodes = list(range(1501))
    edges = [(node, node - 1) for node in range(1, len(nodes))]
    assert find_cycle(nodes, edges) is None
    cycle = find_cycle(nodes, [*edges, (0, 1500)])
    assert cycle is not None


def test_defer_low_priority_records_deferred_tasks(client, project_payload):
    project = create_project(client, project_payload)
    low = create_task(client, project["id"], title="낮은 업무", priority="low")
    create_task(client, project["id"], title="높은 업무", priority="high")
    response = client.post(
        f"/api/projects/{project['id']}/schedule/replan",
        json={"reason": "이월", "strategy": "defer_low_priority"},
    )
    assert response.status_code == 201
    assert response.json()["schedule_snapshot"]["deferred"] == [
        {"task_id": low["id"], "reason": "low_priority_not_started"}
    ]


def test_completed_task_plan_dates_are_preserved_on_regeneration(client, project_payload):
    project = create_project(client, project_payload)
    task = create_task(client, project["id"], estimated_hours=2)
    generated = client.post(f"/api/projects/{project['id']}/schedule/generate", json={}).json()
    original_dates = [
        item["date"] for item in generated["schedule_snapshot"]["placements"] if item["task_id"] == task["id"]
    ]
    client.post(f"/api/tasks/{task['id']}/complete", json={"completed_date": "2099-01-06"})
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    fetched = client.get(f"/api/tasks/{task['id']}").json()
    assert fetched["planned_start_date"] == min(original_dates)
    assert fetched["planned_end_date"] == max(original_dates)


def test_schedule_version_insert_retries_once_on_unique_conflict(client, project_payload, monkeypatch):
    project = create_project(client, project_payload)
    create_task(client, project["id"])
    client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    allocated = iter([1, 2])
    monkeypatch.setattr("app.services.next_schedule_version", lambda db, project_id: next(allocated))
    retried = client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    assert retried.status_code == 201, retried.text
    assert retried.json()["version"] == 2
    with SessionLocal() as db:
        versions = list(
            db.scalars(
                select(ScheduleVersion)
                .where(ScheduleVersion.project_id == project["id"])
                .order_by(ScheduleVersion.version)
            )
        )
    assert [version.version for version in versions] == [1, 2]
