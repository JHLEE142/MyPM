from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, ensure_schema
from app.models import Project, Task, TaskDependency
from app.services import generate_schedule


DEMO_TODAY = date(2026, 8, 6)


DEMO_PROJECTS = [
    {
        "name": "PacePM 베타 출시",
        "owner": "이정현",
        "description": "핵심 프로젝트 관리 흐름을 안정화하고 베타 버전을 출시합니다.",
        "start_date": date(2026, 8, 1),
        "target_date": date(2026, 8, 31),
        "daily_capacity_hours": 4.0,
        "tasks": [
            ("베타 요구사항 확정", 4, "critical", []),
            ("프로젝트 생성 흐름 개선", 6, "high", [0]),
            ("설정 화면 구현", 3, "medium", [1]),
            ("진행 그래프 구현", 5, "high", [1]),
            ("통합 테스트", 8, "critical", [2, 3]),
            ("사용자 가이드 정리", 2, "low", [4]),
            ("배포 점검", 4, "high", [4]),
            ("베타 피드백 반영", 6, "medium", [5, 6]),
        ],
        "states": {
            0: ("completed", 100, 3.5, date(2026, 8, 3)),
            1: ("completed", 100, 6.5, date(2026, 8, 3)),
            2: ("completed", 100, 2.5, date(2026, 8, 4)),
            3: ("completed", 100, 5.0, date(2026, 8, 5)),
            4: ("in_progress", 60, 4.5, None),
        },
    },
    {
        "name": "고객사 A SI 구축",
        "owner": "김서연",
        "description": "고객사 핵심 업무 시스템을 구축하고 데이터 이관을 완료합니다.",
        "start_date": date(2026, 7, 20),
        "target_date": date(2026, 9, 4),
        "daily_capacity_hours": 1.9,
        "tasks": [
            ("착수 및 범위 합의", 8, "critical", []),
            ("현행 프로세스 분석", 6, "high", [0]),
            ("화면 설계", 7, "high", [1]),
            ("인증 모듈 개발", 5, "medium", [1]),
            ("업무 API 개발", 8, "high", [2, 3]),
            ("데이터 이관 스크립트", 4, "medium", [1]),
            ("외부 연동 개발", 6, "high", [4]),
            ("통합 테스트", 7, "critical", [5, 6]),
            ("사용자 교육", 5, "low", [7]),
            ("운영 전환", 8, "critical", [7, 8]),
        ],
        "states": {
            0: ("completed", 100, 8.0, date(2026, 7, 30)),
            1: ("completed", 100, 7.0, date(2026, 8, 4)),
            2: ("in_progress", 30, 2.0, None),
            3: ("blocked", 10, 1.0, None),
        },
        "overdue": {2: date(2026, 8, 5), 3: date(2026, 8, 5)},
    },
    {
        "name": "사내 문서 자동화 PoC",
        "owner": "박민준",
        "description": "반복 문서 업무 자동화의 기술 타당성과 효과를 검증합니다.",
        "start_date": date(2026, 7, 27),
        "target_date": date(2026, 8, 12),
        "daily_capacity_hours": 4.0,
        "tasks": [
            ("대상 문서 선정", 3, "high", []),
            ("샘플 데이터 정제", 5, "medium", [0]),
            ("추출 파이프라인 구현", 8, "critical", [1]),
            ("문서 생성 템플릿", 6, "high", [1]),
            ("정확도 평가", 7, "critical", [2, 3]),
            ("PoC 결과 보고", 4, "medium", [4]),
        ],
        "states": {0: ("completed", 100, 3.0, date(2026, 8, 4))},
    },
    {
        "name": "홈페이지 리뉴얼",
        "owner": "최윤아",
        "description": "브랜드 메시지와 전환 흐름을 중심으로 회사 홈페이지를 개편합니다.",
        "start_date": date(2026, 7, 10),
        "target_date": date(2026, 8, 14),
        "daily_capacity_hours": 2.5,
        "tasks": [
            ("콘텐츠 인벤토리", 4, "medium", []),
            ("정보 구조 설계", 6, "high", [0]),
            ("메인 비주얼 디자인", 8, "high", [1]),
            ("반응형 퍼블리싱", 7, "critical", [2]),
            ("CMS 연동", 6, "medium", [3]),
            ("SEO 메타 정비", 4, "low", [1]),
            ("접근성 및 QA", 7, "high", [4, 5]),
            ("최종 오픈 점검", 5, "critical", [6]),
        ],
        "states": {
            0: ("completed", 100, 4.0, date(2026, 7, 20)),
            1: ("completed", 100, 6.0, date(2026, 7, 23)),
            2: ("completed", 100, 8.5, date(2026, 7, 27)),
            3: ("completed", 100, 7.0, date(2026, 7, 30)),
            4: ("completed", 100, 6.0, date(2026, 8, 3)),
            5: ("completed", 100, 3.5, date(2026, 8, 4)),
            6: ("completed", 100, 7.5, date(2026, 8, 5)),
            7: ("in_progress", 80, 4.0, None),
        },
    },
    {
        "name": "데이터 파이프라인 개선",
        "owner": "이정현",
        "description": "수집 안정성과 처리 시간을 개선하고 운영 관측성을 강화합니다.",
        "start_date": DEMO_TODAY,
        "target_date": date(2026, 9, 4),
        "daily_capacity_hours": 4.0,
        "tasks": [
            ("병목 구간 계측", 4, "high", []),
            ("수집 재시도 정책", 6, "critical", [0]),
            ("변환 작업 최적화", 8, "high", [0]),
            ("데이터 품질 알림", 5, "medium", [1, 2]),
            ("운영 대시보드 정리", 3, "low", [3]),
        ],
        "states": {},
    },
]


def _create_demo_project(db: Session, spec: dict) -> Project:
    project = Project(
        name=spec["name"],
        owner=spec["owner"],
        description=spec["description"],
        start_date=spec["start_date"],
        target_date=spec["target_date"],
        work_days=[0, 1, 2, 3, 4],
        daily_capacity_hours=spec["daily_capacity_hours"],
        buffer_ratio=0.2,
        excluded_dates=[],
        status="active",
    )
    db.add(project)
    db.flush()

    tasks: list[Task] = []
    for index, (title, hours, priority, _) in enumerate(spec["tasks"]):
        task = Task(
            project_id=project.id,
            title=title,
            estimated_hours=float(hours),
            priority=priority,
            status="approved",
            due_date=spec.get("overdue", {}).get(index),
        )
        db.add(task)
        tasks.append(task)
    db.flush()
    for index, (_, _, _, dependency_indexes) in enumerate(spec["tasks"]):
        for dependency_index in dependency_indexes:
            db.add(TaskDependency(task_id=tasks[index].id, depends_on_task_id=tasks[dependency_index].id))
    db.commit()

    generate_schedule(db, project, "데모 초기 일정", as_of=project.start_date)
    for index, (status, progress, actual_hours, actual_end_date) in spec["states"].items():
        task = tasks[index]
        task.status = status
        task.progress_percent = float(progress)
        task.actual_hours = float(actual_hours)
        if progress > 0:
            task.actual_start_date = actual_end_date or DEMO_TODAY
        if actual_end_date:
            task.actual_end_date = actual_end_date
    db.commit()
    return project


def seed_demo(db: Session | None = None) -> list[Project]:
    ensure_schema()
    owns_session = db is None
    session = db or SessionLocal()
    created: list[Project] = []
    try:
        for spec in DEMO_PROJECTS:
            existing = session.scalar(select(Project).where(Project.name == spec["name"]).limit(1))
            if existing:
                print(f"건너뜀: {spec['name']} (같은 이름의 프로젝트가 이미 있습니다)")
                continue
            project = _create_demo_project(session, spec)
            created.append(project)
            print(f"생성: {project.name} (프로젝트 #{project.id})")
        return created
    finally:
        if owns_session:
            session.close()


if __name__ == "__main__":
    seeded = seed_demo()
    print(f"완료: 데모 프로젝트 {len(seeded)}개 생성")
