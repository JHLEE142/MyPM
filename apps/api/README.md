# PacePM API

FastAPI와 SQLAlchemy 2.0으로 구현한 PacePM MVP 백엔드입니다. API 시작 시 테이블을 자동 생성하며, `ANTHROPIC_API_KEY`가 없으면 결정론적 Mock 분석기를 사용합니다.

## 실행

```bash
cd apps/api
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload --env-file .env
```

기본 데이터베이스는 `sqlite:///./pacepm.db`, 기본 파일 저장소는 `./storage`, 업로드 제한은 20MB입니다. PostgreSQL을 사용할 때는 `DATABASE_URL=postgresql+psycopg://...` 형식으로 지정할 수 있습니다(해당 드라이버는 배포 환경에서 제공해야 합니다).

## 테스트

```bash
cd apps/api
.venv/bin/python -m pytest
```

## 주요 흐름

1. `POST /api/projects`로 프로젝트를 생성합니다.
2. `POST /api/projects/{id}/tasks`로 수동 업무를 추가하거나, `/sources`에 multipart 파일 또는 `{"text": "...", "file_name": "brief.md"}` JSON을 전송합니다.
3. `POST /api/projects/{id}/analysis`를 호출하고 `/analysis/status`, `/analysis/review`에서 결과를 확인합니다.
4. `/analysis/approve`에 `tasks`와 `facts` 결정을 일괄 전송합니다.
5. `/schedule/generate`로 승인 업무만 배치합니다. `/schedule/replan`은 보호 업무를 유지한 새 버전을 생성합니다.
6. `/pace`, `/forecast`, `/dashboard`에서 공수 가중 진도와 최근 작업 속도 기반 예측을 조회합니다.

일정 버전은 `/schedule/versions`에서 목록을, `/schedule/versions/{version}`에서 스냅샷을 조회합니다. 두 버전 비교는 `/schedule/versions/compare?from_version=1&to_version=2`를 사용합니다.
