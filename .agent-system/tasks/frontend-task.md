# Codex Stage B — Frontend (apps/web) + 통합

당신은 PacePM의 구현 매니저 겸 시니어 엔지니어다. `docs/SPEC.md`(특히 4장 화면, 10장 API)와 이미 구현된 `apps/api`의 실제 엔드포인트/응답 스키마를 확인한 뒤 프론트엔드를 구현하라. create-next-app(TypeScript, App Router, Tailwind) 스캐폴드가 `apps/web`에 이미 있다.

## Definition of Done

1. 라우트 5개 (SPEC 4장 그대로):
   - `/projects` 프로젝트 목록: 이름, 전체 진행률, 예정 진도율, 상태 배지(정상/주의/위험/심각), 목표일, 예상 완료일, 다음 마일스톤. 프로젝트 삭제.
   - `/projects/new` 3단계 마법사: ① 기본 정보(이름/설명/시작일/목표일) ② 작업 가능 요일·하루 가용시간·버퍼 비율·제외 날짜 ③ 자료 업로드(선택, 파일 또는 텍스트 붙여넣기 — 건너뛰기 가능). 완료 시 생성 후 today로 이동.
   - `/projects/[id]/today` 오늘 화면: 오늘 예정 업무 체크리스트(체크 시 즉시 complete API), 업무별 예상 시간, 오늘 가용시간 vs 배정 합계, 초과 시 "⚠ N시간 초과 배정" 경고, 진행률 슬라이더/메모 입력.
   - `/projects/[id]/sources` 자료 보관함: 파일 업로드(드래그앤드롭 또는 선택), 텍스트 직접 입력 폼, 파일별 상태(업로드됨/텍스트 추출 중/AI 분석 중/검토 필요/분석 완료/분석 실패), "AI 분석 실행" 버튼(POST analysis, 상태 폴링), 실패 시 안내 메시지(HWP 변환 안내 포함), 소스 삭제.
   - `/projects/[id]/plan` 3개 섹션:
     a) 상단 대시보드 카드: 전체 진행률, 계획 대비 차이, 예상 완료일(데이터 부족 시 "예측 데이터 부족 — 최소 3개 작업일 필요"), 위험도, 이번 주 완료율, 남은 공수.
     b) AI 검토 패널: 좌측 분류 탭(목표/산출물/마일스톤/업무/위험/미확정 질문/충돌), 중앙 추출 항목 리스트, 항목 클릭 시 우측에 원문 근거(source block 내용·문서명·위치)와 신뢰도, 승인/수정(제목·공수·우선순위 편집)/거절 버튼. 충돌 항목은 ⚠ 표시와 선택 UI.
     c) 일정: 일간/주간/월간 탭. 일간=날짜별 업무 리스트, 주간=주 단위 그룹 목록+완료율, 월간=마일스톤·목표 일정. "일정 생성" / "재계획" 버튼(재계획 시 조정안 목록을 받아 사용자가 선택), 일정 버전 목록·비교 표시, 목표일 준수 불가 시 명확한 경고 배너. 간트차트 금지.
2. 수동 업무 관리: plan 또는 today에서 업무 추가/수정/상태 변경 가능 (AI 없이 기본 PM으로 사용 가능해야 함).
3. API 클라이언트: `lib/api.ts` 단일 모듈, `NEXT_PUBLIC_API_URL` env(기본 http://localhost:8000). 로딩/에러 상태 처리. 폴링은 sources 분석 상태에만.
4. `packages/shared-types/`: API 응답·요청 TypeScript 타입 정의(프론트에서 import).
5. 루트 `docker-compose.yml`: postgres + api + web 서비스, api는 DATABASE_URL로 postgres 연결. `apps/api/Dockerfile`, `apps/web/Dockerfile` 작성.
6. 루트 `README.md`: 제품 소개, 로컬 실행 방법(venv/npm/docker compose), env 목록, 폴더 구조.
7. `cd apps/web && npm run build` 성공해야 한다. ESLint 에러 없이.
8. CORS: api 쪽에 localhost:3000 허용이 없다면 추가.

## 제약
- 백엔드 로직 변경은 CORS 추가·명백한 버그 수정 외 금지. 변경 시 보고서에 명시.
- UI 텍스트는 한국어. 과한 디자인 라이브러리 추가 금지(Tailwind만).
- 간트차트 만들지 말 것.

## 산출물 보고
최종 메시지에: 라우트별 구현 요약, npm run build 결과, 백엔드에 가한 변경, 미구현·리스크 명시.
