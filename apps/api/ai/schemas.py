from __future__ import annotations

from datetime import date

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceReference(BaseModel):
    source_id: int
    block_id: int


class ExtractedItem(BaseModel):
    content: str = Field(min_length=1)
    source_block_id: int
    confidence: float = Field(default=0.7, ge=0, le=1)


class FixedDateItem(ExtractedItem):
    date: date


class TaskCandidate(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    milestone: str | None = None
    priority: str = "medium"
    estimated_hours: float = Field(default=1.0, ge=0, le=10000)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(min_length=1)
    confidence: float = Field(default=0.7, ge=0, le=1)
    due_date: date | None = None

    @model_validator(mode="after")
    def due_date_in_range(self):
        if self.due_date is not None and not date(1970, 1, 1) <= self.due_date <= date(2100, 12, 31):
            raise ValueError("due_date must be between 1970-01-01 and 2100-12-31")
        return self


class DocumentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_summary: str
    goals: list[ExtractedItem] = Field(default_factory=list)
    deliverables: list[ExtractedItem] = Field(default_factory=list)
    fixed_dates: list[FixedDateItem] = Field(default_factory=list)
    requirements: list[ExtractedItem] = Field(default_factory=list)
    task_candidates: list[TaskCandidate] = Field(default_factory=list)
    risks: list[ExtractedItem] = Field(default_factory=list)
    open_questions: list[ExtractedItem] = Field(default_factory=list)


class ProjectAnalysis(BaseModel):
    document_summaries: list[str] = Field(default_factory=list)
    goals: list[ExtractedItem] = Field(default_factory=list)
    deliverables: list[ExtractedItem] = Field(default_factory=list)
    fixed_dates: list[FixedDateItem] = Field(default_factory=list)
    requirements: list[ExtractedItem] = Field(default_factory=list)
    task_candidates: list[TaskCandidate] = Field(default_factory=list)
    risks: list[ExtractedItem] = Field(default_factory=list)
    open_questions: list[ExtractedItem] = Field(default_factory=list)
    conflicts: list[ExtractedItem] = Field(default_factory=list)


class GeneratedTask(TaskCandidate):
    pass


class GeneratedTaskSet(BaseModel):
    tasks: list[GeneratedTask]

    @model_validator(mode="after")
    def dependencies_reference_tasks(self):
        titles = {task.title for task in self.tasks}
        invalid = {dep for task in self.tasks for dep in task.dependencies if dep not in titles}
        if invalid:
            raise ValueError(f"dependencies reference missing tasks: {sorted(invalid)}")
        return self


class DraftTaskCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    estimated_hours: float = Field(ge=0, le=10000)
    priority: Literal["critical", "high", "medium", "low"] = "medium"


class DraftFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    goal: str | None = Field(default=None, max_length=4000)
    start_date: date | None = None
    target_date: date | None = None
    work_days: list[int] | None = None
    daily_capacity_hours: float | None = Field(default=None, gt=0, le=24)
    buffer_ratio: float | None = Field(default=None, ge=0, le=0.9)
    excluded_dates: list[date] | None = Field(default=None, max_length=200)
    task_candidates: list[DraftTaskCandidate] | None = Field(default=None, max_length=100)

    @field_validator("start_date", "target_date")
    @classmethod
    def date_in_range(cls, value: date | None) -> date | None:
        if value is not None and not date(1970, 1, 1) <= value <= date(2100, 12, 31):
            raise ValueError("date must be between 1970-01-01 and 2100-12-31")
        return value

    @field_validator("excluded_dates")
    @classmethod
    def excluded_dates_in_range(cls, values: list[date] | None) -> list[date] | None:
        if values is not None and any(not date(1970, 1, 1) <= value <= date(2100, 12, 31) for value in values):
            raise ValueError("excluded_dates must be between 1970-01-01 and 2100-12-31")
        return sorted(set(values)) if values is not None else None

    @field_validator("work_days")
    @classmethod
    def valid_work_days(cls, values: list[int] | None) -> list[int] | None:
        if values is not None and (not values or any(value < 0 or value > 6 for value in values)):
            raise ValueError("work_days must contain weekday numbers from 0 to 6")
        return sorted(set(values)) if values is not None else None


class DraftChatResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=8000)
    updated_fields: DraftFields = Field(default_factory=DraftFields)
    completeness_percent: float = Field(ge=0, le=100)
    next_question: str | None = None
