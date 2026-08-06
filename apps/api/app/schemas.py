from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    start_date: date
    target_date: date
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    daily_capacity_hours: float = Field(default=8.0, gt=0)
    buffer_ratio: float = Field(default=0.2, ge=0, lt=1)
    excluded_dates: list[date] = Field(default_factory=list)
    status: str = "active"

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
    description: str | None = None
    start_date: date | None = None
    target_date: date | None = None
    work_days: list[int] | None = None
    daily_capacity_hours: float | None = Field(default=None, gt=0)
    buffer_ratio: float | None = Field(default=None, ge=0, lt=1)
    excluded_dates: list[date] | None = None
    status: str | None = None


class ProjectOut(ORMModel):
    id: int
    name: str
    description: str | None
    start_date: date
    target_date: date
    work_days: list[int]
    daily_capacity_hours: float
    buffer_ratio: float
    excluded_dates: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


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
    storage_path: str | None
    extracted_text: str | None
    analysis_status: str
    error_message: str | None
    uploaded_at: datetime
    blocks: list[SourceBlockOut] = Field(default_factory=list)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    milestone_id: int | None = None
    parent_task_id: int | None = None
    status: str = "approved"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    estimated_hours: float = Field(default=1.0, ge=0)
    actual_hours: float = Field(default=0.0, ge=0)
    progress_percent: float = Field(default=0.0, ge=0, le=100)
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    due_date: date | None = None
    locked: bool = False
    ai_generated: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    dependency_ids: list[int] = Field(default_factory=list)
    source_block_ids: list[int] = Field(default_factory=list)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    milestone_id: int | None = None
    parent_task_id: int | None = None
    status: str | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    estimated_hours: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0)
    progress_percent: float | None = Field(default=None, ge=0, le=100)
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    due_date: date | None = None
    locked: bool | None = None
    dependency_ids: list[int] | None = None


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
    title: str
    description: str | None
    status: str
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

