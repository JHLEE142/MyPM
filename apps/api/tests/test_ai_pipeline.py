from __future__ import annotations

from datetime import date

from ai.document_analyzer import MockProvider
from ai.project_merger import merge_project_analyses
from ai.schemas import DocumentAnalysis, FixedDateItem, ProjectAnalysis, SourceReference, TaskCandidate
from ai.task_generator import generate_tasks
from app.database import SessionLocal
from app.models import Task, TaskDependency


def create_project(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    return response.json()


def test_missing_dependency_is_dropped_and_recorded_as_open_question():
    analysis = ProjectAnalysis(
        task_candidates=[
            TaskCandidate(
                title="구현",
                dependencies=["존재하지 않는 업무"],
                source_references=[SourceReference(source_id=1, block_id=10)],
            )
        ]
    )
    generated = generate_tasks(analysis)
    assert generated.tasks[0].dependencies == []
    assert "존재하지 않는 업무" in analysis.open_questions[0].content


def test_mock_provider_skips_only_invalid_date_matches():
    result = MockProvider().analyze_document(
        1,
        [
            {
                "id": 1,
                "block_order": 0,
                "block_type": "paragraph",
                "content": "잘못된 날짜 2026-13-45, 올바른 날짜 2026-08-31\n- 업무 2026-13-45",
            }
        ],
    )
    assert [item.date for item in result.fixed_dates] == [date(2026, 8, 31)]
    assert result.task_candidates[0].due_date is None


def test_project_deadline_conflict_includes_dates_and_sources():
    merged = merge_project_analyses(
        [
            DocumentAnalysis(
                document_summary="one",
                fixed_dates=[
                    FixedDateItem(content="출시", date=date(2026, 8, 10), source_block_id=1)
                ],
            ),
            DocumentAnalysis(
                document_summary="two",
                fixed_dates=[
                    FixedDateItem(content="완료", date=date(2026, 8, 20), source_block_id=2)
                ],
            ),
        ]
    )
    assert len(merged.conflicts) == 1
    assert "2026-08-10" in merged.conflicts[0].content
    assert "2026-08-20" in merged.conflicts[0].content
    assert "출처 블록" in merged.conflicts[0].content


def test_analysis_persists_milestone_acceptance_criteria_and_critical_status(
    client, project_payload, monkeypatch
):
    project = create_project(client, project_payload)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "milestone.md", "text": "출시 준비"},
    ).json()
    block_id = source["blocks"][0]["id"]

    class MilestoneProvider:
        name = "milestone-test"

        def analyze_document(self, source_id, blocks):
            return DocumentAnalysis(
                document_summary="summary",
                task_candidates=[
                    TaskCandidate(
                        title="출시 점검",
                        description="출시 전 점검",
                        milestone="MVP 출시",
                        due_date=date(2099, 1, 6),
                        acceptance_criteria=["체크리스트를 완료한다", "담당자가 승인한다"],
                        source_references=[SourceReference(source_id=source_id, block_id=block_id)],
                    )
                ],
            )

    monkeypatch.setattr("app.services.get_provider", lambda: MilestoneProvider())
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 202
    review = client.get(f"/api/projects/{project['id']}/analysis/review").json()
    task = review["tasks"][0]
    assert "완료 기준:" in task["description"]
    assert "체크리스트를 완료한다" in task["description"]
    milestones = client.get(f"/api/projects/{project['id']}/milestones").json()
    assert milestones == [
        {
            "id": task["milestone_id"],
            "project_id": project["id"],
            "title": "MVP 출시",
            "description": None,
            "target_date": "2099-01-06",
            "status": "planned",
            "sort_order": 0,
        }
    ]
    approved = client.post(
        f"/api/projects/{project['id']}/analysis/approve",
        json={"tasks": [{"id": task["id"], "action": "approve", "updates": {}}], "facts": []},
    )
    assert approved.status_code == 200
    pace = client.get(f"/api/projects/{project['id']}/pace?as_of=2099-01-07").json()
    assert pace["critical_milestone_failed"] is True
    assert pace["status"] == "critical"


def test_reanalysis_preserves_pending_ai_task_referenced_by_approved_task(client, project_payload, monkeypatch):
    project = create_project(client, project_payload)
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "protected.md", "text": "새 분석"},
    ).json()
    block_id = source["blocks"][0]["id"]
    with SessionLocal() as db:
        prerequisite = Task(
            project_id=project["id"],
            title="보호할 AI 업무",
            status="pending_review",
            ai_generated=True,
        )
        dependent = Task(project_id=project["id"], title="승인된 후속", status="approved")
        db.add_all([prerequisite, dependent])
        db.flush()
        db.add(TaskDependency(task_id=dependent.id, depends_on_task_id=prerequisite.id))
        db.commit()
        protected_id = prerequisite.id

    class ReplacementProvider:
        name = "replacement-test"

        def analyze_document(self, source_id, blocks):
            return DocumentAnalysis(
                document_summary="replacement",
                task_candidates=[
                    TaskCandidate(
                        title="대체 업무",
                        source_references=[SourceReference(source_id=source_id, block_id=block_id)],
                    )
                ],
            )

    monkeypatch.setattr("app.services.get_provider", lambda: ReplacementProvider())
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 202
    with SessionLocal() as db:
        assert db.get(Task, protected_id) is not None
