# 일일 보고 초안 자동 생성

Slack `#0-daily-report`에 매일 올리는 보고를 MyPM 데이터에서 자동으로 만들어 준다.
형식은 기존에 쓰던 그대로다.

```
% - 각 기간별 대비 진행률>

전일
(100%)TradeBridge : 2차 피드백 반영
( — )LawGradeOps 충남대 성적 시스템 : 특이사항 없음

금일
( — )TradeBridge : 3차 피드백 수령 및 반영 / 실데이터 기반 워크플로우 정상화

주간 목표
(86%)TradeBridge : 3차 피드백 수령 및 반영

월간 목표
(86%)TradeBridge : 8월 18일 오픈/마감 / 3차 피드백 수령 및 반영
```

## 실행

```bash
apps/api/.venv/bin/python scripts/daily_report.py                  # 오늘 초안 출력
apps/api/.venv/bin/python scripts/daily_report.py --copy           # 클립보드에 복사
apps/api/.venv/bin/python scripts/daily_report.py --date 2026-08-11
```

API 서버가 꺼져 있어도 된다. DB(`apps/api/mypm.db`)를 직접 읽는다.

## 매일 자동 실행

```bash
apps/api/.venv/bin/python scripts/daily_report.py --install-schedule --at 08:40
apps/api/.venv/bin/python scripts/daily_report.py --uninstall-schedule
```

평일(월~금) 지정한 시각에 launchd가 실행해서 초안을 파일로 저장하고 macOS 알림을 띄운다.

- 저장 위치: `~/Library/Application Support/MyPM/daily-report/YYYY-MM-DD.md`
- 실행 로그: 같은 폴더의 `launchd.log`, `launchd.error.log`
- 등록 파일: `~/Library/LaunchAgents/ai.docenty.mypm.daily-report.plist`

**Slack에 자동으로 올리지 않는다.** 초안을 만들어 두기만 하고, 확인 후 직접 붙여넣는 방식이다.
보고는 사람 이름으로 나가는 글이라 검토 없이 전송하지 않는 것을 기본으로 했다.

## 숫자가 어디서 나오는가

| 칸 | 퍼센트 | 내용 |
|---|---|---|
| 전일 | 그날 기록된 진행률 변경의 평균 | 그날 실제로 상태가 바뀐 업무 |
| 금일 | 오늘 배치된 업무의 진행률 | 오늘 배치된 미완료 업무 (없으면 진행 중·다음 순번 업무) |
| 주간 목표 | 프로젝트 전체 진행률 | 이번 주 배치된 미완료 업무 |
| 월간 목표 | 프로젝트 전체 진행률 | 이번 달 목표일 + 이번 달 미완료 업무 |

설계상 지킨 것 두 가지.

- **전일은 일정표가 아니라 실제 기록(TaskEvent)만 본다.** 일정 버전이 오래됐을 때 이미 끝낸 업무가
  "어제 한 일"로 잘못 올라오는 것을 막는다. 기록이 없으면 `( — ) ... 특이사항 없음`으로 두고 사람이 채운다.
- **목표 칸에는 완료된 업무를 넣지 않는다.** 남은 일만 보여야 목표로 읽힌다.

`( — )`는 근거가 없어 비워 둔 칸이다. 숫자를 지어내지 않는다.

## Slack 연동 (선택)

토큰을 설정하면 최근 2주 메시지를 읽어 본인 문체를 참고하고, 그날 이미 보고가 올라왔는지도 알려준다.
토큰이 없어도 초안 생성은 그대로 동작한다.

토큰은 직접 발급해서 환경변수로 넣어야 한다. 스크립트는 값을 저장하거나 출력하지 않는다.

1. https://api.slack.com/apps 에서 앱 생성
2. OAuth & Permissions → Bot Token Scopes에 `channels:history`(비공개 채널이면 `groups:history`)와
   `channels:read`(비공개면 `groups:read`) 추가
3. 워크스페이스에 설치하고 `xoxb-`로 시작하는 토큰 복사
4. Slack에서 `/invite @앱이름`으로 `#0-daily-report` 채널에 초대
5. 셸 프로파일에 추가

```bash
export SLACK_BOT_TOKEN='xoxb-...'
```

launchd로 자동 실행할 때는 셸 프로파일을 읽지 않으므로, plist의 `EnvironmentVariables`에 직접 넣거나
`~/Library/LaunchAgents/ai.docenty.mypm.daily-report.plist`를 수정한 뒤 다시 로드해야 한다.

```bash
apps/api/.venv/bin/python scripts/daily_report.py --slack-history   # 캐시 갱신
```

읽어 온 메시지는 `~/Library/Application Support/MyPM/daily-report/slack-history.json`에 캐시된다.

## 문체 보정

과거 보고 샘플이 있으면 AI가 표현을 그 말투에 맞춰 다듬는다. 이때도 **퍼센트와 업무명은 바꾸지 않는다.**
샘플이 없거나 provider를 쓸 수 없으면 사실 정리본을 그대로 쓴다. `--no-polish`로 끌 수 있다.

## 검증

```bash
apps/api/.venv/bin/python -m pytest apps/api/tests/test_daily_report.py
```
