from __future__ import annotations

from pathlib import Path

from app.database import SessionLocal
from app.models import SourceDocument, Task


def create_project(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    return response.json()


def test_mutation_with_untrusted_origin_is_rejected(client, project_payload):
    response = client.post(
        "/api/projects",
        json=project_payload,
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_source_upload_rejects_unsupported_content_type(client, project_payload):
    project = create_project(client, project_payload)
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        content=b"plain body",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


def test_task_rejects_server_owned_fields_and_out_of_range_values(client, project_payload):
    project = create_project(client, project_payload)
    ai_field = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "위조", "ai_generated": True, "confidence": 1},
    )
    assert ai_field.status_code == 422
    too_many_hours = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "초과", "estimated_hours": 10000.1},
    )
    assert too_many_hours.status_code == 422
    bad_due_date = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "날짜", "due_date": "2101-01-01"},
    )
    assert bad_due_date.status_code == 422


def test_source_response_hides_storage_path_and_saved_name_is_uuid_only(client, project_payload):
    project = create_project(client, project_payload)
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("original-secret-name.md", b"safe text", "text/markdown")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "storage_path" not in body
    with SessionLocal() as db:
        source = db.get(SourceDocument, body["id"])
        stored_name = Path(source.storage_path).name
    assert stored_name.endswith(".md")
    assert "original-secret-name" not in stored_name
    assert len(Path(stored_name).stem) == 32


def test_pending_or_rejected_tasks_cannot_be_completed_or_blocked(client, project_payload):
    project = create_project(client, project_payload)
    with SessionLocal() as db:
        pending = Task(project_id=project["id"], title="대기", status="pending_review", ai_generated=True)
        rejected = Task(project_id=project["id"], title="거절", status="rejected", ai_generated=True)
        db.add_all([pending, rejected])
        db.commit()
        pending_id, rejected_id = pending.id, rejected.id
    assert client.post(f"/api/tasks/{pending_id}/complete", json={}).status_code == 409
    assert client.post(f"/api/tasks/{rejected_id}/block", json={"reason": "x"}).status_code == 409
