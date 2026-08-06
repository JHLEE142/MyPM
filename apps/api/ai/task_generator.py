from __future__ import annotations

import json
from collections import OrderedDict
from math import ceil

from scheduler.dependencies import assert_acyclic

from .schemas import (
    DailyTaskItem,
    ExtractedItem,
    GeneratedTask,
    HierarchicalTaskSet,
    MonthlyTaskItem,
    ProjectAnalysis,
    WeeklyTaskItem,
)


MAX_DAILY_PER_WEEK = 15
MAX_WEEKLY_PER_MONTH = 10
MAX_MONTHLY = 12
MAX_DAILY_TOTAL = MAX_DAILY_PER_WEEK * MAX_WEEKLY_PER_MONTH * MAX_MONTHLY


def build_hierarchical_task_prompt(analysis: ProjectAnalysis) -> str:
    return (
        "프로젝트 분석 결과를 실행 가능한 3단계 업무 트리로 변환하라. "
        "먼저 월간 업무를 정하고, 각 월간 업무에 맞춰 필요한 주간 업무를 뽑고, "
        "각 주간 업무를 위한 일간 업무를 만들어라. "
        "일간 업무에만 estimated_hours와 source_references를 부여하라. "
        "모든 title과 description은 반드시 한국어로 작성하라. "
        "원문이 영어이거나 다른 언어여도 내용을 한국어로 요약·번역하라. "
        "문서에 없는 사실이나 출처는 만들지 말고 JSON만 반환하라.\n\n"
        "다음 스키마를 정확히 따라라:\n"
        f"{json.dumps(HierarchicalTaskSet.model_json_schema(), ensure_ascii=False)}\n\n"
        "PROJECT ANALYSIS DATA:\n"
        f"{json.dumps(analysis.model_dump(mode='json'), ensure_ascii=False)}"
    )


def generate_tasks(analysis: ProjectAnalysis) -> HierarchicalTaskSet:
    """Mock/fallback path: deterministically arrange extracted candidates into three levels."""
    tasks = _validated_candidates(analysis)
    if not tasks:
        raise ValueError("계층 업무를 생성할 task_candidate가 없습니다")
    if len(tasks) > MAX_DAILY_TOTAL:
        raise ValueError(f"계층 업무는 일간 업무 {MAX_DAILY_TOTAL}개까지 생성할 수 있습니다")

    monthly_groups = _monthly_groups(tasks)
    monthly_items: list[MonthlyTaskItem] = []
    for target_month, month_tasks in monthly_groups:
        weekly_groups = _weekly_groups(month_tasks)
        if len(weekly_groups) > MAX_WEEKLY_PER_MONTH:
            raise ValueError("월간 업무 하나에 주간 업무는 10개까지 생성할 수 있습니다")

        weekly_items: list[WeeklyTaskItem] = []
        for week_number, week_tasks in enumerate(weekly_groups, start=1):
            daily_items = [
                DailyTaskItem(
                    title=task.title,
                    description=_daily_description(task),
                    estimated_hours=task.estimated_hours,
                    priority=task.priority if task.priority in {"critical", "high", "medium", "low"} else "medium",
                    due_date=task.due_date,
                    source_references=task.source_references,
                    confidence=task.confidence,
                )
                for task in week_tasks
            ]
            weekly_items.append(
                WeeklyTaskItem(
                    title=f"{week_number}주차: {week_tasks[0].title}",
                    description=f"{week_tasks[0].title}을(를) 포함한 일간 업무를 수행합니다.",
                    daily=daily_items,
                )
            )

        common_milestones = {task.milestone.strip() for task in month_tasks if task.milestone and task.milestone.strip()}
        if len(common_milestones) == 1:
            monthly_title = next(iter(common_milestones))
        elif target_month:
            monthly_title = f"{int(target_month[-2:])}월 업무"
        else:
            monthly_title = f"{month_tasks[0].title} 월간 업무"
        monthly_items.append(
            MonthlyTaskItem(
                title=monthly_title,
                description=_daily_description(month_tasks[0]),
                target_month=target_month,
                weekly=weekly_items,
            )
        )
    result = HierarchicalTaskSet(monthly=monthly_items)
    result._legacy_tasks = tasks
    return result


def _validated_candidates(analysis: ProjectAnalysis) -> list[GeneratedTask]:
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
    return tasks


def _monthly_groups(tasks: list[GeneratedTask]) -> list[tuple[str | None, list[GeneratedTask]]]:
    dated: OrderedDict[str, list[GeneratedTask]] = OrderedDict()
    undated: list[GeneratedTask] = []
    for task in tasks:
        if task.due_date is None:
            undated.append(task)
        else:
            dated.setdefault(task.due_date.strftime("%Y-%m"), []).append(task)

    if dated:
        if len(dated) > MAX_MONTHLY:
            raise ValueError("월간 업무는 12개까지 생성할 수 있습니다")
        keys = sorted(dated)
        for index, task in enumerate(undated):
            dated[keys[index % len(keys)]].append(task)
        return [(key, dated[key]) for key in keys]

    group_count = max(1, min(MAX_MONTHLY, ceil(len(tasks) / (MAX_DAILY_PER_WEEK * MAX_WEEKLY_PER_MONTH))))
    group_size = ceil(len(tasks) / group_count)
    return [
        (None, tasks[index : index + group_size])
        for index in range(0, len(tasks), group_size)
    ]


def _weekly_groups(tasks: list[GeneratedTask]) -> list[list[GeneratedTask]]:
    dated: OrderedDict[int, list[GeneratedTask]] = OrderedDict()
    undated: list[GeneratedTask] = []
    for task in tasks:
        if task.due_date is None:
            undated.append(task)
        else:
            week_of_month = (task.due_date.day - 1) // 7 + 1
            dated.setdefault(week_of_month, []).append(task)

    if not dated:
        return [tasks[index : index + MAX_DAILY_PER_WEEK] for index in range(0, len(tasks), MAX_DAILY_PER_WEEK)]

    buckets = [dated[key] for key in sorted(dated)]
    for index, task in enumerate(undated):
        buckets[index % len(buckets)].append(task)
    return [
        bucket[index : index + MAX_DAILY_PER_WEEK]
        for bucket in buckets
        for index in range(0, len(bucket), MAX_DAILY_PER_WEEK)
    ]


def _daily_description(task: GeneratedTask) -> str:
    description = task.description.strip()
    if task.acceptance_criteria:
        criteria = "\n".join(f"- {criterion}" for criterion in task.acceptance_criteria)
        description = f"{description}\n\n완료 기준:\n{criteria}" if description else f"완료 기준:\n{criteria}"
    return description
