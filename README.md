# PacePM

PacePM(Project Pace Manager)은 프로젝트 자료를 분석해 목표·산출물·업무 후보를 구조화하고, 사용자가 승인한 업무를 실제 가용시간에 맞춰 일간·주간·월간 일정으로 배치하는 프로젝트 매니저입니다. AI 분석 없이도 수동 업무 관리, 오늘 할 일, 진행률, 페이스와 완료일 예측을 사용할 수 있습니다.

## 주요 화면

- `/projects`: 전체 프로젝트 진행률, 예정 진도율, 위험도와 주요 일정
- `/projects/new`: 기본 정보 → 가용시간 → 선택 자료 업로드의 3단계 생성
- `/projects/{id}/today`: 오늘 업무, 가용시간 초과 경고, 완료 체크와 진행률
- `/projects/{id}/sources`: 파일·텍스트 자료, 분석 실행과 상태 확인
- `/projects/{id}/plan`: 진행 대시보드, AI 결과 검토, 일·주·월 일정, 재계획과 수동 업무

## 로컬 실행

필요 환경은 Python 3.11 이상과 Node.js 20.9 이상입니다.

### 1. API

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
```

기본값은 `sqlite:///./pacepm.db`이며 API는 `http://localhost:8000`에서 실행됩니다. `ANTHROPIC_API_KEY`가 없으면 결정론적 Mock 분석기를 사용합니다.

### 2. Web

별도 터미널에서 실행합니다.

```bash
cd apps/web
cp .env.example .env.local
npm ci
npm run dev
```

웹은 `http://localhost:3000`에서 실행됩니다.

### 3. Docker Compose

루트에서 PostgreSQL, API, Web을 함께 실행할 수 있습니다.

```bash
docker compose up --build
```

종료는 `docker compose down`, 데이터 볼륨까지 제거하려면 `docker compose down -v`를 사용합니다.

## 환경 변수

| 변수 | 서비스 | 기본값 | 설명 |
|---|---|---|---|
| `DATABASE_URL` | API | `sqlite:///./pacepm.db` | SQLAlchemy 연결 문자열. Compose는 PostgreSQL 사용 |
| `ANTHROPIC_API_KEY` | API | 없음 | 미설정 시 Mock Provider 사용 |
| `STORAGE_PATH` | API | `storage` | 업로드 원본 저장 경로 |
| `MAX_UPLOAD_SIZE` | API | `20971520` | 파일당 최대 크기(byte) |
| `CORS_ORIGINS` | API | localhost:3000, 127.0.0.1:3000 | 쉼표로 구분한 허용 Origin |
| `NEXT_PUBLIC_API_URL` | Web | `http://localhost:8000` | 브라우저에서 접근할 API 주소. 빌드 시 번들에 포함 |

## 폴더 구조

```text
pace-pm/
├── apps/
│   ├── api/                 # FastAPI, SQLAlchemy, 문서 파서, AI 분석, 일정 엔진
│   └── web/                 # Next.js App Router, TypeScript, Tailwind CSS
├── packages/
│   └── shared-types/        # API 요청·응답 TypeScript 타입
├── docs/SPEC.md             # 제품·API 사양의 단일 기준
├── docker-compose.yml       # PostgreSQL + API + Web
└── README.md
```

## 검증

```bash
cd apps/api && .venv/bin/python -m pytest
cd apps/web && npm run lint && npm run build
```

지원 파일은 PDF, DOCX, XLSX, TXT, MD, HWPX입니다. 바이너리 HWP는 지원하지 않으므로 HWPX 또는 PDF로 변환해야 합니다.
