# Codex Stage E — Stage D 감사 반영 (Reconcile)

3개 독립 감사(설계·QA·보안)에서 확인된 결함이다. 전부 수정하고 회귀 테스트를 추가하라. 완료 기준: `apps/api/.venv/bin/python -m pytest` 전건 통과, `cd apps/web && npm run build` 성공. **실제 claude/codex CLI를 호출하는 테스트 금지(전부 monkeypatch).**

## A. 500 오류 (CRITICAL)

A1. `ai/document_analyzer.py:210-215` — draft chat용 start/target 날짜 파싱을 200-208처럼 try/except ValueError로 감싸라. "시작일은 2099-13-45"가 500이 아니라 정상 응답(해당 필드 무시)이어야 한다.
A2. `routes.py` chat 핸들러 — `ProviderError`를 catch해 503 + 한국어 안내("AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해 주세요")로 변환.
A3. `routes.py` confirm — `exc.errors(include_url=False, include_context=False)` 사용 (ctx의 ValueError가 JSON 직렬화 불가 → 현재 500). start>target 초안에서 422가 나오는지 테스트.

## B. Mock 대화 품질 (MAJOR)

B1. 날짜 정규식: `\b`가 한글 앞에서 매치 실패 — "2026-09-01부터 2026-09-30까지"가 인식돼야 한다. `\b`를 한글-안전한 경계(lookahead/lookbehind: 숫자가 아닌 문자 또는 문자열 끝)로 교체. 문서 분석용 패턴과 draft chat용 패턴 모두.
B2. 주말/요일 의도: 부정 표현("안", "않", "쉬", "제외", "빼") 가드 추가 — "주말은 쉬어", "주말도 작업 안 해요" → work_days=[0,1,2,3,4]. 긍정("주말 포함/작업") → 주7일 유지.
B3. 시작일/목표일 날조 금지: 제외일·휴일 문맥("제외", "쉬", "휴", "연휴", "휴가")에 붙은 날짜는 start/target 후보에서 빼라. 무관한 날짜 1~2개만으로 start/target을 채우는 위치 기반 fallback은 "N월 N일부터/까지", "시작", "목표", "마감" 등 명시 문맥이 있을 때만 허용.
B4. 쓰레기 추출 완화: "모르", "없", "나중" 포함 문장에서 name/task 추출 금지. task 제목이 "없습니다"류 부정어 단독이면 스킵.
A/B 회귀 테스트: 자연어 한국어 문구("2026-09-01부터 2026-09-30까지 해", "주말은 쉬어", "제외일은 2099-03-01이야", "이름은 나중에 정할게")로 필드 상태 검증.

## C. 동시성 (MAJOR)

C1. draft 락: `chat_project_draft`, `patch_project_draft`, `confirm_project_draft`에서 `db.get(ProjectDraft, id, with_for_update=True)` (SQLite에서도 무해). 동시 chat 테스트(스레드 4개)로 대화 유실 없음 검증.
C2. CLI 전역 세마포어: cli provider 실행에 `threading.BoundedSemaphore(2)` — 즉시 취득 실패 시(blocking=False) 그 provider를 skip하고 다음으로 폴백. draft당 in-flight 1회 가드(진행 중이면 409 "이미 응답을 생성 중입니다").
C3. 쿼터 TOCTOU: 호출 **직전에** ai_usage에 예약 행(success=None 또는 pending)을 INSERT+commit하고, 쿼터 판정은 예약 포함으로 계산. 종료 시 결과 갱신. 동시 12요청 + 쿼터 2 → 2회만 실행되는 테스트(fake provider).

## D. 라우터 정책 (MAJOR — 리드 결정 사항)

D1. **문서 분석(document_analysis)은 CLI provider 제외**: 기본 순서 `anthropic_api,mock`. env `AI_ANALYSIS_ROUTER_ORDER`로 재정의 가능. draft_chat은 기존 `AI_ROUTER_ORDER`(기본 anthropic_api,claude_agent,codex_cli,mock) 유지. SPEC "키 미설정 시 Mock 폴백"이 문서 분석에서 복원된다.
D2. conftest의 `AI_ROUTER_ORDER=mock` 핀은 유지하되, `shutil.which`를 stub한 별도 테스트로 기본 순서 해석을 검증(키 있음/없음 × 분석/챗 4케이스).
D3. AiRouter를 모듈 레벨 캐시(env 값 바뀌면 재생성)로 만들고, 쿼터 조회+사용량 기록에 세션 재사용.
D4. 쿼터 일자 기준을 UTC가 아닌 로컬 날짜로.

## E. CLI 하드닝 (HIGH — 보안)

E1. 실행 전 `claude --help`, `codex exec --help`를 **한 번씩 실제로 실행해 지원 플래그를 확인**한 뒤(이건 CLI 호출이 아니라 help 출력이므로 허용), 지원되는 것만 추가하라. 목표:
   - claude: 도구 전면 차단(`--tools ""` 또는 `--disallowedTools` 등 실지원 플래그), MCP 차단(`--strict-mcp-config --mcp-config '{"mcpServers":{}}'`), 세션 영속화 비활성(`--no-session-persistence` 지원 시), 사용자 설정 미로드 옵션 지원 시 적용.
   - codex: `--ignore-user-config`, `--ignore-rules`, `--ephemeral` 지원 시 적용 (`-s read-only`는 유지).
   지원 여부와 최종 argv를 코드 주석에 기록. 테스트는 monkeypatch로 argv에 하드닝 플래그 포함을 검증.
E2. stderr 로깅: 원문 대신 `len(stderr)`와 returncode만 기본 로깅(DEBUG에서만 앞 500자).
E3. subprocess에 `start_new_session=True`, 타임아웃 시 `os.killpg`로 프로세스 그룹 종료.
E4. JSON 파서: "마지막 JSON" 대신 (1) 펜스드 ```json 블록 우선 → (2) 전체 stdout 파싱 → (3) 첫 유효 후보 순. except에 RecursionError 추가.
E5. CLI 응답용 스키마: CLI 경로에서 쓰는 DocumentAnalysis 검증에 `extra="forbid"` 추가. (참고: D1로 문서 분석에서 CLI가 빠지므로 draft chat 스키마 중심으로 확인.)

## F. 입력 상한 (MEDIUM)

F1. DraftFields: description/goal/name 각 max_length(4000/4000/255), excluded_dates ≤200개, task_candidates ≤100개, task title max 255.
F2. DraftChatResult.reply max_length 8000.
F3. 프롬프트 조립 직전 총 문자 수 > 100_000이면 422 "대화 내용이 너무 깁니다".
F4. 프로젝트당 active draft 개수와 무관하게 전체 active draft 200개 초과 시 생성 409(오래된 것 정리 안내).

## G. 프론트 (MINOR)

G1. 낙관적 사용자 말풍선: 요청 실패 시 롤백(마지막 user 메시지 제거) + 에러 표시.
G2. next_question을 입력창 placeholder 또는 assistant 말풍선 뒤 힌트로 표시.
G3. 빈 문자열 저장 방지: 미리보기 인라인 편집에서 값이 비면 PATCH를 보내지 않음(에러 "String should have..." 노출 제거).
G4. 라우터 위젯: 문서 분석에서 CLI가 빠졌으므로 위젯 라벨은 그대로 두되 툴팁에 "대화형 등록에 사용"을 명시.

## H. 테스트 보강

H1. PATCH 범위 초과(가용시간 100, buffer 5, 1900-01-01) → 422.
H2. 100턴 상한 → 101번째 409 (기존 동작 실측 확인됨 — pytest로 고정).
H3. A/B/C/D/E 각 회귀 테스트 (위 명시).
H4. completeness: 빈 배열([])은 채워진 것으로 계산하지 않기(_is_filled 수정) + 테스트.

## 제약
- 수정 범위는 위 목록 한정. venv 패키지 추가 금지. 실제 CLI 유료 호출 금지(help 확인만 허용).
- 최종 보고: 항목별 done/부분/skip(사유), 하드닝 플래그 실지원 여부 표, pytest·build 결과.
