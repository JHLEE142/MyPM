from __future__ import annotations

from .schemas import DocumentAnalysis, ExtractedItem, ProjectAnalysis, TaskCandidate


def merge_project_analyses(analyses: list[DocumentAnalysis]) -> ProjectAnalysis:
    result = ProjectAnalysis(document_summaries=[item.document_summary for item in analyses])
    for field in ("goals", "deliverables", "fixed_dates", "requirements", "risks", "open_questions"):
        seen: set[str] = set()
        merged = []
        for analysis in analyses:
            for item in getattr(analysis, field):
                key = item.content.strip().casefold()
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
        setattr(result, field, merged)
    tasks: list[TaskCandidate] = []
    task_titles: set[str] = set()
    for analysis in analyses:
        for task in analysis.task_candidates:
            key = task.title.strip().casefold()
            if key not in task_titles:
                task_titles.add(key)
                tasks.append(task)
    result.task_candidates = tasks

    date_contents: dict[str, set[str]] = {}
    date_sources: dict[str, int] = {}
    for fixed in result.fixed_dates:
        label = fixed.content.split(":", 1)[0].strip().casefold()
        date_contents.setdefault(label, set()).add(fixed.date.isoformat())
        date_sources[label] = fixed.source_block_id
    for label, dates in sorted(date_contents.items()):
        if len(dates) > 1:
            result.conflicts.append(
                ExtractedItem(
                    content=f"마감일 충돌: {label or '날짜'}에 {', '.join(sorted(dates))}",
                    source_block_id=date_sources[label],
                    confidence=1.0,
                )
            )
    return result
