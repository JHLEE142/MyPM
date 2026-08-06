from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai.schemas import DraftFields


ProjectStatus = Literal["active", "paused", "completed"]
TaskStatus = Literal[
    "extracted",
    "pending_review",
    "approved",
    "scheduled",
    "in_progress",
    "completed",
    "on_hold",
    "blocked",
    "rejected",
]
DUE_DATE_MIN = date(1970, 1, 1)
DUE_DATE_MAX = date(2100, 12, 31)


def _due_date_in_range(value: date | None) -> date | None:
    if value is not None and not DUE_DATE_MIN <= value <= DUE_DATE_MAX:
        raise ValueError("due_date must be between 1970-01-01 and 2100-12-31")
    return value


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    owner: str | None = Field(default=None, max_length=100)
    description: str | None = None
    start_date: date = Field(default_factory=date.today)
    target_date: date = Field(default_factory=lambda: date.today() + timedelta(days=30))
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    daily_capacity_hours: float = Field(default=4.0, gt=0)
    buffer_ratio: float = Field(default=0.2, ge=0, lt=1)
    excluded_dates: list[date] = Field(default_factory=list)
    status: ProjectStatus = "active"

    @model_validator(mode="after")
    def validate_dates_and_days(self):
        if self.target_date < self.start_date:
            raise ValueError("target_date must be on or after start_date")
        if not self.work_days or any(day < 0 or day > 6 for day in self.work_days):
            raise ValueError("work_days must contain weekday numbers from 0 to 6")
        self.work_days = sorted(set(self.work_days))
        return self


class ProjectCreate(ProjectBase):
    pass


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    owner: str | None = Field(default=None, max_length=100)
    description: str | None = None
    start_date: date | None = None
    target_date: date | None = None
    work_days: list[int] | None = None
    daily_capacity_hours: float | None = Field(default=None, gt=0)
    buffer_ratio: float | None = Field(default=None, ge=0, lt=1)
    excluded_dates: list[date] | None = None
    status: ProjectStatus | None = None


class ProjectOut(ORMModel):
    id: int
    name: str
    owner: str | None
    description: str | None
    start_date: date
    target_date: date
    work_days: list[int]
    daily_capacity_hours: float
    buffer_ratio: float
    excluded_dates: list[str]
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class DraftConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ProjectDraftOut(ORMModel):
    id: int
    status: Literal["active", "confirmed", "discarded"]
    fields: DraftFields
    completeness_percent: float
    conversation: list[DraftConversationMessage]
    created_at: datetime
    updated_at: datetime


class ProjectDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: DraftFields


class DraftChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)


class DraftChatResponse(BaseModel):
    reply: str
    fields: DraftFields
    completeness_percent: float
    next_question: str | None


class RouterProviderStatus(BaseModel):
    name: str
    available: bool
    today_calls: int
    quota: int | None
    last_success_at: datetime | None


class RouterStatus(BaseModel):
    providers: list[RouterProviderStatus]


class SourceBlockOut(ORMModel):
    id: int
    source_document_id: int
    block_type: str
    page_number: int | None
    sheet_name: str | None
    section_title: str | None
    content: str
    block_order: int
    location_metadata: dict[str, Any]


class SourceDocumentOut(ORMModel):
    id: int
    project_id: int
    file_name: str
    file_type: str
    extracted_text: str | None
    analysis_status: str
    error_message: str | None
    uploaded_at: datetime
    blocks: list[SourceBlockOut] = Field(default_factory=list)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    milestone_id: int | None = None
    parent_task_id: int | None = None
    status: TaskStatus = "approved"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    estimated_hours: float = Field(default=1.0, ge=0, le=10000)
    actual_hours: float = Field(default=0.0, ge=0)
    progress_percent: float = Field(default=0.0, ge=0, le=100)
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    due_date: date | None = None
    locked: bool = False
    dependency_ids: list[int] = Field(default_factory=list)
    source_block_ids: list[int] = Field(default_factory=list)

    _validate_due_date = field_validator("due_date")(_due_date_in_range)


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    milestone_id: int | None = None
    parent_task_id: int | None = None
    status: TaskStatus | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    estimated_hours: float | None = Field(default=None, ge=0, le=10000)
    actual_hours: float | None = Field(default=None, ge=0)
    progress_percent: float | None = Field(default=None, ge=0, le=100)
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    due_date: date | None = None
    locked: bool | None = None
    dependency_ids: list[int] | None = None
    source_block_ids: list[int] | None = None

    _validate_due_date = field_validator("due_date")(_due_date_in_range)


class DependencyOut(ORMModel):
    task_id: int
    depends_on_task_id: int
    dependency_type: str


class SourceLinkOut(ORMModel):
    task_id: int
    source_block_id: int
    relevance_score: float


class TaskOut(ORMModel):
    id: int
    project_id: int
    milestone_id: int | None
    parent_task_id: int | None
    cadence: Literal["monthly", "weekly", "daily"] | None
    title: str
    description: str | None
    status: TaskStatus
    priority: str
    estimated_hours: float
    actual_hours: float
    progress_percent: float
    planned_start_date: date | None
    planned_end_date: date | None
    actual_start_date: date | None
    actual_end_date: date | None
    due_date: date | None
    locked: bool
    ai_generated: bool
    confidence: float | None
    created_at: datetime
    updated_at: datetime
    dependencies: list[DependencyOut] = Field(default_factory=list)
    source_links: list[SourceLinkOut] = Field(default_factory=list)


class TaskComplete(BaseModel):
    actual_hours: float | None = Field(default=None, ge=0)
    note: str | None = None
    completed_date: date | None = None


class TaskBlock(BaseModel):
    reason: str = Field(min_length=1)


class ScheduleGenerate(BaseModel):
    reason: str = "initial plan"


class ReplanRequest(BaseModel):
    reason: str = "progress update"
    strategy: Literal["redistribute", "increase_capacity", "defer_low_priority", "change_target"] = "redistribute"
    daily_capacity_hours: float | None = Field(default=None, gt=0)
    target_date: date | None = None


class ReviewDecision(BaseModel):
    id: int
    action: Literal["approve", "modify", "reject", "hold"]
    updates: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    tasks: list[ReviewDecision] = Field(default_factory=list)
    facts: list[ReviewDecision] = Field(default_factory=list)


class FactReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class MilestoneOut(ORMModel):
    id: int
    project_id: int
    title: str
    description: str | None
    target_date: date | None
    status: str
    sort_order: int
