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

    dates_with_sources: dict[str, set[int]] = {}
    for fixed in result.fixed_dates:
        dates_with_sources.setdefault(fixed.date.isoformat(), set()).add(fixed.source_block_id)
    if len(dates_with_sources) > 1:
        details = ", ".join(
            f"{fixed_date} (출처 블록 {', '.join(map(str, sorted(source_ids)))})"
            for fixed_date, source_ids in sorted(dates_with_sources.items())
        )
        first_source = min(source_id for source_ids in dates_with_sources.values() for source_id in source_ids)
        result.conflicts.append(
            ExtractedItem(
                content=f"프로젝트 마감일 충돌: {details}",
                source_block_id=first_source,
                confidence=1.0,
            )
        )
    return result
