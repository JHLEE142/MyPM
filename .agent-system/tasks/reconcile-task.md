# Codex Stage C — 감사 반영 수정 (Reconcile)

4개 독립 감사(설계·QA·보안·코드리뷰)에서 교차 확인된 결함 목록이다. 전부 수정하고 회귀 테스트를 추가하라. docs/SPEC.md가 기준. 완료 후 `apps/api/.venv/bin/python -m pytest` 전건 통과 + `cd apps/web && npm run build` 성공 필수.

## A. 일정 엔진/서비스 (CRITICAL — 최우선)

A1. **완료된 선행업무 처리**: engine에서 `by_id`에 없는 dependency는 이미 충족된 것으로 간주하라. `generate_schedule`/`replan_schedule`은 완료 업무의 `actual_end_date`(없으면 시작일 전날)를 satisfied dependency의 종료일로 엔진에 전달해 후속 업무 earliest 계산에 쓰도록 하라. 재현: A 완료 후 schedule/generate → B가 dependency_unscheduled로 infeasible 되는 버그.

A2. **재계획 보호 업무 누락**: 이전 스냅샷에 배치가 없는 보호 업무(locked/완료/고정마감)가 조용히 사라진다. 보호 업무 중 reserved placement가 없는 미완료 업무는 일반 배치 대상으로 스케줄하고, 배치 불가면 반드시 `unscheduled`에 보고하라. `due_date != None`만으로 업무를 freeze하지 마라(마감일은 제약이지 배치 고정이 아니다). 완료 업무만 placement 보존 대상.

A3. **부분 배치된 선행업무**: 선행이 완전히 배치되지 못했으면(`hours_left > 0`) `task_end`를 설정하지 말고 후속 업무를 `dependency_unscheduled`로 보고하라.

A4. **0시간 선행업무**: estimated_hours=0인 업무는 earliest 가능한 작업일을 `task_end`로 설정해 후속을 막지 않게 하라.

A5. **재계획 잔여공수**: 진행 중 업무는 `estimated_hours × (1 - progress_percent/100)`만 재배정하라. snapshot placement에는 원래 공수와 잔여 공수를 구분 기록.

A6. **재생성 시 계획 이력 보존**: `_update_task_plan`은 완료 업무의 planned_* 를 건드리지 마라. 새 계획에서 미배치된 업무는 status를 `approved`로 되돌려라(`scheduled` 잔존 금지).

A7. **예정 진도율 기준선**: planned_progress는 최신 스냅샷이 아니라 **버전 1(최초 계획) 스냅샷 + 이후 버전에서 새로 추가된 업무의 배치**를 기준으로 계산하거나, 완료 업무의 공수를 완료일 기준으로 baseline에 포함해 재생성 시 비율이 튀지 않게 하라. 간단한 구현: 완료 업무는 actual_end_date(없으면 planned_end)에 해당 공수가 계획된 것으로 baseline에 합산.

A8. **가용시간 초과 경고 도달 가능하게**: dashboard의 capacity_warning은 `effective_daily_capacity_hours`(버퍼 적용) 기준으로 비교하고, 응답에 raw/effective 둘 다 포함하라. SPEC 4장 예시(가용 3h, 배정 3.5h → 30분 초과)가 실제로 발동해야 한다.

A9. **defer_low_priority 구현**: 전략 선택 시 low priority(및 미시작) 업무를 배치 대상에서 제외하고 snapshot에 `deferred: [{task_id, reason}]`로 기록. UI가 이월 목록을 표시할 수 있게 하라.

A10. **버전 번호 경쟁**: schedule_versions insert를 unique constraint 충돌 시 1회 재시도로 감싸라.

A11. **find_cycle 재귀 제거**: 반복(iterative) DFS로 바꿔 1000+ 체인에서 RecursionError가 나지 않게 하라.

## B. 승인 게이트/데이터 무결성 (CRITICAL)

B1. **status Literal + 전이 제한**: Task.status와 Project.status를 Pydantic Literal로 제한. AI 생성 업무(`ai_generated=true`)의 `pending_review → approved/rejected` 전이는 `/analysis/approve`에서만 허용하고 PATCH로는 422. `/complete`, `/block`도 pending_review/rejected 업무에는 409. `ai_generated`, `confidence`는 클라이언트 입력에서 제거(서버 전용) — TaskCreate/TaskPatch에서 삭제.

B2. **source 삭제 보호**: 승인된(approved 이후 상태) AI 업무 또는 승인된 fact가 참조하는 source_document 삭제 요청은 409 + 영향 목록 반환. 참조가 pending/rejected뿐이면 삭제 허용.

B3. **재분석 보호**: 분석 재실행 시 기존 AI 업무 중 (a) approved 이상 상태이거나 (b) 다른 승인 업무가 의존하는 업무는 삭제하지 마라. extracted/pending_review이고 참조되지 않는 것만 대체.

B4. **교차 프로젝트 참조 차단**: source_block_ids 검증에 SourceDocument join으로 `project_id` 일치 강제. milestone/parent/dependency 검증도 approve 경로 포함 전체 통일. parent 자기참조·순환 검사.

B5. **approve updates 검증**: fact updates를 Pydantic 모델(content: str|None, confidence: float 0~1)로 검증해 500 제거.

B6. **estimated_hours 상한**: le=10000, due_date는 1970~2100 범위 검증.

## C. 보안 (HIGH)

C1. **XLSX 파서**: `iter_rows()` 사용(셀 미생성), 로드 직후 `max_row*max_column > 200_000`이면 ValueError("셀 수 초과") → 분석 실패 안내. 워크북 이중 로드 제거 — `data_only=True` 한 번 로드로 계산값을 content에 쓰고, 수식 원문이 필요한 셀만 별도 기록 생략 가능(계산값 우선 — 감사 지적: AI에 수식 원문이 아닌 계산값이 가야 함). 헤더/시트명은 유지.

C2. **zip 상한**: hwpx/xlsx 열기 전 `ZipInfo.file_size` 합계 > 50MB 또는 압축비 > 100:1이면 ValueError. 블록당 content 64KB 절단, 문서당 블록 수 상한 5000.

C3. **업로드 스트리밍/선검사**: Content-Length가 있으면 read 전에 413. 확장자 검사를 read 전에(파일명 기준) 수행. multipart 파일은 청크 단위로 읽어 누적 초과 시 즉시 413. catch-all(`request.body()`) 브랜치 **제거** — multipart와 application/json만 허용, 그 외 415.

C4. **파싱 오프로딩**: create_source의 파일 저장·파싱을 `run_in_threadpool`로 실행.

C5. **Origin 방어**: 상태 변경 메서드(POST/PATCH/DELETE)에 대해 Origin 헤더가 존재하고 허용 목록(CORS_ORIGINS) 밖이면 403 미들웨어. Origin 없는 요청(curl 등)은 허용.

C6. **compose 하드닝**: 포트를 `127.0.0.1:8000:8000`, `127.0.0.1:3000:3000`으로. api Dockerfile에 비-root USER. CORS_ORIGINS가 `*`이고 allow_credentials면 기동 시 거부.

C7. **.dockerignore**: `**/.env`, `**/.env.*`, `!**/.env.example`로 수정.

C8. **에러 노출 제거**: 파서 실패 메시지에서 절대경로 제거(고정 문구 + 예외 클래스명). `SourceDocumentOut`에서 storage_path 제거. run.error_message는 str(exc) 대신 유형+요약 200자 절단.

C9. **저장 파일명**: 디스크 저장명은 uuid + 확장자만. 원본명은 DB 컬럼에 255자 절단 저장. 파일 write 실패/DB 실패 시 정리 순서 보장.

C10. **Anthropic 클라이언트**: `Anthropic(timeout=120, max_retries=2)`. 분석 입력 상한: 블록 총 문자 200,000자 초과 시 분석 실패(명확한 안내: "문서가 너무 큽니다. 분할 업로드해 주세요"). 전역 동시 분석 세마포어(2).

C11. **requirements 핀**: 현재 venv 설치 버전으로 `==` 고정(`.venv/bin/pip freeze` 참조, 직접 의존성만). pytest/httpx는 requirements-dev.txt로 분리(Dockerfile은 prod만 설치).

## D. AI 파이프라인

D1. `document_analyzer.py`의 `parsed` → `parsed_output` 필드명 수정.
D2. task_generator: 매칭 안 되는 dependency 제목은 KeyError 대신 드롭하고 open_question으로 기록.
D3. 잘못된 날짜 매치(2026-13-45 등)는 해당 항목만 skip, 전체 실패 금지.
D4. milestone 영속화: AI 결과의 milestone 문자열로 Milestone 행 생성(중복 병합), task.milestone_id 연결. acceptance_criteria는 task.description 끝에 "완료 기준:" 목록으로 병합. `GET /api/projects/{id}/milestones` 엔드포인트 추가. pace_status의 critical_milestone_failed 계산: 마일스톤 target_date가 지났는데 소속 업무 미완료면 true.
D5. 병합 충돌 탐지 개선: fixed_dates는 (라벨 정규화 없이) 날짜가 2개 이상 서로 다르면 프로젝트 단위 deadline 충돌 fact(`fact_type=constraint`, content에 두 날짜와 출처)로 기록.

## E. 프론트엔드

E1. shared-types 수정: `PaceStatus = "normal"|"warning"|"risk"|"critical"`, Forecast 필드를 실제 API(`work_days_observed`, `velocity_hours_per_day`, `remaining_work_days`)로.
E2. 검토 패널에 `요구사항(requirement)` 분류 탭 추가 + hold(보류) 액션 지원. UI에서 처리 가능한 fact_type과 완료 판정 일치.
E3. 계획/월간/다음 마일스톤 표시는 approved/scheduled/in_progress/completed 업무와 approved fact만 사용.
E4. today 체크 해제: 90% 하드코딩 제거 — 새 API `POST /api/tasks/{id}/reopen`(직전 완료 이벤트의 previous_value로 상태·진행률·actual_hours 복원)을 만들어 호출.
E5. 주간 그룹핑의 `toISOString()` 시간대 버그 수정(로컬 날짜 기반 키). dashboard/pace 호출 시 `as_of=로컬 오늘`을 명시 전달.
E6. locked 토글 UI 추가(업무 편집 폼에 잠금 체크박스).
E7. 재계획 시 deferred 목록 표시(A9 연동).

## F. 테스트 추가 (전부 pytest)

F1. 분석 실패 격리: provider가 예외를 던져도 기존 task/fact 불변 + run=failed.
F2. dashboard over-capacity: effective capacity 초과 시 over_capacity/excess_hours 응답 검증.
F3. 완료 후 재생성: 후속 업무 정상 배치(A1 회귀).
F4. 재계획: 이전 배치 없는 locked/due_date 업무가 배치되거나 unscheduled 보고(A2 회귀).
F5. 부분 배치 선행 + 0시간 선행(A3/A4).
F6. 잔여공수 재배정(A5).
F7. 재생성 후 planned_progress 불변(A7).
F8. PATCH로 pending_review → approved 시도 422(B1).
F9. 교차 프로젝트 source_block 403/422(B4).
F10. 승인 업무 참조 source 삭제 409(B2).
F11. AI 업무 review 응답에 source_links 비어있지 않음 검증.
F12. reopen API 복원 검증(E4).

## 제약
- 수정 범위를 위 목록으로 한정. 새 기능 추가 금지.
- 결정론 유지(랜덤/현재시각 의존 정렬 금지).
- venv에 새 패키지 설치 불가.
- 완료 후 최종 메시지에: 항목별 처리 결과(A1~F12 각각 done/부분/skip+사유), pytest 결과, npm build 결과.
