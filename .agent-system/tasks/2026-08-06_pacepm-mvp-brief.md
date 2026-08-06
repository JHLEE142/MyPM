# Work Brief — PacePM MVP

## Original request
"AI PM Scheduler 기획안" 전문 (docs/SPEC.md로 정리). 첫 커밋은 프로젝트 생성 + 수동 업무 CRUD, 첫 배포는 문서 분석 없는 일정 관리 버전. "이상없이 만들어줘."

## Goal
docs/SPEC.md의 MVP를 pace-pm/ 모노레포에 구현. 백엔드(FastAPI) 중심으로 일정 엔진·진도·예측·재계획이 결정론적으로 동작하고, AI 분석은 Anthropic API(키 없으면 Mock) 어댑터로 동작. 프론트(Next.js) 5개 라우트.

## Context
- 신규 그린필드. pace-pm은 자체 git repo (회사 계정 jeonghyeon.lee@docenty.ai).
- LLM: claude-opus-5, anthropic SDK, messages.parse() + Pydantic. ANTHROPIC_API_KEY 없으면 결정론적 MockProvider.
- DB: SQLAlchemy, DATABASE_URL (기본 SQLite, PostgreSQL 호환). docker-compose에 Postgres.

## Constraints
- LLM은 추출만, 일정 계산은 엔진(랜덤 금지, 결정론).
- 승인 전 업무 미확정, 근거(source_block) 필수, 재계획 보호 규칙, 목표일 불가 시 명시.
- 완료 체크 등 런타임 지표 계산에 LLM 호출 금지.
- 프롬프트 인젝션 방어: 문서 내용은 데이터로만 취급.

## Success criteria
docs/SPEC.md 14장 E2E 시나리오 + 필수 품질 기준 8항목. pytest 그린.

## Suggested delegation
- Codex(gpt-5.6-sol, high): 백엔드 전체(Stage A), 프론트+compose+README(Stage B), 감사 후 수정.
- deep-reasoner(Opus): 설계·정확성 감사. qa-reviewer(Sonnet): 테스트 실행·주장 검증. security-expert(Opus): 업로드/인젝션/API 표면 감사.
- fast-worker(Sonnet): 기계적 후속 수정.

## Execution plan
1. Lead: 스캐폴드(venv, create-next-app) + 본 브리프.
2. Codex Stage A: apps/api 전체 + pytest.
3. Codex Stage B: apps/web 5 라우트, packages/shared-types, docker-compose, README.
4. 병렬 감사: deep-reasoner / qa-reviewer / security-expert.
5. Reconcile: 수정 라운드 → 재검증.
6. 단계별 커밋, 최종 보고.

## Verification plan
- pytest (스케줄러 결정론, 진도율 30% 예제, 페이스 상태, 예측 데이터 부족, 재계획 보호, 의존 순서, 초과 경고).
- API 스모크 (uvicorn 기동 → E2E 시나리오 curl).
- Next.js build 성공.
