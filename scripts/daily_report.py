#!/usr/bin/env python3
"""Slack #0-daily-report 일일 보고 초안 생성기.

MyPM의 실제 업무 데이터(어제 기록된 이벤트, 오늘·이번 주·이번 달 배치, 프로젝트 진행률)에서
`전일 / 금일 / 주간 목표 / 월간 목표` 형식의 초안을 만든다.

Slack 토큰이 설정돼 있으면 최근 2주 메시지를 읽어 본인 문체를 학습에 참고하고,
이미 오늘 보고를 올렸는지도 확인한다. 토큰이 없어도 초안 생성은 그대로 동작한다.

초안은 파일로 저장하고 알림만 띄운다. **Slack에 자동으로 올리지 않는다.**
올리는 것은 사람이 확인한 뒤 직접 하거나 `--post`를 명시해야 한다.

사용법:
    python3 scripts/daily_report.py                 # 오늘 초안 출력
    python3 scripts/daily_report.py --save --notify # 파일 저장 + 알림 (자동 실행용)
    python3 scripts/daily_report.py --slack-history # 최근 2주 메시지 캐시 갱신
    python3 scripts/daily_report.py --install-schedule   # 매일 자동 실행 등록
    python3 scripts/daily_report.py --uninstall-schedule
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))

# 기본 DATABASE_URL이 상대 경로(sqlite:///./mypm.db)라 어느 폴더에서 실행하든 같은 파일을 보도록 고정한다.
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{API_DIR / 'mypm.db'}"

OUTPUT_DIR = Path(
    os.getenv("MYPM_REPORT_DIR", str(Path.home() / "Library" / "Application Support" / "MyPM" / "daily-report"))
)
HISTORY_CACHE = OUTPUT_DIR / "slack-history.json"
SLACK_CHANNEL = os.getenv("SLACK_DAILY_CHANNEL", "0-daily-report")
LAUNCH_LABEL = "ai.docenty.mypm.daily-report"
LAUNCH_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"
HISTORY_DAYS = 14


class ReportError(RuntimeError):
    pass


# ------------------------------------------------------------------------ Slack


def slack_token() -> str | None:
    """토큰은 환경변수에서만 읽는다. 스크립트가 값을 저장하거나 출력하지 않는다."""
    return os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_USER_TOKEN") or None


def _slack_call(method: str, token: str, params: dict[str, str]) -> dict:
    url = f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise ReportError(f"Slack API 호출 실패 ({method}): {exc}") from exc
    if not payload.get("ok"):
        raise ReportError(f"Slack API 오류 ({method}): {payload.get('error', 'unknown')}")
    return payload


def resolve_channel_id(token: str, name: str) -> str:
    cursor = ""
    for _ in range(10):
        params = {"limit": "200", "types": "public_channel,private_channel", "exclude_archived": "true"}
        if cursor:
            params["cursor"] = cursor
        payload = _slack_call("conversations.list", token, params)
        for channel in payload.get("channels", []):
            if channel.get("name") == name.lstrip("#"):
                return channel["id"]
        cursor = payload.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    raise ReportError(f"채널을 찾지 못했습니다: #{name} (봇이 채널에 초대되어 있어야 합니다)")


def fetch_history(token: str, channel: str, days: int = HISTORY_DAYS) -> list[dict]:
    """최근 N일 메시지를 가져온다. 본인 글의 문체를 참고하는 용도."""
    channel_id = resolve_channel_id(token, channel)
    oldest = (datetime.now() - timedelta(days=days)).timestamp()
    payload = _slack_call(
        "conversations.history", token, {"channel": channel_id, "oldest": str(oldest), "limit": "200"}
    )
    messages = [
        {"ts": item.get("ts", ""), "user": item.get("user", ""), "text": item.get("text", "")}
        for item in payload.get("messages", [])
        if item.get("text") and item.get("subtype") is None
    ]
    messages.sort(key=lambda item: item["ts"])
    return messages


def load_history_cache() -> list[dict]:
    if not HISTORY_CACHE.exists():
        return []
    try:
        return json.loads(HISTORY_CACHE.read_text(encoding="utf-8")).get("messages", [])
    except (json.JSONDecodeError, OSError):
        return []


def save_history_cache(messages: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_CACHE.write_text(
        json.dumps({"fetched_at": datetime.now().isoformat(), "messages": messages}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def my_recent_reports(messages: list[dict], limit: int = 6) -> list[str]:
    """보고 형식으로 보이는 메시지만 추린다(전일/금일 같은 머리말이 있는 글)."""
    picked = [item["text"] for item in messages if "전일" in item["text"] and "금일" in item["text"]]
    return picked[-limit:]


# ------------------------------------------------------------------------ 초안


def build_draft(target: date) -> tuple[str, dict]:
    from app.database import SessionLocal
    from app.reports import build_daily_report, render_report_text

    with SessionLocal() as db:
        report = build_daily_report(db, target)
    return render_report_text(report), report


def polish_with_style(text: str, samples: list[str]) -> tuple[str, list[str]]:
    """과거 보고 문체에 맞춰 다듬는다. 실패하면 원문을 그대로 쓴다(사실은 이미 정확하므로)."""
    if not samples:
        return text, []
    try:
        from ai.report_writer import draft_in_user_style

        result = draft_in_user_style(text, samples)
        return result.text, list(result.notes)
    except Exception as exc:  # provider 실패는 치명적이지 않다
        return text, [f"문체 보정 생략: {type(exc).__name__}"]


def notify(title: str, message: str) -> None:
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


# -------------------------------------------------------------------- 자동 실행


def schedule_plist(hour: int, minute: int) -> dict:
    python = str(API_DIR / ".venv" / "bin" / "python")
    return {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": [python, str(Path(__file__).resolve()), "--save", "--notify", "--slack-history"],
        "WorkingDirectory": str(REPO_ROOT),
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": hour, "Minute": minute} for weekday in range(1, 6)
        ],
        "StandardOutPath": str(OUTPUT_DIR / "launchd.log"),
        "StandardErrorPath": str(OUTPUT_DIR / "launchd.error.log"),
        "RunAtLoad": False,
        "EnvironmentVariables": {"MYPM_REPORT_DIR": str(OUTPUT_DIR)},
    }


def install_schedule(hour: int, minute: int, assume_yes: bool) -> int:
    plist = schedule_plist(hour, minute)
    print(f"\n매일(평일) {hour:02d}:{minute:02d}에 아래 작업을 실행하도록 등록합니다.")
    print(f"  실행     : {' '.join(plist['ProgramArguments'])}")
    print(f"  결과 저장: {OUTPUT_DIR}")
    print(f"  등록 파일: {LAUNCH_PLIST}")
    print("\n  초안을 파일로 저장하고 알림만 띄웁니다. Slack에 자동으로 올리지 않습니다.")
    if not assume_yes:
        if not sys.stdin.isatty():
            raise ReportError("대화형 입력이 불가능합니다. --yes 로 동의를 명시하세요.")
        if input("\n등록할까요? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print("취소했습니다.")
            return 1
    LAUNCH_PLIST.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with LAUNCH_PLIST.open("wb") as handle:
        plistlib.dump(plist, handle)
    subprocess.run(["launchctl", "unload", str(LAUNCH_PLIST)], check=False, capture_output=True)
    loaded = subprocess.run(["launchctl", "load", str(LAUNCH_PLIST)], check=False, capture_output=True)
    if loaded.returncode != 0:
        print(f"경고: launchctl load 실패 — {loaded.stderr.decode('utf-8', 'replace').strip()}")
        print("      로그인 후 다시 시도하거나 시스템 설정 > 로그인 항목을 확인하세요.")
        return 2
    print(f"\n등록했습니다. 해제하려면: python3 {Path(__file__).name} --uninstall-schedule")
    return 0


def uninstall_schedule() -> int:
    if not LAUNCH_PLIST.exists():
        print(f"등록된 자동 실행이 없습니다: {LAUNCH_PLIST}")
        return 0
    subprocess.run(["launchctl", "unload", str(LAUNCH_PLIST)], check=False, capture_output=True)
    LAUNCH_PLIST.unlink()
    print(f"해제했습니다: {LAUNCH_PLIST}")
    print(f"저장된 초안은 그대로 있습니다: {OUTPUT_DIR}")
    return 0


# ------------------------------------------------------------------------ main


def run(args: argparse.Namespace) -> int:
    target = date.fromisoformat(args.date) if args.date else date.today()

    messages = load_history_cache()
    if args.slack_history:
        token = slack_token()
        if token:
            try:
                messages = fetch_history(token, args.channel, args.history_days)
                save_history_cache(messages)
                print(f"Slack #{args.channel} 최근 {args.history_days}일 메시지 {len(messages)}건을 읽었습니다.")
            except ReportError as exc:
                print(f"경고: {exc}")
        else:
            print("알림: SLACK_BOT_TOKEN이 없어 Slack 히스토리는 건너뜁니다(초안 생성은 계속합니다).")

    text, report = build_draft(target)
    samples = my_recent_reports(messages)
    if samples and not args.no_polish:
        text, notes = polish_with_style(text, samples)
        for note in notes:
            print(f"참고: {note}")

    already = [
        item for item in messages
        if datetime.fromtimestamp(float(item["ts"] or 0)).date() == target and "전일" in item["text"]
    ]
    header = f"# {target.isoformat()} #{args.channel} 초안"
    if already:
        header += "  ⚠️ 오늘 이미 올라간 보고가 있습니다"
    body = f"{header}\n\n{text}\n"

    if args.save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{target.isoformat()}.md"
        path.write_text(body, encoding="utf-8")
        print(f"저장: {path}")
        if args.notify:
            projects = len(report["sections"]["금일"])
            notify("MyPM 일일 보고 초안", f"{target.isoformat()} · 프로젝트 {projects}개 정리 완료")
    if args.copy and copy_to_clipboard(text):
        print("클립보드에 복사했습니다. Slack에 붙여넣으세요.")
    if not args.save:
        print(body)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Slack 일일 보고 초안을 만듭니다.")
    parser.add_argument("--date", help="대상 날짜 (YYYY-MM-DD, 기본: 오늘)")
    parser.add_argument("--channel", default=SLACK_CHANNEL, help=f"대상 채널 (기본: {SLACK_CHANNEL})")
    parser.add_argument("--slack-history", action="store_true", help="Slack 최근 메시지를 다시 읽어 캐시 갱신")
    parser.add_argument("--history-days", type=int, default=HISTORY_DAYS, help=f"읽을 기간 (기본 {HISTORY_DAYS}일)")
    parser.add_argument("--no-polish", action="store_true", help="문체 보정 없이 사실만 정리")
    parser.add_argument("--save", action="store_true", help="파일로 저장")
    parser.add_argument("--notify", action="store_true", help="저장 후 macOS 알림")
    parser.add_argument("--copy", action="store_true", help="초안을 클립보드에 복사")
    parser.add_argument("--install-schedule", action="store_true", help="평일 매일 자동 실행 등록 (launchd)")
    parser.add_argument("--uninstall-schedule", action="store_true", help="자동 실행 해제")
    parser.add_argument("--at", default="08:40", help="자동 실행 시각 HH:MM (기본 08:40)")
    parser.add_argument("--yes", action="store_true", help="확인 없이 진행")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.uninstall_schedule:
            return uninstall_schedule()
        if args.install_schedule:
            hour, _, minute = args.at.partition(":")
            if not hour.isdigit() or not minute.isdigit():
                raise ReportError("--at 은 HH:MM 형식이어야 합니다")
            return install_schedule(int(hour), int(minute), args.yes)
        return run(args)
    except ReportError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
