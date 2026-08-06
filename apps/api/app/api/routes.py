from __future__ import annotations

import os
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from parsers import UnsupportedFileTypeError, parse_document
from scheduler.capacity import capacity_warning
from scheduler.dependencies import DependencyCycleError, assert_acyclic

from ..database import get_db
from ..models import (
    AnalysisRun,
    Project,
    ProjectFact,
    ScheduleVersion,
    SourceBlock,
    SourceDocument,
    Task,
    TaskDependency,
    TaskSourceLink,
)
from ..schemas import (
    ApprovalRequest,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    ReplanRequest,
    ScheduleGenerate,
    SourceDocumentOut,
    TaskBlock,
    TaskComplete,
    TaskCreate,
    TaskOut,
    TaskPatch,
)
from ..services import (
    calculate_forecast,
    calculate_pace,
    generate_schedule,
    project_or_none,
    record_task_event,
    replan_schedule,
    run_analysis,
    task_query,
)


router = APIRouter(prefix="/api")
ALLOWED_EXTENSIONS = {"pdf", "docx", "xlsx", "txt", "md", "hwpx"}
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", str(20 * 1024 * 1024)))
STORAGE_ROOT = Path(os.getenv("STORAGE_PATH", "storage"))


def _project(db: Session, project_id: int) -> Project:
    project = project_or_none(db, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _task(db: Session, task_id: int) -> Task:
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.dependencies), selectinload(Task.source_links))
    )
    if task is None:
        raise HTTPException(404, "task not found")
    return task


def _source_query(project_id: int):
    return (
        select(SourceDocument)
        .where(SourceDocument.project_id == project_id)
        .options(selectinload(SourceDocument.blocks))
        .order_by(SourceDocument.id)
    )


def _version_dict(version: ScheduleVersion, include_snapshot: bool = True) -> dict[str, Any]:
    result = {
        "id": version.id,
        "project_id": version.project_id,
        "version": version.version,
        "reason": version.reason,
        "created_at": version.created_at,
    }
    if include_snapshot:
        result["schedule_snapshot"] = version.schedule_snapshot
    return result


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    values = payload.model_dump()
    values["excluded_dates"] = [value.isoformat() for value in payload.excluded_dates]
    project = Project(**values)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return list(db.scalars(select(Project).order_by(Project.id)))


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _project(db, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectPatch, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    values = payload.model_dump(exclude_unset=True)
    required_fields = {"name", "start_date", "target_date", "work_days", "daily_capacity_hours", "buffer_ratio", "status"}
    if any(values.get(field) is None for field in required_fields if field in values):
        raise HTTPException(422, "required project fields cannot be null")
    if "excluded_dates" in values and values["excluded_dates"] is not None:
        values["excluded_dates"] = [value.isoformat() for value in values["excluded_dates"]]
    for key, value in values.items():
        setattr(project, key, value)
    if project.target_date < project.start_date:
        raise HTTPException(422, "target_date must be on or after start_date")
    if not project.work_days or any(day < 0 or day > 6 for day in project.work_days):
        raise HTTPException(422, "invalid work_days")
    project.work_days = sorted(set(project.work_days))
    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    paths = [source.storage_path for source in project.sources if source.storage_path]
    db.delete(project)
    db.commit()
    for path_value in paths:
        path = Path(path_value)
        if path.is_file() and STORAGE_ROOT.resolve() in path.resolve().parents:
            path.unlink(missing_ok=True)
    return Response(status_code=204)


@router.post("/projects/{project_id}/sources", response_model=SourceDocumentOut, status_code=201)
async def create_source(project_id: int, request: Request, db: Session = Depends(get_db)):
    _project(db, project_id)
    content_type = request.headers.get("content-type", "")
    file_name: str | None = None
    raw: bytes
    file_type: str
    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded = form.get("file")
        text_value = form.get("text")
        if uploaded is not None and hasattr(uploaded, "read"):
            file_name = Path(str(getattr(uploaded, "filename", "upload"))).name
            raw = await uploaded.read()
            file_type = Path(file_name).suffix.lower().lstrip(".")
        elif text_value is not None:
            file_name = Path(str(form.get("file_name") or "direct-input.txt")).name
            raw = str(text_value).encode("utf-8")
            file_type = "md" if file_name.lower().endswith(".md") else "txt"
        else:
            raise HTTPException(400, "file 또는 text가 필요합니다")
    elif "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise HTTPException(400, "text가 필요합니다")
        file_name = Path(str(payload.get("file_name") or "direct-input.txt")).name
        raw = payload["text"].encode("utf-8")
        file_type = "md" if file_name.lower().endswith(".md") else "txt"
    else:
        raw = await request.body()
        file_name = "direct-input.txt"
        file_type = "txt"
    if file_type not in ALLOWED_EXTENSIONS:
        message = "HWPX 또는 PDF로 변환해 다시 업로드해 주세요" if file_type == "hwp" else "지원하지 않는 파일 형식입니다"
        raise HTTPException(415, message)
    if len(raw) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다")
    if not raw:
        raise HTTPException(400, "빈 파일은 업로드할 수 없습니다")

    project_dir = STORAGE_ROOT / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{file_name}"
    storage_path = project_dir / stored_name
    storage_path.write_bytes(raw)
    source = SourceDocument(
        project_id=project_id,
        file_name=file_name,
        file_type=file_type,
        storage_path=str(storage_path),
        analysis_status="extracting",
    )
    db.add(source)
    db.flush()
    try:
        blocks = parse_document(storage_path, file_type)
        source.extracted_text = "\n\n".join(block["content"] for block in blocks)
        for block in blocks:
            db.add(SourceBlock(source_document_id=source.id, **block))
        source.analysis_status = "text_extracted"
    except Exception as exc:
        source.analysis_status = "failed"
        if isinstance(exc, UnsupportedFileTypeError):
            source.error_message = str(exc)
        else:
            source.error_message = f"문서 파싱에 실패했습니다: {exc}. 파일을 확인하거나 다른 형식으로 변환해 주세요"
    db.commit()
    return db.scalar(_source_query(project_id).where(SourceDocument.id == source.id))


@router.get("/projects/{project_id}/sources", response_model=list[SourceDocumentOut])
def list_sources(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return list(db.scalars(_source_query(project_id)))


@router.get("/sources/{source_id}", response_model=SourceDocumentOut)
def get_source(source_id: int, db: Session = Depends(get_db)):
    source = db.scalar(
        select(SourceDocument).where(SourceDocument.id == source_id).options(selectinload(SourceDocument.blocks))
    )
    if source is None:
        raise HTTPException(404, "source not found")
    return source


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int, db: Session = Depends(get_db)):
    source = db.get(SourceDocument, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    path = Path(source.storage_path) if source.storage_path else None
    db.delete(source)
    db.commit()
    if path and path.is_file() and STORAGE_ROOT.resolve() in path.resolve().parents:
        path.unlink(missing_ok=True)
    return Response(status_code=204)


@router.post("/projects/{project_id}/analysis", status_code=202)
def start_analysis(project_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _project(db, project_id)
    active = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project_id, AnalysisRun.status.in_(["queued", "running"]))
        .order_by(AnalysisRun.id.desc())
    )
    if active:
        raise HTTPException(409, "analysis already running")
    run = AnalysisRun(project_id=project_id, status="queued", model_provider="pending", prompt_version="v1")
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(run_analysis, run.id, project_id)
    return {"run_id": run.id, "status": run.status}


@router.get("/projects/{project_id}/analysis/status")
def analysis_status(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.project_id == project_id).order_by(AnalysisRun.id.desc()))
    if run is None:
        return {"status": "not_started"}
    return {
        "run_id": run.id,
        "status": run.status,
        "model_provider": run.model_provider,
        "prompt_version": run.prompt_version,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_message": run.error_message,
    }


@router.get("/projects/{project_id}/analysis/review")
def analysis_review(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    facts = list(db.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id).order_by(ProjectFact.id)))
    tasks = list(db.scalars(task_query(project_id)))
    referenced_ids = {fact.source_block_id for fact in facts if fact.source_block_id is not None}
    referenced_ids.update(link.source_block_id for task in tasks if task.ai_generated for link in task.source_links)
    blocks = list(db.scalars(select(SourceBlock).where(SourceBlock.id.in_(referenced_ids)).order_by(SourceBlock.id))) if referenced_ids else []
    return {
        "facts": [
            {
                "id": fact.id,
                "fact_type": fact.fact_type,
                "content": fact.content,
                "confidence": fact.confidence,
                "review_status": fact.review_status,
                "source_block_id": fact.source_block_id,
            }
            for fact in facts
        ],
        "tasks": [TaskOut.model_validate(task).model_dump(mode="json") for task in tasks if task.ai_generated],
        "source_blocks": [
            {
                "id": block.id,
                "source_document_id": block.source_document_id,
                "block_type": block.block_type,
                "page_number": block.page_number,
                "sheet_name": block.sheet_name,
                "section_title": block.section_title,
                "content": block.content,
                "block_order": block.block_order,
                "location_metadata": block.location_metadata,
            }
            for block in blocks
        ],
    }


@router.post("/projects/{project_id}/analysis/approve")
def approve_analysis(project_id: int, payload: ApprovalRequest, db: Session = Depends(get_db)):
    _project(db, project_id)
    changed = {"tasks": 0, "facts": 0}
    for decision in payload.tasks:
        task = db.get(Task, decision.id)
        if task is None or task.project_id != project_id or not task.ai_generated:
            raise HTTPException(404, f"review task not found: {decision.id}")
        if decision.action == "reject":
            task.status = "rejected"
        elif decision.action == "hold":
            task.status = "pending_review"
        else:
            try:
                updates = TaskPatch.model_validate(decision.updates).model_dump(exclude_unset=True)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            required_fields = {"title", "status", "priority", "estimated_hours", "actual_hours", "progress_percent", "locked"}
            if any(updates.get(field) is None for field in required_fields if field in updates):
                raise HTTPException(422, "required task fields cannot be null")
            dependency_ids = updates.pop("dependency_ids", None)
            for key, value in updates.items():
                setattr(task, key, value)
            if dependency_ids is not None:
                dependency_ids = sorted(set(dependency_ids))
                found = set(
                    db.scalars(
                        select(Task.id).where(Task.project_id == project_id, Task.id.in_(dependency_ids))
                    )
                )
                if found != set(dependency_ids) or task.id in dependency_ids:
                    raise HTTPException(422, "invalid dependency_ids")
                task.dependencies.clear()
                db.flush()
                for dependency_id in dependency_ids:
                    db.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency_id))
            task.status = "approved"
        changed["tasks"] += 1
    for decision in payload.facts:
        fact = db.get(ProjectFact, decision.id)
        if fact is None or fact.project_id != project_id:
            raise HTTPException(404, f"review fact not found: {decision.id}")
        if decision.action == "reject":
            fact.review_status = "rejected"
        elif decision.action == "hold":
            fact.review_status = "pending_review"
        else:
            if "content" in decision.updates:
                fact.content = str(decision.updates["content"])
            if "confidence" in decision.updates:
                confidence = float(decision.updates["confidence"])
                if not 0 <= confidence <= 1:
                    raise HTTPException(422, "confidence must be between 0 and 1")
                fact.confidence = confidence
            fact.review_status = "approved"
        changed["facts"] += 1
    try:
        db.flush()
        _assert_project_dependencies(db, project_id)
    except HTTPException:
        db.rollback()
        raise
    pending_tasks = db.scalar(
        select(Task.id).where(
            Task.project_id == project_id,
            Task.ai_generated.is_(True),
            Task.status == "pending_review",
        ).limit(1)
    )
    pending_facts = db.scalar(
        select(ProjectFact.id).where(
            ProjectFact.project_id == project_id,
            ProjectFact.review_status == "pending_review",
        ).limit(1)
    )
    if pending_tasks is None and pending_facts is None:
        for source in db.scalars(select(SourceDocument).where(SourceDocument.project_id == project_id)):
            if source.analysis_status == "review_required":
                source.analysis_status = "completed"
    db.commit()
    return {"status": "applied", **changed}


def _validate_task_references(db: Session, project_id: int, payload: TaskCreate) -> None:
    related_ids = set(payload.dependency_ids)
    if payload.parent_task_id:
        related_ids.add(payload.parent_task_id)
    if related_ids:
        found = set(db.scalars(select(Task.id).where(Task.project_id == project_id, Task.id.in_(related_ids))))
        if found != related_ids:
            raise HTTPException(422, "dependency or parent task does not belong to project")
    if payload.milestone_id:
        from ..models import Milestone

        milestone = db.get(Milestone, payload.milestone_id)
        if milestone is None or milestone.project_id != project_id:
            raise HTTPException(422, "milestone does not belong to project")
    if payload.source_block_ids:
        found_blocks = set(db.scalars(select(SourceBlock.id).where(SourceBlock.id.in_(payload.source_block_ids))))
        if found_blocks != set(payload.source_block_ids):
            raise HTTPException(422, "source_block does not exist")


def _assert_project_dependencies(db: Session, project_id: int) -> None:
    ids = list(db.scalars(select(Task.id).where(Task.project_id == project_id)))
    edges = list(
        db.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            .join(Task, Task.id == TaskDependency.task_id)
            .where(Task.project_id == project_id)
        ).tuples()
    )
    try:
        assert_acyclic(ids, edges)
    except DependencyCycleError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(project_id: int, payload: TaskCreate, db: Session = Depends(get_db)):
    _project(db, project_id)
    _validate_task_references(db, project_id, payload)
    values = payload.model_dump(exclude={"dependency_ids", "source_block_ids"})
    task = Task(project_id=project_id, **values)
    db.add(task)
    db.flush()
    for dependency_id in sorted(set(payload.dependency_ids)):
        if dependency_id == task.id:
            db.rollback()
            raise HTTPException(422, "task cannot depend on itself")
        db.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency_id))
    for block_id in sorted(set(payload.source_block_ids)):
        db.add(TaskSourceLink(task_id=task.id, source_block_id=block_id, relevance_score=1.0))
    try:
        db.flush()
        _assert_project_dependencies(db, project_id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    return _task(db, task.id)


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return list(db.scalars(task_query(project_id)))


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    return _task(db, task_id)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskPatch, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    values = payload.model_dump(exclude_unset=True)
    required_fields = {"title", "status", "priority", "estimated_hours", "actual_hours", "progress_percent", "locked"}
    if any(values.get(field) is None for field in required_fields if field in values):
        raise HTTPException(422, "required task fields cannot be null")
    dependency_ids = values.pop("dependency_ids", None)
    if dependency_ids is not None:
        found = set(db.scalars(select(Task.id).where(Task.project_id == task.project_id, Task.id.in_(dependency_ids))))
        if found != set(dependency_ids) or task.id in dependency_ids:
            raise HTTPException(422, "invalid dependency_ids")
        task.dependencies.clear()
        db.flush()
        for dependency_id in sorted(set(dependency_ids)):
            db.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency_id))
    previous = {key: getattr(task, key) for key in values}
    for key, value in values.items():
        setattr(task, key, value)
    try:
        db.flush()
        _assert_project_dependencies(db, task.project_id)
        record_task_event(db, task, "updated", previous, values)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    return _task(db, task.id)


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int, payload: TaskComplete | None = None, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    payload = payload or TaskComplete()
    previous = {"status": task.status, "progress_percent": task.progress_percent, "actual_hours": task.actual_hours}
    completed_date = payload.completed_date or date.today()
    task.status = "completed"
    task.progress_percent = 100
    task.actual_hours = payload.actual_hours if payload.actual_hours is not None else (task.actual_hours or task.estimated_hours)
    task.actual_start_date = task.actual_start_date or completed_date
    task.actual_end_date = completed_date
    record_task_event(
        db,
        task,
        "completed",
        previous,
        {"status": "completed", "progress_percent": 100, "actual_hours": task.actual_hours},
        payload.note,
        event_date=completed_date,
    )
    db.commit()
    return _task(db, task.id)


@router.post("/tasks/{task_id}/block", response_model=TaskOut)
def block_task(task_id: int, payload: TaskBlock, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    previous = task.status
    task.status = "blocked"
    record_task_event(db, task, "blocked", previous, "blocked", payload.reason)
    db.commit()
    return _task(db, task.id)


@router.post("/projects/{project_id}/schedule/generate", status_code=201)
def schedule_generate(project_id: int, payload: ScheduleGenerate | None = None, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    version = generate_schedule(db, project, (payload or ScheduleGenerate()).reason)
    return _version_dict(version)


@router.get("/projects/{project_id}/schedule")
def get_schedule(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    version = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.project_id == project_id)
        .order_by(ScheduleVersion.version.desc())
    )
    if version is None:
        return {"version": None, "schedule_snapshot": None}
    return _version_dict(version)


@router.post("/projects/{project_id}/schedule/replan", status_code=201)
def schedule_replan(project_id: int, payload: ReplanRequest, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    if payload.strategy == "increase_capacity":
        if payload.daily_capacity_hours is None:
            raise HTTPException(422, "daily_capacity_hours is required")
        project.daily_capacity_hours = payload.daily_capacity_hours
    elif payload.strategy == "change_target":
        if payload.target_date is None or payload.target_date < project.start_date:
            raise HTTPException(422, "valid target_date is required")
        project.target_date = payload.target_date
    version = replan_schedule(db, project, payload.reason)
    return _version_dict(version)


@router.get("/projects/{project_id}/schedule/versions")
def list_schedule_versions(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    versions = list(
        db.scalars(
            select(ScheduleVersion)
            .where(ScheduleVersion.project_id == project_id)
            .order_by(ScheduleVersion.version)
        )
    )
    return [_version_dict(version, include_snapshot=False) for version in versions]


@router.get("/projects/{project_id}/schedule/versions/compare")
def compare_schedule_versions(project_id: int, from_version: int, to_version: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    versions = list(
        db.scalars(
            select(ScheduleVersion).where(
                ScheduleVersion.project_id == project_id,
                ScheduleVersion.version.in_([from_version, to_version]),
            )
        )
    )
    by_number = {item.version: item for item in versions}
    if from_version not in by_number or to_version not in by_number:
        raise HTTPException(404, "schedule version not found")

    def task_map(version: ScheduleVersion):
        result: dict[int, list[tuple[str, float]]] = defaultdict(list)
        for item in version.schedule_snapshot.get("placements", []):
            result[int(item["task_id"])].append((str(item["date"]), float(item["hours"])))
        return {key: sorted(value) for key, value in result.items()}

    before, after = task_map(by_number[from_version]), task_map(by_number[to_version])
    changed = [
        {"task_id": task_id, "before": before.get(task_id, []), "after": after.get(task_id, [])}
        for task_id in sorted(set(before) | set(after))
        if before.get(task_id) != after.get(task_id)
    ]
    return {"from_version": from_version, "to_version": to_version, "changes": changed}


@router.get("/projects/{project_id}/schedule/versions/{version_number}")
def get_schedule_version(project_id: int, version_number: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    version = db.scalar(
        select(ScheduleVersion).where(
            ScheduleVersion.project_id == project_id, ScheduleVersion.version == version_number
        )
    )
    if version is None:
        raise HTTPException(404, "schedule version not found")
    return _version_dict(version)


@router.get("/projects/{project_id}/pace")
def get_pace(project_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    return calculate_pace(db, _project(db, project_id), as_of)


@router.get("/projects/{project_id}/forecast")
def get_forecast(project_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    return calculate_forecast(db, _project(db, project_id), as_of)


@router.get("/projects/{project_id}/dashboard")
def get_dashboard(project_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    as_of = as_of or date.today()
    pace = calculate_pace(db, project, as_of)
    version = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.project_id == project_id)
        .order_by(ScheduleVersion.version.desc())
    )
    placements = version.schedule_snapshot.get("placements", []) if version else []
    today_items = [item for item in placements if item["date"] == as_of.isoformat()]
    tasks = list(db.scalars(task_query(project_id)))
    task_map = {task.id: task for task in tasks}
    assigned = sum(float(item["hours"]) for item in today_items)
    warning = capacity_warning(assigned, project.daily_capacity_hours)
    return {
        "project_id": project_id,
        "as_of": as_of.isoformat(),
        "pace": pace,
        "today": {
            "available_hours": project.daily_capacity_hours,
            "assigned_hours": round(assigned, 2),
            **warning,
            "items": [
                {
                    **item,
                    "title": task_map[int(item["task_id"])].title if int(item["task_id"]) in task_map else None,
                    "status": task_map[int(item["task_id"])].status if int(item["task_id"]) in task_map else None,
                }
                for item in today_items
            ],
        },
        "blocked_task_ids": sorted(task.id for task in tasks if task.status == "blocked"),
    }
