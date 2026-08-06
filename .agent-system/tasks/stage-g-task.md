# Codex Stage G — 계층 업무(월간→주간→일간) + 일정 체이닝 수정 + 한국어 출력

당신은 PacePM 구현 매니저다. 완료 기준: `apps/api/.venv/bin/python -m pytest` 전건 통과(기존 98건 회귀 없음), `cd apps/web && npm run build` 성공. 실제 LLM 호출 테스트 금지(mock/monkeypatch).

## A. 계층 업무 구조 (핵심 신기능)

문서 분석으로 업무가 생성될 때, **월간 업무를 먼저 정하고 → 각 월간 업무에 필요한 주간 업무를 도출하고 → 각 주간 업무를 위한 일간 업무**를 만들어야 한다. 화면에는 글머리(bullet) 트리로 표시한다.

A1. 모델: `tasks.cadence` String(10) nullable 컬럼 추가 (`monthly`/`weekly`/`daily`/NULL=기존 flat). ensure_schema에 SQLite ALTER 보정. TaskOut/shared-types에 cadence 반영.

A2. AI 스키마(ai/schemas.py): 계층 생성용 신규 스키마 —
```
DailyTaskItem: title, description="", estimated_hours(0~10000, 리프가 공수 보유), priority, due_date?, source_references(min 1), confidence
WeeklyTaskItem: title, description="", daily: list[DailyTaskItem] (min 1)
MonthlyTaskItem: title, description="", target_month?("YYYY-MM"), weekly: list[WeeklyTaskItem] (min 1)
HierarchicalTaskSet: monthly: list[MonthlyTaskItem] (min 1)
```
extra="forbid". 검증: 순환 없음(트리라 자동), 월간≤12, 주간/월간≤10, 일간/주간≤15.

A3. task_generator: ProjectAnalysis → HierarchicalTaskSet 생성.
- Anthropic/OpenAI 경로: 프롬프트에 "먼저 월간 업무를 정하고, 각 월간 업무에 맞춰 필요한 주간 업무를 뽑고, 각 주간 업무를 위한 일간 업무를 만들어라. 일간 업무에만 estimated_hours와 source_references를 부여하라" 지시. **모든 title/description은 반드시 한국어로** (문서가 영어여도 한국어로 요약·번역) — 문서 분석 프롬프트에도 동일하게 한국어 출력 지시 추가.
- Mock 경로(결정론): 추출된 task_candidates를 due_date 월(없으면 순서 기준 균등 분할)로 월간 그룹핑("N월 업무" 또는 첫 태스크 제목 기반), 월간당 주 단위로 주간 그룹("N주차: ..."), 원래 후보들이 일간 업무가 됨. 후보가 1개뿐이어도 월간1-주간1-일간1 구조 생성.

A4. 저장(run_analysis): monthly → weekly → daily 순서로 Task 생성, parent_task_id 연결, cadence 세팅. 전부 ai_generated=True, 자동승인 시 approved. 상위(월간/주간) estimated_hours는 하위 합계로 저장. source_links는 일간에 연결(상위는 첫 일간 것 복사 1개). 기존 마일스톤 로직은 월간 업무 제목으로 Milestone 생성·연결 유지.

A5. 컨테이너 규칙(이중 계산 방지 — 중요):
- **자식이 있는 task(월간/주간)는 일정 엔진 배치·진도 가중 합계·오늘 화면·남은 공수 계산에서 제외**하고 리프(일간·flat)만 포함. services의 eligible/progress 대상 쿼리에 "자식 없는 task" 필터 추가(children 존재 여부 서브쿼리).
- 상위 진도율은 저장하지 않고 하위에서 파생: TaskOut에 계산 필드 없이, 프론트에서 children 합산으로 표시.
- complete/reopen 체크는 리프만 허용(자식 있는 task에 complete 호출 시 409 "하위 업무를 완료하세요").
- 부모 삭제 시 하위 전체 재귀 삭제(DELETE /api/tasks/{id}).

A6. 프론트(SchedulePanel 업무 목록): 글머리 트리 렌더링 —
```
● 월간 업무 제목  (파생 진도 N% · 총 Nh)
   ◦ 주간 업무 제목  (N% · Nh)
      · [체크박스] 일간 업무 제목  Nh  [삭제]
```
- 월간/주간 행: 체크박스 없음, 파생 진도 %와 합계 시간, 삭제 버튼(하위 포함 삭제 확인창).
- 일간/flat 행: 기존 체크박스·삭제 유지.
- flat 업무(cadence NULL, parent 없음)는 "기타 업무" 그룹으로 트리 아래 표시.
- 직접 추가 폼은 기존대로 flat 업무 생성(변경 없음).

## B. 일정 체이닝 수정 (QA 확인 버그)

B1. `generate_schedule`의 start_date를 `max(project.start_date, date.today())`로 클램프 (replan과 동일하게). as_of 파라미터로 주입 가능하게 해 테스트 결정론 유지.
B2. 일정 스테일 표시: /plan과 /today 페이지에서 `project.updated_at > 최신 schedule_version.created_at`이면 상단에 경고 배너 "프로젝트 설정이 변경되었습니다 — 일정을 다시 생성해야 반영됩니다" + 일정 생성 버튼으로 이동 링크. (versions 목록의 최신 created_at과 비교; 버전 없으면 표시 안 함)

## C. 테스트

- 계층 생성: mock 분석 → 월간/주간/일간 3단 트리 생성, cadence·parent 연결, 상위 공수=하위 합, 자동승인 approved.
- 이중 계산 방지: 계층 프로젝트에서 progress/pace가 리프 공수만 집계, schedule/generate placements에 상위 task 미포함.
- 부모 complete 409, 부모 삭제 시 하위 전체 삭제.
- B1 회귀: start_date 과거 + generate → placements가 오늘부터.
- 프롬프트에 한국어 지시 포함 여부(문자열 검증 수준).
- 기존 98건 회귀 없음.

## 제약
- venv 새 패키지 금지. 실 LLM 호출 금지. 기존 flat 업무 플로우(직접 추가·draft confirm) 동작 유지.
- 최종 보고: 항목별 done/부분/skip, pytest·build 결과.
