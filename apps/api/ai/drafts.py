from __future__ import annotations

from typing import Any

from .schemas import DraftFields


REQUIRED_DRAFT_FIELDS = ("name", "start_date", "target_date", "daily_capacity_hours")
OPTIONAL_DRAFT_FIELDS = (
    "description",
    "goal",
    "work_days",
    "buffer_ratio",
    "excluded_dates",
    "task_candidates",
)


def merge_draft_fields(current: dict[str, Any], updates: DraftFields | dict[str, Any]) -> dict[str, Any]:
    validated = updates if isinstance(updates, DraftFields) else DraftFields.model_validate(updates)
    merged = dict(current)
    for key, value in validated.model_dump(mode="json", exclude_none=True).items():
        merged[key] = value
    return DraftFields.model_validate(merged).model_dump(mode="json", exclude_none=True)


def calculate_completeness(fields: dict[str, Any] | DraftFields) -> float:
    values = fields.model_dump(mode="json", exclude_none=True) if isinstance(fields, DraftFields) else fields
    required_score = sum(_is_filled(values, field) for field in REQUIRED_DRAFT_FIELDS) * 15.0
    optional_weight = 40.0 / len(OPTIONAL_DRAFT_FIELDS)
    optional_score = sum(_is_filled(values, field) for field in OPTIONAL_DRAFT_FIELDS) * optional_weight
    return round(min(100.0, required_score + optional_score), 1)


def missing_required_fields(fields: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_DRAFT_FIELDS if not _is_filled(fields, field)]


def _is_filled(fields: dict[str, Any], name: str) -> bool:
    if name not in fields or fields[name] is None:
        return False
    value = fields[name]
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
