from __future__ import annotations

from .schemas import ExtractedItem, GeneratedTask, GeneratedTaskSet, ProjectAnalysis
from scheduler.dependencies import assert_acyclic


def generate_tasks(analysis: ProjectAnalysis) -> GeneratedTaskSet:
    tasks = [GeneratedTask.model_validate(item.model_dump()) for item in analysis.task_candidates]
    by_title = {task.title: index for index, task in enumerate(tasks)}
    for task in tasks:
        valid_dependencies: list[str] = []
        for dependency in task.dependencies:
            if dependency in by_title:
                valid_dependencies.append(dependency)
                continue
            analysis.open_questions.append(
                ExtractedItem(
                    content=f"업무 '{task.title}'의 선행 업무 '{dependency}'를 찾을 수 없어 의존관계에서 제외했습니다.",
                    source_block_id=task.source_references[0].block_id,
                    confidence=task.confidence,
                )
            )
        task.dependencies = valid_dependencies
    edges = [(index, by_title[dependency]) for index, task in enumerate(tasks) for dependency in task.dependencies]
    assert_acyclic(range(len(tasks)), edges)
    return GeneratedTaskSet(tasks=tasks)
