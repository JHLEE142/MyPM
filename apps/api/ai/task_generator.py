from __future__ import annotations

from .schemas import GeneratedTask, GeneratedTaskSet, ProjectAnalysis
from scheduler.dependencies import assert_acyclic


def generate_tasks(analysis: ProjectAnalysis) -> GeneratedTaskSet:
    tasks = [GeneratedTask.model_validate(item.model_dump()) for item in analysis.task_candidates]
    by_title = {task.title: index for index, task in enumerate(tasks)}
    edges = [(index, by_title[dependency]) for index, task in enumerate(tasks) for dependency in task.dependencies]
    assert_acyclic(range(len(tasks)), edges)
    return GeneratedTaskSet(tasks=tasks)
