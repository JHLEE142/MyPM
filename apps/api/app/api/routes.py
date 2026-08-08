from __future__ import annotations

import json
import os
import threading
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from parsers import IMAGE_EXTENSIONS, IMAGE_MIME, UnsupportedFileTypeError, parse_document
from parsers.url_fetcher import UrlFetchError, fetch_url_blocks
from scheduler.capacity import capacity_warning
from scheduler.dependencies import DependencyCycleError, assert_acyclic
from ai.drafts import calculate_completeness, merge_draft_fields, missing_required_fields
from ai.cli_providers import ProviderError
from ai.document_analyzer import build_draft_prompt
from ai.openai_provider import describe_image_safely
from ai.router import AiRouter, get_ai_router, router_status
from ai.schemas import DraftFields

from ..database import get_db
from ..reports import build_daily_report, render_report_text
from ..models import (
    AnalysisRun,
    ProjectDraft,
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
)
from ..schemas import (
    ApprovalRequest,
    DraftChatRequest,
    DraftChatResponse,
    FactReviewUpdate,
    MilestoneOut,
    ProjectCreate,
    ProjectDraftOut,
    ProjectDraftPatch,
    ProjectOut,
    ProjectPatch,
    ReplanRequest,
    RouterStatus,
    ScheduleGenerate,
    SourceDocumentOut,
    TaskBlock,
    TaskComplete,
    TaskCreate,
    TaskOut,
    TaskMoveRequest,
    TaskPatch,
    TaskReorderRequest,
    TaskUpdateApplyRequest,
    TaskUpdatePreviewRequest,
)
from ..services import (
    calculate_forecast,
    calculate_pace,
    generate_schedule,
    leaf_task_query,
    project_or_none,
    record_task_event,
    replan_schedule,
    run_analysis,
    task_query,
)


router = APIRouter(prefix="/api")
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", str(20 * 1024 * 1024)))
STORAGE_ROOT = Path(os.getenv("STORAGE_PATH", "storage"))
UPLOAD_CHUNK_SIZE = 1024 * 1024
APPROVED_TASK_STATUSES = {"approved", "scheduled", "in_progress", "completed", "on_hold", "blocked"}
MAX_ACTIVE_DRAFTS = 200
MAX_DRAFT_PROMPT_CHARACTERS = 100_000
_DRAFT_CHAT_GUARD = threading.Lock()
_DRAFT_CHATS_IN_FLIGHT: set[int] = set()


def _project(db: Session, project_id: int) -> Project:
    project = project_or_none(db, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _draft(db: Session, draft_id: int, *, with_for_update: bool = False) -> ProjectDraft:
    draft = db.get(ProjectDraft, draft_id, with_for_update=with_for_update)
    if draft is None:
        raise HTTPException(404, "project draft not found")
    return draft


def _task(db: Session, task_id: int) -> Task:
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.dependencies), selectinload(Task.source_links))
    )
    if task is None:
        raise HTTPException(404, "task not found")
    return task


def _task_has_children(db: Session, task_id: int) -> bool:
    return db.scalar(select(Task.id).where(Task.parent_task_id == task_id).limit(1)) is not None


def _task_subtree(db: Session, root: Task) -> list[Task]:
    project_tasks = list(db.scalars(select(Task).where(Task.project_id == root.project_id).order_by(Task.id)))
    children: dict[int, list[Task]] = defaultdict(list)
    for task in project_tasks:
        if task.parent_task_id is not None:
            children[task.parent_task_id].append(task)
    result: list[Task] = []
    stack = [root]
    while stack:
        task = stack.pop()
        result.append(task)
        stack.extend(children.get(task.id, []))
    return result


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


async def _read_upload_chunks(uploaded: Any) -> bytes:
    content = bytearray()
    while True:
        chunk = await uploaded.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(413, f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다")


def _write_and_parse(
    path: Path, raw: bytes, file_type: str, original_name: str = ""
) -> tuple[list[dict[str, Any]], Exception | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(raw)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    try:
        blocks = parse_document(path, file_type, original_name)
        if file_type in IMAGE_EXTENSIONS:
            caption = describe_image_safely(raw, IMAGE_MIME.get(file_type, "image/png"))
            if caption:
                blocks[0]["content"] = f"{blocks[0]['content']}\n이미지 내용: {caption}"
                blocks[0]["location_metadata"]["ai_caption"] = True
        return blocks, None
    except Exception as exc:
        return [], exc


def _safe_parser_error(exc: Exception) -> str:
    if isinstance(exc, UnsupportedFileTypeError):
        return str(exc)
    safe_reasons = {
        "셀 수 초과",
        "압축 해제 크기 초과",
        "압축비 초과",
        "문서 블록 수 초과",
    }
    reason = str(exc) if str(exc) in safe_reasons else None
    detail = f": {reason}" if reason else ""
    return f"문서 파싱에 실패했습니다 ({type(exc).__name__}{detail}). 파일을 확인하거나 다른 형식으로 변환해 주세요"


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    values = payload.model_dump()
    values["excluded_dates"] = [value.isoformat() for value in payload.excluded_dates]
    project = Project(**values)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/project-drafts", response_model=ProjectDraftOut, status_code=201)
def create_project_draft(db: Session = Depends(get_db)):
    active_count = int(db.scalar(select(func.count(ProjectDraft.id)).where(ProjectDraft.status == "active")) or 0)
    if active_count >= MAX_ACTIVE_DRAFTS:
        raise HTTPException(409, "활성 draft가 200개에 도달했습니다. 오래된 draft를 정리한 뒤 다시 시도해 주세요")
    first_question = "안녕하세요! 만들 프로젝트의 이름은 무엇인가요?"
    draft = ProjectDraft(
        fields={},
        completeness_percent=0.0,
        conversation=[{"role": "assistant", "content": first_question}],
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/project-drafts/{draft_id}", response_model=ProjectDraftOut)
def get_project_draft(draft_id: int, db: Session = Depends(get_db)):
    return _draft(db, draft_id)


@router.patch("/project-drafts/{draft_id}", response_model=ProjectDraftOut)
def patch_project_draft(draft_id: int, payload: ProjectDraftPatch, db: Session = Depends(get_db)):
    draft = _draft(db, draft_id, with_for_update=True)
    if draft.status != "active":
        raise HTTPException(409, "active draft만 수정할 수 있습니다")
    draft.fields = merge_draft_fields(draft.fields or {}, payload.fields)
    draft.completeness_percent = calculate_completeness(draft.fields)
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/project-drafts/{draft_id}/chat", response_model=DraftChatResponse)
def chat_project_draft(draft_id: int, payload: DraftChatRequest, db: Session = Depends(get_db)):
    with _DRAFT_CHAT_GUARD:
        if draft_id in _DRAFT_CHATS_IN_FLIGHT:
            raise HTTPException(409, "이미 응답을 생성 중입니다")
        _DRAFT_CHATS_IN_FLIGHT.add(draft_id)
    try:
        draft = _draft(db, draft_id, with_for_update=True)
        if draft.status != "active":
            raise HTTPException(409, "active draft에서만 대화할 수 있습니다")
        conversation = list(draft.conversation or [])
        if sum(item.get("role") == "user" for item in conversation) >= 100:
            raise HTTPException(409, "draft당 대화는 100턴까지 가능합니다")
        conversation.append({"role": "user", "content": payload.message.strip()})
        current_fields = DraftFields.model_validate(draft.fields or {})
        if len(build_draft_prompt(current_fields, conversation)) > MAX_DRAFT_PROMPT_CHARACTERS:
            raise HTTPException(422, "대화 내용이 너무 깁니다")
        try:
            result = get_ai_router().chat_draft(current_fields, conversation)
        except ProviderError as exc:
            raise HTTPException(
                503,
                "AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해 주세요",
            ) from exc
        merged = merge_draft_fields(draft.fields or {}, result.updated_fields)
        completeness = calculate_completeness(merged)
        conversation.append({"role": "assistant", "content": result.reply})
        draft.fields = merged
        draft.completeness_percent = completeness
        draft.conversation = conversation
        db.commit()
        db.refresh(draft)
        return {
            "reply": result.reply,
            "fields": DraftFields.model_validate(merged),
            "completeness_percent": completeness,
            "next_question": result.next_question,
        }
    finally:
        with _DRAFT_CHAT_GUARD:
            _DRAFT_CHATS_IN_FLIGHT.discard(draft_id)


@router.post("/project-drafts/{draft_id}/confirm", response_model=ProjectOut, status_code=201)
def confirm_project_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = _draft(db, draft_id, with_for_update=True)
    if draft.status != "active":
        raise HTTPException(409, "active draft만 확정할 수 있습니다")
    fields = DraftFields.model_validate(draft.fields or {}).model_dump(mode="json", exclude_none=True)
    missing = missing_required_fields(fields)
    if missing:
        raise HTTPException(422, detail={"message": "필수 필드가 부족합니다", "missing_fields": missing})
    try:
        project_values = ProjectCreate(
            name=fields["name"],
            description=fields.get("description"),
            start_date=fields["start_date"],
            target_date=fields["target_date"],
            work_days=fields.get("work_days", [0, 1, 2, 3, 4]),
            daily_capacity_hours=fields["daily_capacity_hours"],
            buffer_ratio=fields.get("buffer_ratio", 0.2),
            excluded_dates=fields.get("excluded_dates", []),
        )
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_url=False, include_context=False)) from exc
    values = project_values.model_dump()
    values["excluded_dates"] = [value.isoformat() for value in project_values.excluded_dates]
    project = Project(**values)
    db.add(project)
    db.flush()
    if fields.get("goal"):
        db.add(
            ProjectFact(
                project_id=project.id,
                fact_type="goal",
                content=fields["goal"],
                confidence=1.0,
                review_status="approved",
                source_block_id=None,
            )
        )
    for candidate in fields.get("task_candidates", []):
        db.add(
            Task(
                project_id=project.id,
                title=candidate["title"],
                estimated_hours=candidate["estimated_hours"],
                priority=candidate.get("priority", "medium"),
                status="approved",
                ai_generated=False,
                confidence=None,
            )
        )
    draft.status = "confirmed"
    db.commit()
    db.refresh(project)
    return project


@router.delete("/project-drafts/{draft_id}", status_code=204)
def discard_project_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = _draft(db, draft_id)
    if draft.status == "confirmed":
        raise HTTPException(409, "confirmed draft는 폐기할 수 없습니다")
    draft.status = "discarded"
    db.commit()
    return Response(status_code=204)


@router.get("/ai/router/status", response_model=RouterStatus)
def get_ai_router_status():
    return {"providers": router_status()}


class AiKeyUpdate(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)


def _validate_ai_provider(provider: str) -> str:
    from app.ai_settings import AI_PROVIDERS

    if provider not in AI_PROVIDERS:
        raise HTTPException(404, f"지원하지 않는 provider: {provider}")
    return provider


@router.get("/settings/ai")
def get_ai_settings(db: Session = Depends(get_db)):
    from app.ai_settings import ai_key_status

    return {"providers": ai_key_status(db)}


@router.put("/settings/ai/{provider}")
def put_ai_key(provider: str, payload: AiKeyUpdate, db: Session = Depends(get_db)):
    from app.ai_settings import ai_key_status, set_ai_key

    _validate_ai_provider(provider)
    try:
        set_ai_key(db, provider, payload.api_key)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"providers": ai_key_status(db)}


@router.delete("/settings/ai/{provider}")
def remove_ai_key(provider: str, db: Session = Depends(get_db)):
    from app.ai_settings import ai_key_status, delete_ai_key

    _validate_ai_provider(provider)
    delete_ai_key(db, provider)
    return {"providers": ai_key_status(db)}


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


def _sanitize_file_type(suffix: str) -> str:
    cleaned = suffix.lower().lstrip(".")
    return cleaned if cleaned and len(cleaned) <= 10 and cleaned.isalnum() else "bin"


async def _create_url_source(project_id: int, url: str, db: Session):
    url = url.strip()
    if not url or len(url) > 2000:
        raise HTTPException(422, "올바른 URL을 입력해 주세요")
    try:
        title, blocks = await run_in_threadpool(fetch_url_blocks, url)
    except UrlFetchError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "URL 내용을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요") from exc
    source = SourceDocument(
        project_id=project_id,
        file_name=(title or url)[:255],
        file_type="url",
        storage_path=None,
        analysis_status="extracting",
    )
    try:
        db.add(source)
        db.flush()
        source.extracted_text = "\n\n".join(block["content"] for block in blocks)
        for block in blocks:
            db.add(SourceBlock(source_document_id=source.id, **block))
        source.analysis_status = "text_extracted"
        db.commit()
    except Exception:
        db.rollback()
        raise
    return db.scalar(_source_query(project_id).where(SourceDocument.id == source.id))


@router.post("/projects/{project_id}/sources", response_model=SourceDocumentOut, status_code=201)
async def create_source(project_id: int, request: Request, db: Session = Depends(get_db)):
    _project(db, project_id)
    content_type = request.headers.get("content-type", "")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE:
                raise HTTPException(413, f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다")
        except ValueError:
            raise HTTPException(400, "유효하지 않은 Content-Length입니다") from None
    file_name: str | None = None
    raw: bytes
    file_type: str
    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded = form.get("file")
        text_value = form.get("text")
        if uploaded is not None and hasattr(uploaded, "read"):
            file_name = Path(str(getattr(uploaded, "filename", "upload"))).name
            file_type = _sanitize_file_type(Path(file_name).suffix)
            raw = await _read_upload_chunks(uploaded)
        elif text_value is not None:
            file_name = Path(str(form.get("file_name") or "direct-input.txt")).name
            file_type = Path(file_name).suffix.lower().lstrip(".") or "txt"
            if file_type not in {"txt", "md"}:
                raise HTTPException(415, "텍스트 직접 입력은 TXT 또는 MD 파일명만 지원합니다")
            raw = str(text_value).encode("utf-8")
        else:
            raise HTTPException(400, "file 또는 text가 필요합니다")
    elif "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "text 또는 url이 필요합니다")
        if isinstance(payload.get("url"), str):
            return await _create_url_source(project_id, payload["url"], db)
        if not isinstance(payload.get("text"), str):
            raise HTTPException(400, "text 또는 url이 필요합니다")
        file_name = Path(str(payload.get("file_name") or "direct-input.txt")).name
        file_type = Path(file_name).suffix.lower().lstrip(".") or "txt"
        if file_type not in {"txt", "md"}:
            raise HTTPException(415, "텍스트 직접 입력은 TXT 또는 MD 파일명만 지원합니다")
        raw = payload["text"].encode("utf-8")
    else:
        raise HTTPException(415, "multipart/form-data 또는 application/json만 지원합니다")
    if len(raw) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다")
    if not raw:
        raise HTTPException(400, "빈 파일은 업로드할 수 없습니다")

    project_dir = STORAGE_ROOT / str(project_id)
    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    storage_path = project_dir / stored_name
    try:
        blocks, parse_error = await run_in_threadpool(_write_and_parse, storage_path, raw, file_type, file_name)
    except Exception as exc:
        raise HTTPException(500, f"파일 저장에 실패했습니다 ({type(exc).__name__})") from exc
    source = SourceDocument(
        project_id=project_id,
        file_name=file_name[:255],
        file_type=file_type,
        storage_path=str(storage_path),
        analysis_status="extracting",
    )
    try:
        db.add(source)
        db.flush()
        if parse_error is None:
            source.extracted_text = "\n\n".join(block["content"] for block in blocks)
            for block in blocks:
                db.add(SourceBlock(source_document_id=source.id, **block))
            source.analysis_status = "text_extracted"
        else:
            source.analysis_status = "failed"
            source.error_message = _safe_parser_error(parse_error)
        db.commit()
    except Exception:
        db.rollback()
        await run_in_threadpool(storage_path.unlink, missing_ok=True)
        raise
    return db.scalar(_source_query(project_id).where(SourceDocument.id == source.id))


@router.get("/reports/daily")
def daily_report(date_: str | None = None, db: Session = Depends(get_db)):
    """Slack 일일 보고 초안. `?date_=YYYY-MM-DD`로 특정 날짜를 지정할 수 있다."""
    if date_:
        try:
            target = date.fromisoformat(date_)
        except ValueError:
            raise HTTPException(422, "date_는 YYYY-MM-DD 형식이어야 합니다") from None
    else:
        target = date.today()
    report = build_daily_report(db, target)
    report["text"] = render_report_text(report)
    return report


@router.get("/projects/{project_id}/sources", response_model=list[SourceDocumentOut])
def list_sources(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return list(db.scalars(_source_query(project_id)))


UPDATABLE_TASK_STATUSES = {"approved", "scheduled", "in_progress", "completed", "on_hold", "blocked"}


def _update_target_tasks(db: Session, project_id: int) -> list[Task]:
    return [task for task in db.scalars(leaf_task_query(project_id)) if task.status in UPDATABLE_TASK_STATUSES]


def _task_snapshot(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "estimated_hours": task.estimated_hours,
        "progress_percent": task.progress_percent,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


@router.post("/projects/{project_id}/task-updates/preview")
def preview_task_updates(project_id: int, payload: TaskUpdatePreviewRequest, db: Session = Depends(get_db)):
    """진행 메모를 요약하고, 기존 업무에 반영할 변경을 제안한다(저장하지 않음)."""
    project = _project(db, project_id)
    tasks = _update_target_tasks(db, project_id)
    if not tasks:
        raise HTTPException(409, "반영할 기존 업무가 없습니다. 먼저 업무를 추가하세요.")
    snapshots = [_task_snapshot(task) for task in tasks]
    known_ids = {snapshot["id"] for snapshot in snapshots}
    ai_router = get_ai_router()
    try:
        plan = ai_router.plan_task_updates(
            payload.text,
            snapshots,
            {
                "name": project.name,
                "start_date": project.start_date.isoformat(),
                "target_date": project.target_date.isoformat(),
                "today": date.today().isoformat(),
            },
        )
    except ProviderError as exc:
        raise HTTPException(503, "AI provider가 응답하지 않아 제안을 만들지 못했습니다") from exc
    by_id = {task.id: task for task in tasks}
    proposals: list[dict[str, Any]] = []
    for item in plan.updates:
        # 모델이 만들어낸 존재하지 않는 task_id는 버린다.
        if item.action != "create" and item.task_id not in known_ids:
            continue
        proposal = item.model_dump(mode="json")
        proposal["current"] = _task_snapshot(by_id[item.task_id]) if item.task_id in by_id else None
        proposals.append(proposal)
    return {
        "summary": plan.summary,
        "provider": ai_router.last_provider_name or "unknown",
        "updates": proposals,
    }


@router.post("/projects/{project_id}/task-updates/apply")
def apply_task_updates(project_id: int, payload: TaskUpdateApplyRequest, db: Session = Depends(get_db)):
    """검토한 제안을 실제 업무에 반영한다."""
    _project(db, project_id)
    tasks = {task.id: task for task in _update_target_tasks(db, project_id)}
    applied = {"updated": 0, "completed": 0, "created": 0}
    today = date.today()
    for item in payload.updates:
        if item.action == "create":
            db.add(
                Task(
                    project_id=project_id,
                    title=item.title.strip(),
                    status="approved",
                    priority=item.priority or "medium",
                    estimated_hours=item.estimated_hours if item.estimated_hours is not None else 1.0,
                    progress_percent=item.progress_percent or 0.0,
                    due_date=item.due_date,
                )
            )
            applied["created"] += 1
            continue
        task = tasks.get(item.task_id)
        if task is None:
            raise HTTPException(404, f"업무를 찾을 수 없습니다: {item.task_id}")
        if _task_has_children(db, task.id):
            raise HTTPException(409, f"하위 업무가 있는 업무는 직접 반영할 수 없습니다: {task.title}")
        previous = {
            "status": task.status,
            "progress_percent": task.progress_percent,
            "actual_hours": task.actual_hours,
        }
        if item.action == "complete":
            task.status = "completed"
            task.progress_percent = 100.0
            task.actual_hours = task.actual_hours or task.estimated_hours
            task.actual_start_date = task.actual_start_date or today
            task.actual_end_date = today
            record_task_event(
                db,
                task,
                "completed",
                previous,
                {"status": "completed", "progress_percent": 100.0, "actual_hours": task.actual_hours},
                "자료 메모 반영",
                event_date=today,
            )
            applied["completed"] += 1
            continue
        if item.title is not None:
            task.title = item.title.strip()
        if item.estimated_hours is not None:
            task.estimated_hours = item.estimated_hours
        if item.priority is not None:
            task.priority = item.priority
        if item.due_date is not None:
            task.due_date = item.due_date
        if item.progress_percent is not None:
            task.progress_percent = item.progress_percent
            if item.progress_percent >= 100:
                task.status = "completed"
                task.actual_end_date = today
            elif item.progress_percent > 0:
                if task.status in {"approved", "scheduled", "completed"}:
                    task.status = "in_progress"
                task.actual_start_date = task.actual_start_date or today
                task.actual_end_date = None
            record_task_event(
                db,
                task,
                "progress_updated",
                previous,
                {"status": task.status, "progress_percent": task.progress_percent},
                "자료 메모 반영",
                event_date=today,
            )
        applied["updated"] += 1
    db.commit()
    return applied


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
    affected_tasks = list(
        db.execute(
            select(Task.id, Task.title, Task.status)
            .join(TaskSourceLink, TaskSourceLink.task_id == Task.id)
            .join(SourceBlock, SourceBlock.id == TaskSourceLink.source_block_id)
            .where(
                SourceBlock.source_document_id == source_id,
                Task.ai_generated.is_(True),
                Task.status.in_(sorted(APPROVED_TASK_STATUSES)),
            )
            .order_by(Task.id)
        ).mappings()
    )
    affected_facts = list(
        db.execute(
            select(ProjectFact.id, ProjectFact.fact_type, ProjectFact.content)
            .join(SourceBlock, SourceBlock.id == ProjectFact.source_block_id)
            .where(SourceBlock.source_document_id == source_id, ProjectFact.review_status == "approved")
            .order_by(ProjectFact.id)
        ).mappings()
    )
    if affected_tasks or affected_facts:
        raise HTTPException(
            409,
            detail={
                "message": "승인된 항목이 참조하는 source는 삭제할 수 없습니다",
                "tasks": [dict(item) for item in affected_tasks],
                "facts": [dict(item) for item in affected_facts],
            },
        )
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
    tasks = list(db.scalars(leaf_task_query(project_id)))
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
            source_block_ids = updates.pop("source_block_ids", None)
            _validate_task_references(
                db,
                project_id,
                dependency_ids=dependency_ids,
                parent_task_id=updates.get("parent_task_id", task.parent_task_id),
                milestone_id=updates.get("milestone_id", task.milestone_id),
                source_block_ids=source_block_ids,
                task_id=task.id,
            )
            for key, value in updates.items():
                setattr(task, key, value)
            if dependency_ids is not None:
                task.dependencies.clear()
                db.flush()
                for dependency_id in sorted(set(dependency_ids)):
                    db.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency_id))
            if source_block_ids is not None:
                task.source_links.clear()
                db.flush()
                for block_id in sorted(set(source_block_ids)):
                    db.add(TaskSourceLink(task_id=task.id, source_block_id=block_id, relevance_score=1.0))
            task.status = "approved"
        if _task_has_children(db, task.id):
            for descendant in _task_subtree(db, task)[1:]:
                if descendant.ai_generated and descendant.status == "pending_review":
                    descendant.status = task.status
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
            try:
                updates = FactReviewUpdate.model_validate(decision.updates).model_dump(exclude_unset=True)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if updates.get("content") is None and "content" in updates:
                raise HTTPException(422, "fact content cannot be null")
            for key, value in updates.items():
                setattr(fact, key, value)
            fact.review_status = "approved"
        changed["facts"] += 1
    try:
        db.flush()
        _assert_project_dependencies(db, project_id)
    except HTTPException:
        db.rollback()
        raise
    pending_tasks = db.scalar(
        leaf_task_query(project_id).where(
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
        for task in db.scalars(
            select(Task).where(
                Task.project_id == project_id,
                Task.ai_generated.is_(True),
                Task.status == "pending_review",
            )
        ):
            if _task_has_children(db, task.id):
                leaf_descendants = [
                    descendant
                    for descendant in _task_subtree(db, task)[1:]
                    if not _task_has_children(db, descendant.id)
                ]
                task.status = (
                    "rejected"
                    if leaf_descendants and all(descendant.status == "rejected" for descendant in leaf_descendants)
                    else "approved"
                )
        for source in db.scalars(select(SourceDocument).where(SourceDocument.project_id == project_id)):
            if source.analysis_status == "review_required":
                source.analysis_status = "completed"
    db.commit()
    return {"status": "applied", **changed}


def _validate_task_references(
    db: Session,
    project_id: int,
    *,
    dependency_ids: list[int] | None = None,
    parent_task_id: int | None = None,
    milestone_id: int | None = None,
    source_block_ids: list[int] | None = None,
    task_id: int | None = None,
) -> None:
    related_ids = set(dependency_ids or [])
    if parent_task_id:
        related_ids.add(parent_task_id)
    if task_id is not None and task_id in related_ids:
        raise HTTPException(422, "task cannot reference itself as parent or dependency")
    if related_ids:
        found = set(db.scalars(select(Task.id).where(Task.project_id == project_id, Task.id.in_(related_ids))))
        if found != related_ids:
            raise HTTPException(422, "dependency or parent task does not belong to project")
    if milestone_id:
        milestone = db.get(Milestone, milestone_id)
        if milestone is None or milestone.project_id != project_id:
            raise HTTPException(422, "milestone does not belong to project")
    if source_block_ids:
        found_blocks = set(
            db.scalars(
                select(SourceBlock.id)
                .join(SourceDocument, SourceDocument.id == SourceBlock.source_document_id)
                .where(SourceDocument.project_id == project_id, SourceBlock.id.in_(source_block_ids))
            )
        )
        if found_blocks != set(source_block_ids):
            raise HTTPException(422, "source_block does not belong to project")


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
        parent_edges = list(
            db.execute(
                select(Task.id, Task.parent_task_id).where(
                    Task.project_id == project_id,
                    Task.parent_task_id.is_not(None),
                )
            ).tuples()
        )
        assert_acyclic(ids, parent_edges)
    except DependencyCycleError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(project_id: int, payload: TaskCreate, db: Session = Depends(get_db)):
    _project(db, project_id)
    _validate_task_references(
        db,
        project_id,
        dependency_ids=payload.dependency_ids,
        parent_task_id=payload.parent_task_id,
        milestone_id=payload.milestone_id,
        source_block_ids=payload.source_block_ids,
    )
    values = payload.model_dump(exclude={"dependency_ids", "source_block_ids"})
    max_sort = db.scalar(select(func.max(Task.sort_order)).where(Task.project_id == project_id)) or 0
    task = Task(project_id=project_id, sort_order=max_sort + 1, **values)
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


@router.post("/tasks/{task_id}/move", response_model=TaskOut)
def move_task(task_id: int, payload: TaskMoveRequest, db: Session = Depends(get_db)):
    """업무를 다른 주제(주간/월간 그룹 또는 기타)로 이동한다. 계층 규칙:
    monthly는 이동 불가(순서 변경만), weekly는 monthly 아래로만,
    리프(daily/기타)는 weekly 아래(daily가 됨) 또는 최상위(기타)로."""
    task = _task(db, task_id)
    new_parent = None
    if payload.new_parent_id is not None:
        new_parent = db.get(Task, payload.new_parent_id)
        if new_parent is None or new_parent.project_id != task.project_id:
            raise HTTPException(422, "이동 대상 그룹이 같은 프로젝트에 없습니다")
        # 순환 방지: 새 부모의 조상 경로에 자기 자신이 있으면 안 된다
        cursor = new_parent
        for _ in range(20):
            if cursor is None:
                break
            if cursor.id == task.id:
                raise HTTPException(422, "자기 하위로는 이동할 수 없습니다")
            cursor = db.get(Task, cursor.parent_task_id) if cursor.parent_task_id else None

    has_children = db.scalar(select(Task.id).where(Task.parent_task_id == task.id).limit(1)) is not None
    if task.cadence == "monthly":
        raise HTTPException(422, "월간 업무는 이동할 수 없습니다 (순서 변경만 가능)")
    if task.cadence == "weekly" or has_children:
        if new_parent is None or new_parent.cadence != "monthly":
            raise HTTPException(422, "주간 업무는 월간 업무 아래로만 이동할 수 있습니다")
        task.parent_task_id = new_parent.id
        task.cadence = "weekly"
        task.milestone_id = new_parent.milestone_id
    else:  # 리프(daily 또는 기타)
        if new_parent is None:
            task.parent_task_id = None
            task.cadence = None
        elif new_parent.cadence == "weekly":
            task.parent_task_id = new_parent.id
            task.cadence = "daily"
            task.milestone_id = new_parent.milestone_id
        else:
            raise HTTPException(422, "일간 업무는 주간 업무 아래 또는 기타로만 이동할 수 있습니다")
    db.flush()

    # 새 형제 그룹 내 위치 반영 (before_task_id 앞, 없으면 맨 뒤)
    siblings = list(
        db.scalars(
            select(Task)
            .where(
                Task.project_id == task.project_id,
                Task.parent_task_id.is_(None) if task.parent_task_id is None else Task.parent_task_id == task.parent_task_id,
                Task.id != task.id,
            )
            .order_by(Task.sort_order, Task.id)
        )
    )
    if task.parent_task_id is None:
        # 최상위 형제는 표시 그룹 기준(기타=cadence 없음)만 대상으로 정렬
        siblings = [item for item in siblings if item.cadence is None]
    insert_at = len(siblings)
    if payload.before_task_id is not None:
        for index, sibling in enumerate(siblings):
            if sibling.id == payload.before_task_id:
                insert_at = index
                break
    ordered = siblings[:insert_at] + [task] + siblings[insert_at:]
    base = min((item.sort_order for item in ordered), default=0)
    for index, item in enumerate(ordered):
        item.sort_order = base + index
    db.commit()
    return _task(db, task.id)


@router.post("/projects/{project_id}/tasks/reorder")
def reorder_tasks(project_id: int, payload: TaskReorderRequest, db: Session = Depends(get_db)):
    _project(db, project_id)
    ordered_ids = payload.ordered_ids
    if len(set(ordered_ids)) != len(ordered_ids):
        raise HTTPException(422, "ordered_ids에 중복이 있습니다")
    tasks = {
        task.id: task
        for task in db.scalars(select(Task).where(Task.project_id == project_id, Task.id.in_(ordered_ids)))
    }
    if set(ordered_ids) != set(tasks):
        raise HTTPException(422, "ordered_ids에 이 프로젝트의 업무가 아닌 항목이 있습니다")
    # 형제 그룹 단위 재정렬: 전달된 목록 순서대로 sort_order를 재부여한다.
    base = min(task.sort_order for task in tasks.values())
    for index, task_id in enumerate(ordered_ids):
        tasks[task_id].sort_order = base + index
    db.commit()
    return {"updated": len(ordered_ids)}


@router.get("/projects/{project_id}/milestones", response_model=list[MilestoneOut])
def list_milestones(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.sort_order, Milestone.id)
        )
    )


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    return _task(db, task_id)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskPatch, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    values = payload.model_dump(exclude_unset=True)
    requested_status = values.get("status")
    if _task_has_children(db, task.id) and (
        requested_status == "completed" or (task.status == "completed" and requested_status not in {None, "completed"})
    ):
        raise HTTPException(409, "하위 업무를 완료하세요")
    if (
        task.ai_generated
        and task.status == "pending_review"
        and requested_status is not None
        and requested_status != "pending_review"
    ):
        raise HTTPException(422, "AI 생성 업무의 검토 상태 전이는 analysis/approve에서만 가능합니다")
    required_fields = {"title", "status", "priority", "estimated_hours", "actual_hours", "progress_percent", "locked"}
    if any(values.get(field) is None for field in required_fields if field in values):
        raise HTTPException(422, "required task fields cannot be null")
    dependency_ids = values.pop("dependency_ids", None)
    source_block_ids = values.pop("source_block_ids", None)
    _validate_task_references(
        db,
        task.project_id,
        dependency_ids=dependency_ids,
        parent_task_id=values.get("parent_task_id", task.parent_task_id),
        milestone_id=values.get("milestone_id", task.milestone_id),
        source_block_ids=source_block_ids,
        task_id=task.id,
    )
    if dependency_ids is not None:
        task.dependencies.clear()
        db.flush()
        for dependency_id in sorted(set(dependency_ids)):
            db.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency_id))
    if source_block_ids is not None:
        task.source_links.clear()
        db.flush()
        for block_id in sorted(set(source_block_ids)):
            db.add(TaskSourceLink(task_id=task.id, source_block_id=block_id, relevance_score=1.0))
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
    if _task_has_children(db, task.id):
        raise HTTPException(409, "하위 업무를 완료하세요")
    if task.status in {"pending_review", "rejected"}:
        raise HTTPException(409, "검토 대기 또는 거절 업무는 완료할 수 없습니다")
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


@router.post("/tasks/{task_id}/reopen", response_model=TaskOut)
def reopen_task(task_id: int, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    if _task_has_children(db, task.id):
        raise HTTPException(409, "하위 업무를 완료하세요")
    if task.status != "completed":
        raise HTTPException(409, "완료된 업무만 다시 열 수 있습니다")
    completed_event = db.scalar(
        select(TaskEvent)
        .where(TaskEvent.task_id == task.id, TaskEvent.event_type == "completed")
        .order_by(TaskEvent.id.desc())
        .limit(1)
    )
    # 완료 이력이 있으면 완료 직전 상태로, 없으면(외부 시드 등) 진행 중 상태로 되돌린다.
    previous_status = "in_progress"
    previous_progress = 0.0
    previous_hours = task.actual_hours or 0.0
    if completed_event is not None and completed_event.previous_value:
        try:
            previous = json.loads(completed_event.previous_value)
            previous_status = previous["status"]
            previous_progress = float(previous["progress_percent"])
            previous_hours = float(previous["actual_hours"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    completed_state = {
        "status": task.status,
        "progress_percent": task.progress_percent,
        "actual_hours": task.actual_hours,
    }
    task.status = previous_status
    task.progress_percent = previous_progress
    task.actual_hours = previous_hours
    task.actual_end_date = None
    restored = {"status": previous_status, "progress_percent": previous_progress, "actual_hours": previous_hours}
    record_task_event(db, task, "reopened", completed_state, restored)
    db.commit()
    return _task(db, task.id)


@router.post("/tasks/{task_id}/block", response_model=TaskOut)
def block_task(task_id: int, payload: TaskBlock, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    if task.status in {"pending_review", "rejected"}:
        raise HTTPException(409, "검토 대기 또는 거절 업무는 차단할 수 없습니다")
    previous = task.status
    task.status = "blocked"
    record_task_event(db, task, "blocked", previous, "blocked", payload.reason)
    db.commit()
    return _task(db, task.id)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = _task(db, task_id)
    for descendant in reversed(_task_subtree(db, task)):
        db.delete(descendant)
    db.commit()
    return Response(status_code=204)


@router.delete("/projects/{project_id}/tasks")
def delete_all_tasks(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)))
    for task in tasks:
        db.delete(task)
    db.commit()
    return {"deleted": len(tasks)}


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
    version = replan_schedule(db, project, payload.reason, payload.strategy)
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
    tasks = list(db.scalars(leaf_task_query(project_id)))
    task_map = {task.id: task for task in tasks}
    today_items = [
        item
        for item in placements
        if item["date"] == as_of.isoformat() and int(item["task_id"]) in task_map
    ]
    assigned = sum(float(item["hours"]) for item in today_items)
    effective_capacity = round(project.daily_capacity_hours * (1 - project.buffer_ratio), 6)
    warning = capacity_warning(assigned, effective_capacity)
    return {
        "project_id": project_id,
        "as_of": as_of.isoformat(),
        "pace": pace,
        "today": {
            "available_hours": effective_capacity,
            "raw_daily_capacity_hours": project.daily_capacity_hours,
            "effective_daily_capacity_hours": effective_capacity,
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
