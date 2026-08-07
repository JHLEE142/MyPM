"""자료 텍스트 → 요약 + 기존 업무 반영 흐름 (mock provider 기준)."""
from __future__ import annotations


def create_project(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def add_task(client, project_id, title, hours=4):
    response = client.post(
        f"/api/projects/{project_id}/tasks", json={"title": title, "estimated_hours": hours}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_preview_matches_existing_tasks_and_apply_updates_progress(client, project_payload):
    project = create_project(client, project_payload)
    design_id = add_task(client, project["id"], "화면 설계")
    api_id = add_task(client, project["id"], "업무 API 개발")

    preview = client.post(
        f"/api/projects/{project['id']}/task-updates/preview",
        json={"text": "(60%)화면 설계 진행 중입니다.\n업무 API 개발은 완료했습니다."},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["summary"]
    by_task = {item["task_id"]: item for item in body["updates"]}
    assert by_task[design_id]["action"] == "update"
    assert by_task[design_id]["progress_percent"] == 60
    assert by_task[design_id]["current"]["progress_percent"] == 0
    assert by_task[api_id]["action"] == "complete"

    applied = client.post(
        f"/api/projects/{project['id']}/task-updates/apply",
        json={
            "updates": [
                {"action": "update", "task_id": design_id, "progress_percent": 60},
                {"action": "complete", "task_id": api_id},
            ]
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json() == {"updated": 1, "completed": 1, "created": 0}

    tasks = {item["id"]: item for item in client.get(f"/api/projects/{project['id']}/tasks").json()}
    assert tasks[design_id]["progress_percent"] == 60
    assert tasks[design_id]["status"] == "in_progress"
    assert tasks[api_id]["status"] == "completed"
    assert tasks[api_id]["progress_percent"] == 100


def test_apply_can_create_a_task_and_rejects_unknown_task_id(client, project_payload):
    project = create_project(client, project_payload)
    add_task(client, project["id"], "기존 업무")

    created = client.post(
        f"/api/projects/{project['id']}/task-updates/apply",
        json={"updates": [{"action": "create", "title": "메모에서 나온 새 업무", "estimated_hours": 3}]},
    )
    assert created.status_code == 200, created.text
    assert created.json()["created"] == 1
    titles = [item["title"] for item in client.get(f"/api/projects/{project['id']}/tasks").json()]
    assert "메모에서 나온 새 업무" in titles

    missing = client.post(
        f"/api/projects/{project['id']}/task-updates/apply",
        json={"updates": [{"action": "update", "task_id": 999999, "progress_percent": 50}]},
    )
    assert missing.status_code == 404


def test_preview_requires_existing_tasks(client, project_payload):
    project = create_project(client, project_payload)
    response = client.post(
        f"/api/projects/{project['id']}/task-updates/preview", json={"text": "아무 메모"}
    )
    assert response.status_code == 409
