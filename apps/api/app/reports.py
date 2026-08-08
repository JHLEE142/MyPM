"""일일 업무 보고 초안 생성.

Slack #0-daily-report에 매일 올리는 형식(전일 / 금일 / 주간 목표 / 월간 목표,
각 줄은 `(진행률%)프로젝트 : 내용`)에 맞춰 MyPM 데이터에서 초안을 만든다.

수치는 전부 실제 데이터에서 계산한다. 근거가 없으면 빈 줄로 두고 사람이 채우게 한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from scheduler.progress import weighted_progress

from .models import Project, ScheduleVersion, Task, TaskEvent
from .services import leaf_task_query

DONE_EVENTS = {"completed", "progress_updated"}
SECTIONS = ("전일", "금일", "주간 목표", "월간 목표")


@dataclass
class ReportLine:
    project_id: int
    project: str
    percent: float | None
    items: list[str] = field(default_factory=list)

    def render(self) -> str:
        percent = "( — )" if self.percent is None else f"({round(self.percent)}%)"
        body = " / ".join(self.items) if self.items else "특이사항 없음"
        return f"{percent}{self.project} : {body}"


def previous_workday(target: date, work_days: set[int]) -> date:
    cursor = target - timedelta(days=1)
    for _ in range(14):
        if not work_days or cursor.weekday() in work_days:
            return cursor
        cursor -= timedelta(days=1)
    return target - timedelta(days=1)


def week_bounds(target: date) -> tuple[date, date]:
    monday = target - timedelta(days=target.weekday())
    return monday, monday + timedelta(days=6)


def month_bounds(target: date) -> tuple[date, date]:
    first = target.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first, next_month - timedelta(days=1)


def _latest_snapshot(db: Session, project_id: int) -> dict[str, Any]:
    version = db.scalar(
        select(ScheduleVersion)
        .where(ScheduleVersion.project_id == project_id)
        .order_by(ScheduleVersion.version.desc())
        .limit(1)
    )
    return (version.schedule_snapshot or {}) if version else {}


def _placements_by_date(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for placement in snapshot.get("placements", []):
        key = str(placement.get("date", ""))[:10]
        grouped.setdefault(key, []).append(placement)
    return grouped


def _events_between(db: Session, task_ids: list[int], start: date, end: date) -> list[TaskEvent]:
    if not task_ids:
        return []
    begin = datetime.combine(start, time.min, tzinfo=timezone.utc)
    finish = datetime.combine(end, time.max, tzinfo=timezone.utc)
    return list(
        db.scalars(
            select(TaskEvent)
            .where(
                TaskEvent.task_id.in_(task_ids),
                TaskEvent.event_type.in_(sorted(DONE_EVENTS)),
                TaskEvent.created_at >= begin,
                TaskEvent.created_at <= finish,
            )
            .order_by(TaskEvent.id)
        )
    )


def _progress_from_event(event: TaskEvent) -> float | None:
    try:
        payload = json.loads(event.new_value or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    value = payload.get("progress_percent")
    return float(value) if isinstance(value, (int, float)) else None


def _hours_ratio(placements: list[dict[str, Any]], tasks: dict[int, Task]) -> float | None:
    """구간에 배치된 공수 대비 실제 진척(진행률 가중)."""
    total = 0.0
    done = 0.0
    for placement in placements:
        task = tasks.get(placement.get("task_id"))
        if task is None:
            continue
        hours = float(placement.get("hours") or 0)
        total += hours
        done += hours * min(100.0, max(0.0, task.progress_percent)) / 100
    return round(done / total * 100, 1) if total > 0 else None


def _titles(task_ids: list[int], tasks: dict[int, Task], limit: int = 3, *, open_only: bool = False) -> list[str]:
    """업무 제목을 중복 없이 추린다. open_only면 완료된 업무는 뺀다(목표 칸에는 남은 일만 적는다)."""
    seen: list[str] = []
    for task_id in task_ids:
        task = tasks.get(task_id)
        if task is None or task.title in seen:
            continue
        if open_only and task.status == "completed":
            continue
        seen.append(task.title)
        if len(seen) >= limit:
            break
    return seen


def _next_open_tasks(tasks: dict[int, Task], limit: int = 3) -> list[str]:
    """일정에 배치된 미완료 업무가 없을 때 쓰는 대안: 진행 중 → 남은 업무 순."""
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    candidates = [task for task in tasks.values() if task.status != "completed"]
    candidates.sort(
        key=lambda task: (
            0 if task.status == "in_progress" else 1,
            priority_rank.get(task.priority, 2),
            task.sort_order,
        )
    )
    return [task.title for task in candidates[:limit]]


def build_daily_report(db: Session, target: date | None = None) -> dict[str, Any]:
    """대상 날짜의 보고 초안 데이터를 만든다."""
    target = target or date.today()
    projects = list(db.scalars(select(Project).where(Project.status == "active").order_by(Project.id)))
    week_start, week_end = week_bounds(target)
    month_start, month_end = month_bounds(target)

    sections: dict[str, list[ReportLine]] = {name: [] for name in SECTIONS}
    for project in projects:
        tasks = {task.id: task for task in db.scalars(leaf_task_query(project.id))}
        snapshot = _latest_snapshot(db, project.id)
        by_date = _placements_by_date(snapshot)
        work_days = set(project.work_days or [0, 1, 2, 3, 4])
        yesterday = previous_workday(target, work_days)

        def placements_in(start: date, end: date) -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            cursor = start
            while cursor <= end:
                found.extend(by_date.get(cursor.isoformat(), []))
                cursor += timedelta(days=1)
            return found

        # 전일은 "실제로 한 일"이므로 그 날 기록된 이벤트만 쓴다. 배치표로 대체하지 않는다
        # (일정이 오래된 버전이면 이미 끝낸 업무가 어제 한 일처럼 보이기 때문).
        yesterday_events = _events_between(db, list(tasks), yesterday, yesterday)
        percent_values = [value for value in map(_progress_from_event, yesterday_events) if value is not None]
        sections["전일"].append(
            ReportLine(
                project.id,
                project.name,
                round(sum(percent_values) / len(percent_values), 1) if percent_values else None,
                _titles([event.task_id for event in yesterday_events], tasks),
            )
        )

        # 금일·주간·월간은 "앞으로 할 일"이므로 완료된 업무는 빼고, 진행률은 구간 전체 기준으로 낸다.
        today_placements = by_date.get(target.isoformat(), [])
        today_items = _titles([item["task_id"] for item in today_placements], tasks, open_only=True)
        sections["금일"].append(
            ReportLine(
                project.id,
                project.name,
                _hours_ratio(today_placements, tasks),
                today_items or _next_open_tasks(tasks),
            )
        )

        # 주간·월간의 퍼센트는 프로젝트 전체 진행률을 쓴다. 구간 배치 대비 비율을 쓰면
        # 일정 버전이 오래됐을 때(그 구간에 완료 업무만 남아 있을 때) 100%로 잘못 나온다.
        overall = round(weighted_progress(list(tasks.values())), 1) if tasks else None

        week_placements = placements_in(week_start, week_end)
        week_items = _titles([item["task_id"] for item in week_placements], tasks, limit=4, open_only=True)
        sections["주간 목표"].append(
            ReportLine(project.id, project.name, overall, week_items or _next_open_tasks(tasks, limit=4))
        )

        month_placements = placements_in(month_start, month_end)
        month_items = _titles([item["task_id"] for item in month_placements], tasks, limit=4, open_only=True)
        month_items = month_items or _next_open_tasks(tasks, limit=4)
        if month_start <= project.target_date <= month_end:
            month_items.insert(0, f"{project.target_date.strftime('%-m월 %-d일')} 오픈/마감")
        sections["월간 목표"].append(ReportLine(project.id, project.name, overall, month_items))

    return {
        "date": target.isoformat(),
        "previous_date": previous_workday(target, {0, 1, 2, 3, 4}).isoformat(),
        "week": [week_start.isoformat(), week_end.isoformat()],
        "month": [month_start.isoformat(), month_end.isoformat()],
        "sections": {
            name: [
                {
                    "project_id": line.project_id,
                    "project": line.project,
                    "percent": line.percent,
                    "items": line.items,
                    "text": line.render(),
                }
                for line in lines
            ]
            for name, lines in sections.items()
        },
    }


def render_report_text(report: dict[str, Any]) -> str:
    """Slack에 그대로 붙여넣을 수 있는 형식으로 만든다."""
    blocks = ["% - 각 기간별 대비 진행률>"]
    for name in SECTIONS:
        blocks.append(f"\n{name}")
        lines = report["sections"].get(name, [])
        if lines:
            blocks.extend(line["text"] for line in lines)
        else:
            blocks.append("(내용 없음)")
    return "\n".join(blocks)
