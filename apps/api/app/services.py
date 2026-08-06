from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ai.document_analyzer import get_provider
from ai.project_merger import merge_project_analyses
from ai.task_generator import generate_tasks
from scheduler.engine import ScheduleEngine
from scheduler.forecast import forecast_completion, required_daily_velocity
from scheduler.progress import pace_ratio, pace_status, planned_progress, weighted_progress

from .database import SessionLocal
from .models import (
    AnalysisRun,
    Project,
    ProjectFact,
    ScheduleVersion,
    SourceBlock,
    SourceDocument,
    Task,
    TaskDependency,
    TaskEvent,
    TaskSourceLink,
    utcnow,
)


SCHEDULABLE_STATUSES = {"approved", "scheduled", "in_progress", "blocked"}


def project_or_none(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def task_query(project_id: int):
    return (
        select(Task)
        .where(Task.project_id == project_id)
        .options(selectinload(Task.dependencies), selectinload(Task.source_links))
        .order_by(Task.id)
    )


def next_schedule_version(db: Session, project_id: int) -> int:
    current = db.scalar(select(func.max(ScheduleVersion.version)).where(ScheduleVersion.project_id == project_id))
    return int(current or 0) + 1


def _excluded(project: Project) -> set[date]:
    return {date.fromisoformat(value) if isinstance(value, str) else value for value in (project.excluded_dates or [])}


def _update_task_plan(tasks: list[Task], snapshot: dict[str, Any], *, protected_ids: set[int] | None = None) -> None:
    protected_ids = protected_ids or set()
    dates: dict[int, list[date]] = defaultdict(list)
    for placement in snapshot["placements"]:
        dates[int(placement["task_id"])].append(date.fromisoformat(str(placement["date"])))
    unscheduled_ids = {int(item["task_id"]) for item in snapshot["unscheduled"]}
    for task in tasks:
        if task.id in protected_ids:
            continue
        task_dates = dates.get(task.id, [])
        task.planned_start_date = min(task_dates) if task_dates else None
        task.planned_end_date = max(task_dates) if task_dates else None
        if task.id not in unscheduled_ids and task_dates and task.status == "approved":
            task.status = "scheduled"


def generate_schedule(db: Session, project: Project, reason: str = "initial plan") -> ScheduleVersion:
    tasks = list(db.scalars(task_query(project.id)))
    eligible = [task for task in tasks if task.status in SCHEDULABLE_STATUSES and task.status != "completed"]
    snapshot = ScheduleEngine().generate(
        eligible,
        start_date=project.start_date,
        target_date=project.target_date,
        work_days=project.work_days,
        daily_capacity_hours=project.daily_capacity_hours,
        buffer_ratio=project.buffer_ratio,
        excluded_dates=_excluded(project),
    )
    version = ScheduleVersion(
        project_id=project.id,
        version=next_schedule_version(db, project.id),
        reason=reason,
        schedule_snapshot=snapshot,
    )
    db.add(version)
    _update_task_plan(tasks, snapshot)
    db.commit()
    db.refresh(version)
    return version


def replan_schedule(db: Session, project: Project, reason: str) -> ScheduleVersion:
    latest = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.project_id == project.id)
        .order_by(ScheduleVersion.version.desc())
        .limit(1)
    )
    tasks = list(db.scalars(task_query(project.id)))
    protected_ids = {
        task.id
        for task in tasks
        if task.status == "completed"
        or task.locked
        or task.due_date is not None
        or task.status in {"meeting", "review"}
        or (task.actual_start_date is not None and task.priority in {"critical", "high"})
    }
    previous = latest.schedule_snapshot.get("placements", []) if latest else []
    reserved = [placement for placement in previous if int(placement["task_id"]) in protected_ids]
    movable = [
        task
        for task in tasks
        if task.id not in protected_ids and task.status in SCHEDULABLE_STATUSES and task.status != "completed"
    ]
    snapshot = ScheduleEngine().generate(
        movable,
        start_date=max(project.start_date, date.today()),
        target_date=project.target_date,
        work_days=project.work_days,
        daily_capacity_hours=project.daily_capacity_hours,
        buffer_ratio=project.buffer_ratio,
        excluded_dates=_excluded(project),
        reserved_placements=reserved,
    )
    snapshot["protected_task_ids"] = sorted(protected_ids)
    version = ScheduleVersion(
        project_id=project.id,
        version=next_schedule_version(db, project.id),
        reason=reason,
        schedule_snapshot=snapshot,
    )
    db.add(version)
    _update_task_plan(tasks, snapshot, protected_ids=protected_ids)
    db.commit()
    db.refresh(version)
    return version


def calculate_forecast(db: Session, project: Project, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    tasks = [task for task in db.scalars(task_query(project.id)) if task.status not in {"pending_review", "extracted", "rejected"}]
    remaining = sum(task.estimated_hours * (1 - task.progress_percent / 100) for task in tasks)
    logs: dict[date, float] = defaultdict(float)
    for task in tasks:
        if task.actual_end_date:
            logs[task.actual_end_date] += task.actual_hours if task.actual_hours > 0 else task.estimated_hours
    result = forecast_completion(remaining, logs, as_of, project.work_days, _excluded(project))
    result["remaining_hours"] = round(remaining, 2)
    result["required_daily_hours"] = required_daily_velocity(
        remaining, as_of, project.target_date, project.work_days, _excluded(project)
    )
    return result


def calculate_pace(db: Session, project: Project, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    tasks = [task for task in db.scalars(task_query(project.id)) if task.status not in {"pending_review", "extracted", "rejected"}]
    actual = weighted_progress(tasks)
    latest = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.project_id == project.id)
        .order_by(ScheduleVersion.version.desc())
        .limit(1)
    )
    placements = latest.schedule_snapshot.get("placements", []) if latest else []
    total = sum(task.estimated_hours for task in tasks)
    planned = planned_progress(placements, total, as_of) if latest else 0.0
    ratio = pace_ratio(actual, planned)
    forecast = calculate_forecast(db, project, as_of)
    predicted = forecast.get("estimated_completion_date")
    delay_days = max(0, (date.fromisoformat(str(predicted)) - project.target_date).days) if predicted else 0
    critical_blocked = any(task.status == "blocked" and task.priority == "critical" for task in tasks)
    status = pace_status(ratio, delay_days, dependency_blocked=critical_blocked)
    return {
        "actual_progress_percent": actual,
        "planned_progress_percent": planned,
        "pace_ratio": ratio,
        "status": status,
        "delay_days": delay_days,
        "target_date": project.target_date.isoformat(),
        "forecast": forecast,
    }


FACT_FIELD_MAP = {
    "goals": "goal",
    "deliverables": "deliverable",
    "fixed_dates": "deadline",
    "requirements": "requirement",
    "risks": "risk",
    "open_questions": "open_question",
    "conflicts": "constraint",
}


def run_analysis(run_id: int, project_id: int) -> None:
    db = SessionLocal()
    run = db.get(AnalysisRun, run_id)
    if run is None:
        db.close()
        return
    try:
        provider = get_provider()
        run.status = "running"
        run.model_provider = provider.name
        sources = list(
            db.scalars(
                select(SourceDocument)
                .where(SourceDocument.project_id == project_id, SourceDocument.blocks.any())
                .options(selectinload(SourceDocument.blocks))
                .order_by(SourceDocument.id)
            )
        )
        if not sources:
            raise ValueError("분석할 수 있는 source가 없습니다")
        for source in sources:
            source.analysis_status = "analyzing"
        db.commit()

        analyses = []
        valid_refs: set[tuple[int, int]] = set()
        for source in sources:
            blocks = []
            for block in source.blocks:
                valid_refs.add((source.id, block.id))
                blocks.append(
                    {
                        "id": block.id,
                        "block_type": block.block_type,
                        "content": block.content,
                        "block_order": block.block_order,
                        "page_number": block.page_number,
                        "sheet_name": block.sheet_name,
                        "section_title": block.section_title,
                    }
                )
            analyses.append(provider.analyze_document(source.id, blocks))
        merged = merge_project_analyses(analyses)
        generated = generate_tasks(merged)

        referenced_blocks: list[int] = []
        for field in FACT_FIELD_MAP:
            referenced_blocks.extend(item.source_block_id for item in getattr(merged, field))
        for task in generated.tasks:
            for reference in task.source_references:
                if (reference.source_id, reference.block_id) not in valid_refs:
                    raise ValueError(f"존재하지 않는 source_block 참조: {reference.source_id}/{reference.block_id}")
                referenced_blocks.append(reference.block_id)
        valid_block_ids = {block_id for _, block_id in valid_refs}
        if any(block_id not in valid_block_ids for block_id in referenced_blocks):
            raise ValueError("존재하지 않는 source_block_id가 포함되었습니다")

        db.execute(
            delete(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.review_status == "pending_review")
        )
        old_pending = list(
            db.scalars(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.ai_generated.is_(True),
                    Task.status.in_(["extracted", "pending_review"]),
                )
            )
        )
        for task in old_pending:
            db.delete(task)

        for field, fact_type in FACT_FIELD_MAP.items():
            for item in getattr(merged, field):
                content = item.content
                if field == "fixed_dates":
                    content = f"{item.content}: {item.date.isoformat()}"
                db.add(
                    ProjectFact(
                        project_id=project_id,
                        fact_type=fact_type,
                        content=content,
                        confidence=item.confidence,
                        review_status="pending_review",
                        source_block_id=item.source_block_id,
                    )
                )
        title_to_task: dict[str, Task] = {}
        for item in generated.tasks:
            task = Task(
                project_id=project_id,
                title=item.title,
                description=item.description,
                status="pending_review",
                priority=item.priority if item.priority in {"critical", "high", "medium", "low"} else "medium",
                estimated_hours=item.estimated_hours,
                due_date=item.due_date,
                ai_generated=True,
                confidence=item.confidence,
            )
            db.add(task)
            db.flush()
            title_to_task[item.title] = task
            for reference in item.source_references:
                db.add(TaskSourceLink(task_id=task.id, source_block_id=reference.block_id, relevance_score=item.confidence))
        for item in generated.tasks:
            for dependency in item.dependencies:
                db.add(TaskDependency(task_id=title_to_task[item.title].id, depends_on_task_id=title_to_task[dependency].id))
        run = db.get(AnalysisRun, run_id)
        if run is None:
            raise ValueError("analysis run disappeared")
        run.status = "completed"
        run.completed_at = utcnow()
        for source in sources:
            source.analysis_status = "review_required"
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(AnalysisRun, run_id)
        if run is not None:
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = utcnow()
        for source in db.scalars(select(SourceDocument).where(SourceDocument.project_id == project_id)):
            if source.analysis_status == "analyzing":
                source.analysis_status = "failed"
                source.error_message = "문서 분석에 실패했습니다. 내용을 확인한 뒤 다시 시도해 주세요"
        db.commit()
    finally:
        db.close()


def record_task_event(
    db: Session,
    task: Task,
    event_type: str,
    previous: Any,
    new: Any,
    note: str | None = None,
    *,
    event_date: date | None = None,
) -> TaskEvent:
    event = TaskEvent(
        task_id=task.id,
        event_type=event_type,
        previous_value=json.dumps(previous, ensure_ascii=False, default=str),
        new_value=json.dumps(new, ensure_ascii=False, default=str),
        note=note,
    )
    if event_date:
        event.created_at = datetime.combine(event_date, datetime.min.time(), tzinfo=timezone.utc)
    db.add(event)
    return event
