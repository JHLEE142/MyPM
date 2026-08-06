from __future__ import annotations


def create_project(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_api_smoke_project_manual_task_complete_and_pace(client, project_payload):
    project = create_project(client, project_payload)
    task = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "구현", "estimated_hours": 4, "priority": "high"},
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]
    assert client.post(f"/api/projects/{project['id']}/schedule/generate", json={}).status_code == 201
    completed = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"actual_hours": 3.5, "completed_date": "2099-01-06"},
    )
    assert completed.status_code == 200
    assert completed.json()["progress_percent"] == 100
    pace = client.get(f"/api/projects/{project['id']}/pace?as_of=2099-01-06")
    assert pace.status_code == 200
    assert pace.json()["actual_progress_percent"] == 100


def test_replan_protects_completed_and_locked_tasks_and_increments_version(client, project_payload):
    project = create_project(client, project_payload)
    ids = []
    for title, locked in (("완료할 업무", False), ("잠긴 업무", True), ("이동 업무", False)):
        response = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": title, "estimated_hours": 2, "locked": locked},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    first = client.post(f"/api/projects/{project['id']}/schedule/generate", json={}).json()
    client.post(f"/api/tasks/{ids[0]}/complete", json={"completed_date": "2099-01-05"})
    second_response = client.post(
        f"/api/projects/{project['id']}/schedule/replan",
        json={"reason": "delay", "strategy": "redistribute"},
    )
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    assert first["version"] == 1
    assert second["version"] == 2

    def placements(snapshot, task_id):
        return [
            (item["date"], item["hours"])
            for item in snapshot["schedule_snapshot"]["placements"]
            if item["task_id"] == task_id
        ]

    assert placements(first, ids[0]) == placements(second, ids[0])
    assert placements(first, ids[1]) == placements(second, ids[1])
    versions = client.get(f"/api/projects/{project['id']}/schedule/versions").json()
    assert [item["version"] for item in versions] == [1, 2]
    comparison = client.get(
        f"/api/projects/{project['id']}/schedule/versions/compare?from_version=1&to_version=2"
    )
    assert comparison.status_code == 200


def test_api_rejects_dependency_cycle(client, project_payload):
    project = create_project(client, project_payload)
    a = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "A"}).json()
    b = client.post(
        f"/api/projects/{project['id']}/tasks", json={"title": "B", "dependency_ids": [a["id"]]}
    ).json()
    response = client.patch(f"/api/tasks/{a['id']}", json={"dependency_ids": [b["id"]]})
    assert response.status_code == 422


def test_mock_analysis_review_approve_schedule_e2e(client, project_payload):
    project = create_project(client, project_payload)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={
            "file_name": "brief.md",
            "text": "# 목표\nPacePM MVP를 출시한다.\n\n## 업무\n- 프로젝트 API 구현 2시간\n- 일정 테스트 작성 1시간",
        },
    )
    assert source.status_code == 201, source.text
    assert source.json()["analysis_status"] == "text_extracted"
    analysis = client.post(f"/api/projects/{project['id']}/analysis")
    assert analysis.status_code == 202
    analysis_status = client.get(f"/api/projects/{project['id']}/analysis/status").json()
    assert analysis_status["status"] == "completed"
    assert analysis_status["model_provider"] == "mock"

    review = client.get(f"/api/projects/{project['id']}/analysis/review").json()
    assert len(review["tasks"]) == 2
    assert all(item["status"] == "pending_review" for item in review["tasks"])
    decisions = {
        "tasks": [{"id": item["id"], "action": "approve", "updates": {}} for item in review["tasks"]],
        "facts": [{"id": item["id"], "action": "approve", "updates": {}} for item in review["facts"]],
    }
    approved = client.post(f"/api/projects/{project['id']}/analysis/approve", json=decisions)
    assert approved.status_code == 200, approved.text
    scheduled = client.post(f"/api/projects/{project['id']}/schedule/generate", json={})
    assert scheduled.status_code == 201, scheduled.text
    scheduled_ids = {item["task_id"] for item in scheduled.json()["schedule_snapshot"]["placements"]}
    assert scheduled_ids == {item["id"] for item in review["tasks"]}
