from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, selectinload

from ai.document_analyzer import get_provider
from ai.project_merger import merge_project_analyses
from ai.task_generator import generate_tasks
from scheduler.engine import ScheduleEngine
from scheduler.forecast import forecast_completion, required_daily_velocity
from scheduler.progress import pace_ratio, pace_status, planned_progress, weighted_progress

from .database import SessionLocal
from .models import (
    AnalysisRun,
    Milestone,
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
APPROVED_TASK_STATUSES = {"approved", "scheduled", "in_progress", "completed", "on_hold", "blocked"}
ANALYSIS_SEMAPHORE = threading.BoundedSemaphore(2)
MAX_ANALYSIS_CHARACTERS = 200_000


def project_or_none(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def task_query(project_id: int):
    return (
        select(Task)
        .where(Task.project_id == project_id)
        .options(selectinload(Task.dependencies), selectinload(Task.source_links))
        .order_by(Task.id)
    )


def leaf_task_query(project_id: int):
    child = aliased(Task)
    return task_query(project_id).where(
        ~select(child.id).where(child.parent_task_id == Task.id).exists()
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
        if task.status == "completed" or task.id in protected_ids:
            continue
        task_dates = dates.get(task.id, [])
        task.planned_start_date = min(task_dates) if task_dates else None
        task.planned_end_date = max(task_dates) if task_dates else None
        if task_dates:
            if task.status == "approved":
                task.status = "scheduled"
        elif task.status == "scheduled":
            task.status = "approved"


def _completed_dependency_ends(tasks: list[Task], start_date: date) -> dict[int, date]:
    return {
        task.id: task.actual_end_date or (start_date - date.resolution)
        for task in tasks
        if task.status == "completed"
    }


def _schedule_task_value(task: Task, *, remaining_only: bool = False) -> dict[str, Any]:
    remaining = task.estimated_hours
    if remaining_only and task.status == "in_progress":
        remaining = task.estimated_hours * (1 - task.progress_percent / 100)
    return {
        "id": task.id,
        "priority": task.priority,
        "due_date": task.due_date,
        "estimated_hours": remaining,
        "original_estimated_hours": task.estimated_hours,
        "remaining_estimated_hours": remaining,
        "dependency_ids": [dependency.depends_on_task_id for dependency in task.dependencies],
    }


def _save_schedule_version(
    db: Session,
    project_id: int,
    reason: str,
    snapshot: dict[str, Any],
    tasks: list[Task],
    *,
    protected_ids: set[int] | None = None,
) -> ScheduleVersion:
    for attempt in range(2):
        version = ScheduleVersion(
            project_id=project_id,
            version=next_schedule_version(db, project_id),
            reason=reason,
            schedule_snapshot=snapshot,
        )
        try:
            with db.begin_nested():
                db.add(version)
                db.flush()
        except IntegrityError:
            if attempt == 0:
                continue
            raise
        _update_task_plan(tasks, snapshot, protected_ids=protected_ids)
        db.commit()
        db.refresh(version)
        return version
    raise RuntimeError("schedule version allocation failed")


def generate_schedule(
    db: Session,
    project: Project,
    reason: str = "initial plan",
    *,
    as_of: date | None = None,
) -> ScheduleVersion:
    as_of = as_of or date.today()
    tasks = list(db.scalars(task_query(project.id)))
    leaf_tasks = list(db.scalars(leaf_task_query(project.id)))
    eligible = [task for task in leaf_tasks if task.status in SCHEDULABLE_STATUSES and task.status != "completed"]
    snapshot = ScheduleEngine().generate(
        [_schedule_task_value(task) for task in eligible],
        start_date=max(project.start_date, as_of),
        target_date=project.target_date,
        work_days=project.work_days,
        daily_capacity_hours=project.daily_capacity_hours,
        buffer_ratio=project.buffer_ratio,
        excluded_dates=_excluded(project),
        satisfied_dependency_ends=_completed_dependency_ends(leaf_tasks, project.start_date),
    )
    return _save_schedule_version(db, project.id, reason, snapshot, tasks)


def replan_schedule(db: Session, project: Project, reason: str, strategy: str = "redistribute") -> ScheduleVersion:
    latest = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.project_id == project.id)
        .order_by(ScheduleVersion.version.desc())
        .limit(1)
    )
    tasks = list(db.scalars(task_query(project.id)))
    leaf_tasks = list(db.scalars(leaf_task_query(project.id)))
    protected_ids = {
        task.id
        for task in leaf_tasks
        if task.status == "completed"
        or task.locked
        or task.status in {"meeting", "review"}
        or (task.actual_start_date is not None and task.priority in {"critical", "high"})
    }
    previous = latest.schedule_snapshot.get("placements", []) if latest else []
    previous_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for placement in previous:
        previous_by_task[int(placement["task_id"])].append(placement)
    reserved_task_ids = protected_ids & set(previous_by_task)
    reserved: list[dict[str, Any]] = []
    unsatisfied_reserved_ids: set[int] = set()
    task_by_id = {task.id: task for task in leaf_tasks}
    for task_id in sorted(reserved_task_ids):
        task = task_by_id[task_id]
        expected = task.estimated_hours
        if task.status == "in_progress":
            expected *= 1 - task.progress_percent / 100
        reserved_hours = sum(float(item["hours"]) for item in previous_by_task[task_id])
        satisfies_dependencies = task.status == "completed" or reserved_hours + 1e-9 >= expected
        if not satisfies_dependencies:
            unsatisfied_reserved_ids.add(task_id)
        for placement in previous_by_task[task_id]:
            reserved.append({
                **placement,
                "original_estimated_hours": task.estimated_hours,
                "remaining_estimated_hours": round(expected, 6),
                "satisfies_dependencies": satisfies_dependencies,
            })
    deferred_tasks = [
        task
        for task in leaf_tasks
        if strategy == "defer_low_priority"
        and task.priority == "low"
        and task.progress_percent <= 0
        and task.actual_start_date is None
        and task.status in SCHEDULABLE_STATUSES
        and task.id not in reserved_task_ids
    ]
    deferred_ids = {task.id for task in deferred_tasks}
    movable = [
        task
        for task in leaf_tasks
        if task.id not in reserved_task_ids
        and task.id not in deferred_ids
        and task.status in SCHEDULABLE_STATUSES
        and task.status != "completed"
    ]
    snapshot = ScheduleEngine().generate(
        [_schedule_task_value(task, remaining_only=True) for task in movable],
        start_date=max(project.start_date, date.today()),
        target_date=project.target_date,
        work_days=project.work_days,
        daily_capacity_hours=project.daily_capacity_hours,
        buffer_ratio=project.buffer_ratio,
        excluded_dates=_excluded(project),
        reserved_placements=reserved,
        satisfied_dependency_ends=_completed_dependency_ends(leaf_tasks, project.start_date),
        unsatisfied_dependency_ids=unsatisfied_reserved_ids,
    )
    snapshot["protected_task_ids"] = sorted(protected_ids)
    snapshot["deferred"] = [
        {"task_id": task.id, "reason": "low_priority_not_started"}
        for task in sorted(deferred_tasks, key=lambda item: item.id)
    ]
    return _save_schedule_version(db, project.id, reason, snapshot, tasks, protected_ids=reserved_task_ids)


def calculate_forecast(db: Session, project: Project, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    tasks = [
        task
        for task in db.scalars(leaf_task_query(project.id))
        if task.status not in {"pending_review", "extracted", "rejected"}
    ]
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
    tasks = [
        task
        for task in db.scalars(leaf_task_query(project.id))
        if task.status not in {"pending_review", "extracted", "rejected"}
    ]
    leaf_ids = {task.id for task in tasks}
    actual = weighted_progress(tasks)
    versions = list(
        db.scalars(
            select(ScheduleVersion)
            .where(ScheduleVersion.project_id == project.id)
            .order_by(ScheduleVersion.version, ScheduleVersion.id)
        )
    )
    baseline: list[dict[str, Any]] = []
    baseline_ids: set[int] = set()
    for index, version in enumerate(versions):
        version_placements = [
            placement
            for placement in version.schedule_snapshot.get("placements", [])
            if int(placement["task_id"]) in leaf_ids
        ]
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for placement in version_placements:
            grouped[int(placement["task_id"])].append(placement)
        if index == 0:
            baseline.extend(version_placements)
            baseline_ids = {
                int(task_id)
                for task_id in version.schedule_snapshot.get("task_ids", [])
                if int(task_id) in leaf_ids
            }
            if not baseline_ids:
                baseline_ids = set(grouped)
                baseline_ids.update(
                    int(item["task_id"])
                    for item in version.schedule_snapshot.get("unscheduled", [])
                    if int(item["task_id"]) in leaf_ids
                )
            continue
        for task_id in sorted(set(grouped) - baseline_ids):
            baseline.extend(grouped[task_id])
            baseline_ids.add(task_id)
    completed_ids = {task.id for task in tasks if task.status == "completed"}
    baseline = [item for item in baseline if int(item["task_id"]) not in completed_ids]
    for task in tasks:
        if task.status != "completed":
            continue
        completed_on = task.actual_end_date or task.planned_end_date
        if completed_on is not None:
            baseline.append({"task_id": task.id, "date": completed_on.isoformat(), "hours": task.estimated_hours})
    total = sum(task.estimated_hours for task in tasks)
    planned = planned_progress(baseline, total, as_of) if versions or completed_ids else 0.0
    ratio = pace_ratio(actual, planned)
    forecast = calculate_forecast(db, project, as_of)
    predicted = forecast.get("estimated_completion_date")
    delay_days = max(0, (date.fromisoformat(str(predicted)) - project.target_date).days) if predicted else 0
    critical_blocked = any(task.status == "blocked" and task.priority == "critical" for task in tasks)
    milestone_child = aliased(Task)
    critical_milestone_failed = db.scalar(
        select(Milestone.id)
        .join(Task, Task.milestone_id == Milestone.id)
        .where(
            Milestone.project_id == project.id,
            Milestone.target_date < as_of,
            Task.status.in_(sorted(APPROVED_TASK_STATUSES - {"completed"})),
            ~select(milestone_child.id).where(milestone_child.parent_task_id == Task.id).exists(),
        )
        .limit(1)
    ) is not None
    status = pace_status(
        ratio,
        delay_days,
        dependency_blocked=critical_blocked,
        critical_milestone_failed=critical_milestone_failed,
    )
    return {
        "actual_progress_percent": actual,
        "planned_progress_percent": planned,
        "pace_ratio": ratio,
        "status": status,
        "delay_days": delay_days,
        "critical_milestone_failed": critical_milestone_failed,
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


def review_gate_enabled() -> bool:
    """기본은 자동 승인. AI_REVIEW_GATE=1이면 SPEC 원칙 1의 검토 게이트를 복원한다."""
    return os.getenv("AI_REVIEW_GATE", "").strip().lower() in {"1", "true", "yes"}


def run_analysis(run_id: int, project_id: int) -> None:
    ANALYSIS_SEMAPHORE.acquire()
    db = SessionLocal()
    run = db.get(AnalysisRun, run_id)
    if run is None:
        db.close()
        ANALYSIS_SEMAPHORE.release()
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
        total_characters = sum(len(block.content) for source in sources for block in source.blocks)
        if total_characters > MAX_ANALYSIS_CHARACTERS:
            raise ValueError("문서가 너무 큽니다. 분할 업로드해 주세요")

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
        run.model_provider = getattr(provider, "last_provider_name", None) or provider.name
        merged = merge_project_analyses(analyses)
        generate_hierarchy = getattr(provider, "generate_hierarchical_tasks", None)
        generated = generate_hierarchy(merged) if callable(generate_hierarchy) else generate_tasks(merged)

        referenced_blocks: list[int] = []
        for field in FACT_FIELD_MAP:
            referenced_blocks.extend(item.source_block_id for item in getattr(merged, field))
        for monthly_item in generated.monthly:
            for weekly_item in monthly_item.weekly:
                for daily_item in weekly_item.daily:
                    for reference in daily_item.source_references:
                        if (reference.source_id, reference.block_id) not in valid_refs:
                            raise ValueError(f"존재하지 않는 source_block 참조: {reference.source_id}/{reference.block_id}")
                        referenced_blocks.append(reference.block_id)
        valid_block_ids = {block_id for _, block_id in valid_refs}
        if any(block_id not in valid_block_ids for block_id in referenced_blocks):
            raise ValueError("존재하지 않는 source_block_id가 포함되었습니다")

        db.execute(
            delete(ProjectFact).where(ProjectFact.project_id == project_id, ProjectFact.review_status == "pending_review")
        )
        protected_dependency_ids = set(
            db.scalars(
                select(TaskDependency.depends_on_task_id)
                .join(Task, Task.id == TaskDependency.task_id)
                .where(Task.project_id == project_id, Task.status.in_(sorted(APPROVED_TASK_STATUSES)))
            )
        )
        old_pending = list(
            db.scalars(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.ai_generated.is_(True),
                    Task.status.in_(["extracted", "pending_review"]),
                    Task.id.not_in(protected_dependency_ids),
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
                        review_status="pending_review" if review_gate_enabled() else "approved",
                        source_block_id=item.source_block_id,
                    )
                )
        milestone_by_title = {
            milestone.title.strip().casefold(): milestone
            for milestone in db.scalars(
                select(Milestone).where(Milestone.project_id == project_id).order_by(Milestone.id)
            )
        }
        title_to_task: dict[str, Task] = {}
        generated_status = "pending_review" if review_gate_enabled() else "approved"
        priority_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

        for monthly_item in generated.monthly:
            daily_items = [daily for weekly in monthly_item.weekly for daily in weekly.daily]
            first_daily = daily_items[0]
            due_dates = [daily.due_date for daily in daily_items if daily.due_date is not None]
            monthly_due_date = max(due_dates) if due_dates else None
            monthly_hours = sum(daily.estimated_hours for daily in daily_items)
            monthly_priority = max(
                (daily.priority for daily in daily_items),
                key=lambda value: priority_order.get(value, 1),
            )

            milestone_title = monthly_item.title.strip()
            milestone_key = milestone_title.casefold()
            milestone = milestone_by_title.get(milestone_key)
            if milestone is None:
                milestone = Milestone(
                    project_id=project_id,
                    title=milestone_title,
                    target_date=monthly_due_date,
                    sort_order=len(milestone_by_title),
                )
                db.add(milestone)
                db.flush()
                milestone_by_title[milestone_key] = milestone
            elif monthly_due_date and (milestone.target_date is None or monthly_due_date < milestone.target_date):
                milestone.target_date = monthly_due_date

            monthly_task = Task(
                project_id=project_id,
                milestone_id=milestone.id,
                cadence="monthly",
                title=monthly_item.title,
                description=monthly_item.description.strip(),
                status=generated_status,
                priority=monthly_priority,
                estimated_hours=monthly_hours,
                due_date=monthly_due_date,
                ai_generated=True,
                confidence=first_daily.confidence,
            )
            db.add(monthly_task)
            db.flush()
            first_reference = first_daily.source_references[0]
            db.add(
                TaskSourceLink(
                    task_id=monthly_task.id,
                    source_block_id=first_reference.block_id,
                    relevance_score=first_daily.confidence,
                )
            )

            for weekly_item in monthly_item.weekly:
                weekly_due_dates = [daily.due_date for daily in weekly_item.daily if daily.due_date is not None]
                weekly_due_date = max(weekly_due_dates) if weekly_due_dates else None
                weekly_hours = sum(daily.estimated_hours for daily in weekly_item.daily)
                weekly_priority = max(
                    (daily.priority for daily in weekly_item.daily),
                    key=lambda value: priority_order.get(value, 1),
                )
                weekly_first = weekly_item.daily[0]
                weekly_task = Task(
                    project_id=project_id,
                    milestone_id=milestone.id,
                    parent_task_id=monthly_task.id,
                    cadence="weekly",
                    title=weekly_item.title,
                    description=weekly_item.description.strip(),
                    status=generated_status,
                    priority=weekly_priority,
                    estimated_hours=weekly_hours,
                    due_date=weekly_due_date,
                    ai_generated=True,
                    confidence=weekly_first.confidence,
                )
                db.add(weekly_task)
                db.flush()
                db.add(
                    TaskSourceLink(
                        task_id=weekly_task.id,
                        source_block_id=weekly_first.source_references[0].block_id,
                        relevance_score=weekly_first.confidence,
                    )
                )

                for daily_item in weekly_item.daily:
                    daily_task = Task(
                        project_id=project_id,
                        milestone_id=milestone.id,
                        parent_task_id=weekly_task.id,
                        cadence="daily",
                        title=daily_item.title,
                        description=daily_item.description.strip(),
                        status=generated_status,
                        priority=daily_item.priority,
                        estimated_hours=daily_item.estimated_hours,
                        due_date=daily_item.due_date,
                        ai_generated=True,
                        confidence=daily_item.confidence,
                    )
                    db.add(daily_task)
                    db.flush()
                    title_to_task.setdefault(daily_item.title, daily_task)
                    for reference in daily_item.source_references:
                        db.add(
                            TaskSourceLink(
                                task_id=daily_task.id,
                                source_block_id=reference.block_id,
                                relevance_score=daily_item.confidence,
                            )
                        )

        for item in merged.task_candidates:
            task = title_to_task.get(item.title)
            if task is None:
                continue
            for dependency in item.dependencies:
                prerequisite = title_to_task.get(dependency)
                if prerequisite is not None:
                    db.add(TaskDependency(task_id=task.id, depends_on_task_id=prerequisite.id))
        run = db.get(AnalysisRun, run_id)
        if run is None:
            raise ValueError("analysis run disappeared")
        run.status = "completed"
        run.completed_at = utcnow()
        for source in sources:
            source.analysis_status = "review_required" if review_gate_enabled() else "completed"
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(AnalysisRun, run_id)
        if run is not None:
            run.status = "failed"
            summary = re.sub(r"(?:[A-Za-z]:\\\\|/)[^\s,;]+", "<path>", str(exc)).strip()
            run.error_message = f"{type(exc).__name__}: {summary}"[:200]
            run.completed_at = utcnow()
        for source in db.scalars(select(SourceDocument).where(SourceDocument.project_id == project_id)):
            if source.analysis_status == "analyzing":
                source.analysis_status = "failed"
                source.error_message = "문서 분석에 실패했습니다. 내용을 확인한 뒤 다시 시도해 주세요"
        db.commit()
    finally:
        db.close()
        ANALYSIS_SEMAPHORE.release()


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
