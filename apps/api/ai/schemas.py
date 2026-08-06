from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator


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
