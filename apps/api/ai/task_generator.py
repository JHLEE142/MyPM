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


def build_hierarchical_task_prompt(analysis: ProjectAnalysis, context: dict | None = None) -> str:
    context_line = ""
    if context:
        context_line = (
            f"컨텍스트: 오늘은 {context.get('today')}이고, 프로젝트 '{context.get('project_name')}'의 기간은 "
            f"{context.get('start_date')} ~ {context.get('target_date')}이다. "
            "모든 시기(월·주차·날짜)는 이 기간 안에서 배치하라. "
            f"오늘({context.get('today')}) 이전 날짜의 due_date나 이미 지난 주차는 절대 만들지 마라. "
            "첫 주차는 오늘이 속한 주부터 시작하라.\n"
        )
    return (
        context_line
        + "프로젝트 분석 결과를 실행 가능한 3단계 업무 트리로 변환하라. "
        "먼저 월간 업무를 정하고, 각 월간 업무에 맞춰 필요한 주간 업무를 뽑고, "
        "각 주간 업무를 위한 일간 업무를 만들어라.\n"
        "- 월간 업무: 프로젝트 시작월부터 목표월까지 모든 달력 월을 하나씩 커버하고 target_month(YYYY-MM)를 반드시 지정하라. "
        "제목은 '8월: 자료 요청·파일럿 범위 확정'처럼 그 달의 핵심 목표를 담아라. "
        "준비 단계뿐 아니라 구축·견적, 오픈, 오픈 이후 운영·개선(오류 답변 수정, 미답변 질문 반영, KPI 리포트) 단계까지 나눠라.\n"
        "- 주간 업무: 각 월간 아래 2개 이상, target_week_start(그 주 월요일 날짜)를 지정하고, 제목에 주차와 목표를 담아라 "
        "(예: '8월 2주차: 자료 요청 리스트 확정과 미팅 일정 잡기'). "
        "자료 요청 발송, 회신 취합, 파일럿 범위 확정, 견적안 작성, 주간 진행 리포트처럼 실제 주 단위 산출물이 있어야 한다.\n"
        "- 일간 업무: 각 주간 아래 최소 3개, 최대 8개의 구체적 실행 항목으로 만들고, due_date를 해당 주 안의 날짜로 지정하라. "
        "매주 반복되는 점검 업무(예: 슬랙·노션 미확인 질문 확인, 요청 자료 회신 체크, 리스크성 질문 분류)는 "
        "해당하는 모든 주차의 일간 업무로 반복해서 넣어라. "
        "회의록·문서의 결정사항, 보류사항, 요청 자료 목록, 리스크 대응(예: 판례·하자 책임 단정·분쟁성 민원 분류), "
        "반복 점검 업무(예: 회신 현황·진행상황·누락 데이터 일일 확인), 보고·리포트 작성도 빠짐없이 업무로 만들어라. "
        "문서에 근거가 있는 실행 항목을 요약해 버리지 말고 개별 업무로 나눠라.\n"
        "일간 업무에만 estimated_hours와 source_references를 부여하라. "
        "source_references의 source_id와 block_id는 아래 PROJECT ANALYSIS DATA에 실제로 존재하는 정수 값만 사용하라. "
        "모든 title과 description은 반드시 한국어로 작성하라. "
        "원문이 영어이거나 다른 언어여도 내용을 한국어로 요약·번역하라. "
        "문서에 없는 사실이나 출처는 만들지 말고 JSON만 반환하라.\n\n"
        "다음 스키마를 정확히 따라라:\n"
        f"{json.dumps(HierarchicalTaskSet.model_json_schema(), ensure_ascii=False)}\n\n"
        "PROJECT ANALYSIS DATA:\n"
        f"{json.dumps(analysis.model_dump(mode='json'), ensure_ascii=False)}"
    )


def generate_tasks(analysis: ProjectAnalysis, context: dict | None = None) -> HierarchicalTaskSet:
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
