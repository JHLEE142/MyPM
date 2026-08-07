#!/usr/bin/env python3
"""MyPM 에이전트 규칙 설치기.

AI 코딩 에이전트(Claude Code 등)가 자료를 받았을 때 MyPM의 업무·기한·진행률을
자동으로 갱신하도록 하는 규칙 파일(AGENTS.md)을 생성하고, 에이전트 메모리에 연결한다.

에이전트에게 데이터 변경 권한을 주는 작업이므로 두 단계 동의를 받는다.
  1단계: 무엇을 하게 되는지 고지 → "동의" 입력
  2단계: 실제로 바뀌는 파일 미리보기 → y 입력

사용법:
    python3 scripts/install_agent_rules.py                 # 대화형 설치
    python3 scripts/install_agent_rules.py --dry-run       # 미리보기만
    python3 scripts/install_agent_rules.py --uninstall     # 연결 해제
    python3 scripts/install_agent_rules.py --level standard --scope global --yes

의존성 없이 Python 3.11+ 표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "agent-rules" / "AGENTS.template.md"
DEFAULT_API_BASE = os.getenv("MYPM_API_URL", "http://localhost:8000").rstrip("/")

BLOCK_BEGIN = "<!-- BEGIN MyPM agent rules (managed by scripts/install_agent_rules.py) -->"
BLOCK_END = "<!-- END MyPM agent rules -->"

LEVELS: dict[str, dict[str, object]] = {
    "conservative": {
        "label": "보수적 — 모든 변경 전에 확인",
        "auto": [
            "MyPM 조회 (프로젝트·업무·진행률 읽기)",
            "반영할 변경안을 정리해서 사용자에게 제시",
        ],
        "confirm": [
            "업무 진행률·상태 변경",
            "업무 추가",
            "업무 이름·공수·우선순위·기한 수정",
            "업무 삭제",
            "프로젝트 목표일·시작일·가용시간 변경",
            "업무 전체 삭제, 프로젝트 삭제",
            "일정 재생성",
        ],
        "extra_safety": "- 이 수준에서는 조회 외의 어떤 쓰기 작업도 사용자 확인 없이 하지 않는다.",
    },
    "standard": {
        "label": "표준 — 일상 갱신은 자동, 파괴적 변경은 확인 (권장)",
        "auto": [
            "업무 진행률 변경, 완료 처리, 완료 해제",
            "업무 이름·설명·예상공수·우선순위·기한 수정",
            "자료에 새로 등장한 업무 추가",
            "명백히 취소·폐기된 개별 업무 삭제",
            "업무 추가·삭제 후 일정 재생성 (`POST /schedule/generate`)",
            "프로젝트 설명(description) 갱신",
        ],
        "confirm": [
            "프로젝트 **목표일(target_date)·시작일 변경** — 일정 전체가 재계산되고 위험도 판정이 바뀐다",
            "업무 **전체 삭제** (`DELETE /projects/{id}/tasks`)",
            "**프로젝트 삭제** (`DELETE /projects/{id}`)",
            "하루 가용시간(daily_capacity_hours) 변경",
            "근거가 약한데 진행률을 크게(20%p 이상) 내리는 변경",
            "레지스트리에 없는 새 프로젝트 생성",
        ],
        "extra_safety": "",
    },
    "autonomous": {
        "label": "자율 — 목표일·전체 삭제까지 자동, 프로젝트 삭제만 확인",
        "auto": [
            "업무 진행률 변경, 완료 처리, 완료 해제",
            "업무 이름·설명·예상공수·우선순위·기한 수정",
            "업무 추가 및 개별 삭제",
            "프로젝트 목표일·시작일·가용시간 변경과 그에 따른 재계획(`replan`)",
            "일정 재생성",
            "프로젝트 설명 갱신",
        ],
        "confirm": [
            "**프로젝트 삭제** (`DELETE /projects/{id}`)",
            "업무 **전체 삭제** (`DELETE /projects/{id}/tasks`)",
            "레지스트리에 없는 새 프로젝트 생성",
        ],
        "extra_safety": (
            "- 자율 수준에서는 변경 폭이 크므로, 한 번에 5건이 넘는 변경을 적용할 때는\n"
            "  적용 전 목록을 먼저 출력하고 적용 후 되돌리는 방법(업무 ID와 이전 값)을 보고에 남긴다."
        ),
    },
}

# 설명에서 슬랙 채널·저장소 슬러그처럼 보이는 토큰을 뽑아 식별 키워드 후보로 쓴다.
KEYWORD_PATTERN = re.compile(r"#?\b([a-z][a-z0-9]*(?:[-_][a-z0-9]+){1,5})\b")
KEYWORD_STOPWORDS = {"content-type", "application-json", "e-mail", "http-localhost"}


class InstallError(RuntimeError):
    """설치를 계속할 수 없는 상태."""


# --------------------------------------------------------------------------- 렌더링


def fetch_projects(api_base: str, timeout: float = 3.0) -> list[dict]:
    """MyPM API에서 프로젝트 목록을 읽는다. 실패하면 빈 목록(설치는 계속)."""
    try:
        with urllib.request.urlopen(f"{api_base}/api/projects", timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    return payload if isinstance(payload, list) else []


def extract_keywords(project: dict) -> list[str]:
    """프로젝트명·설명에서 슬랙 채널명 같은 식별 토큰을 추출한다."""
    found: list[str] = []
    for token in KEYWORD_PATTERN.findall(str(project.get("description") or "")):
        if token in KEYWORD_STOPWORDS or token in found:
            continue
        found.append(token)
    return found[:3]


def render_registry(projects: list[dict]) -> str:
    header = "| ID | MyPM 프로젝트명 | 기간 | 식별 키워드 (직접 보완하세요) |\n|---|---|---|---|"
    if not projects:
        return (
            f"{header}\n"
            "| — | _(API에 연결하지 못해 비어 있습니다)_ | — | "
            "MyPM API를 켠 뒤 설치 스크립트를 다시 실행하거나, 이 표를 직접 채우세요 |"
        )
    rows = []
    for project in projects:
        keywords = extract_keywords(project) or ["_(비어 있음 — 직접 채우세요)_"]
        name = str(project.get("name", "")).replace("|", "\\|")
        period = f"{project.get('start_date', '?')} ~ {project.get('target_date', '?')}"
        rows.append(f"| {project.get('id', '?')} | {name} | {period} | {', '.join(f'`{k}`' for k in keywords)} |")
    return "\n".join([header, *rows])


REGISTRY_HEADING = "## 대상 프로젝트 레지스트리"


def existing_registry(path: Path) -> str | None:
    """이미 설치된 규칙 파일에서 손으로 다듬은 레지스트리 표를 꺼낸다.

    사용자가 슬랙 채널명·저장소 경로를 채워 넣은 표를 재설치가 날려버리지 않도록 한다.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if REGISTRY_HEADING not in text:
        return None
    section = text.split(REGISTRY_HEADING, 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.strip().startswith("|")]
    if len(rows) < 3:  # 헤더 + 구분선 + 최소 1행
        return None
    return "\n".join(rows)


def render_rules(
    level: str,
    api_base: str,
    projects: list[dict],
    now: datetime,
    registry_table: str | None = None,
) -> str:
    if level not in LEVELS:
        raise InstallError(f"알 수 없는 자율성 수준입니다: {level}")
    if not TEMPLATE_PATH.exists():
        raise InstallError(f"템플릿을 찾을 수 없습니다: {TEMPLATE_PATH}")
    config = LEVELS[level]
    port = api_base.rsplit(":", 1)[-1] if api_base.rsplit(":", 1)[-1].isdigit() else "8000"
    stamp = now.strftime("%Y-%m-%d %H:%M")
    replacements = {
        "{{GENERATED_AT}}": stamp,
        "{{REGISTRY_AT}}": stamp,
        "{{LEVEL}}": level,
        "{{LEVEL_LABEL}}": str(config["label"]),
        "{{MYPM_ROOT}}": str(REPO_ROOT),
        "{{API_BASE}}": api_base,
        "{{API_PORT}}": port,
        "{{REGISTRY_TABLE}}": registry_table or render_registry(projects),
        "{{AUTO_LIST}}": "\n".join(f"- {item}" for item in config["auto"]),
        "{{CONFIRM_LIST}}": "\n".join(f"- {item}" for item in config["confirm"]),
        "{{EXTRA_SAFETY}}": str(config["extra_safety"]),
    }
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    for token, value in replacements.items():
        text = text.replace(token, value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if leftover:
        raise InstallError(f"템플릿에 치환되지 않은 자리표시자가 남았습니다: {sorted(set(leftover))}")
    return text


# ----------------------------------------------------------------- 메모리 파일 편집


def managed_block(rules_path: Path) -> str:
    return (
        f"{BLOCK_BEGIN}\n"
        "# MyPM 자동 반영\n"
        "\n"
        "자료를 받아 작업을 지시받았고 그 내용이 MyPM에 등록된 프로젝트에 해당하면,\n"
        "아래 규칙에 따라 MyPM의 업무·기한·진행률도 함께 갱신한다.\n"
        "\n"
        f"@{rules_path}\n"
        f"{BLOCK_END}"
    )


def apply_block(current: str, rules_path: Path) -> str:
    """관리 블록을 삽입하거나 기존 블록을 교체한다(멱등)."""
    block = managed_block(rules_path)
    if BLOCK_BEGIN in current and BLOCK_END in current:
        start = current.index(BLOCK_BEGIN)
        end = current.index(BLOCK_END) + len(BLOCK_END)
        return current[:start] + block + current[end:]
    if not current.strip():
        return block + "\n"
    return current.rstrip("\n") + "\n\n" + block + "\n"


def remove_block(current: str) -> str:
    """관리 블록만 제거하고 나머지는 그대로 둔다."""
    if BLOCK_BEGIN not in current or BLOCK_END not in current:
        return current
    start = current.index(BLOCK_BEGIN)
    end = current.index(BLOCK_END) + len(BLOCK_END)
    cleaned = current[:start].rstrip("\n") + "\n" + current[end:].lstrip("\n")
    return cleaned.lstrip("\n")


def diff_text(path: Path, new_text: str) -> str:
    old_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if old_text == new_text:
        return ""
    lines = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"{path} (현재)",
        tofile=f"{path} (변경 후)",
        n=2,
    )
    return "".join(lines)


def backup(path: Path, now: datetime) -> Path | None:
    if not path.exists():
        return None
    target = path.with_suffix(path.suffix + f".bak.{now.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, target)
    return target


def write_file(path: Path, text: str, now: datetime) -> Path | None:
    saved = backup(path, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return saved


# ------------------------------------------------------------------------- 동의 절차


def disclosure(level: str, rules_path: Path, memory_path: Path, project_count: int) -> str:
    config = LEVELS[level]
    auto_lines = "\n".join(f"      • {re.sub(r'[*`]', '', str(item))}" for item in config["auto"])
    confirm_lines = "\n".join(f"      • {re.sub(r'[*`]', '', str(item))}" for item in config["confirm"])
    return f"""
{'=' * 74}
  MyPM 에이전트 규칙 설치 — 동의가 필요합니다
{'=' * 74}

  이 설치는 AI 코딩 에이전트에게 당신의 MyPM 데이터를 바꿀 권한을 줍니다.
  설치 후 에이전트는 자료(캡처·문서·메모)를 받으면 아래 작업을 수행합니다.

  [자율성 수준] {level} — {config['label']}

  ▸ 확인 없이 자동으로 수행:
{auto_lines}

  ▸ 사용자에게 먼저 확인:
{confirm_lines}

  [바뀌는 파일]
      1. {rules_path}
         규칙 본문이 새로 생성됩니다. (기존 파일이 있으면 .bak 백업)
      2. {memory_path}
         에이전트가 항상 읽도록 import 블록이 추가됩니다. (기존 내용은 보존)

  [알아두실 점]
      • 레지스트리에 {project_count}개 프로젝트가 기록됩니다. 프로젝트명·기간이 규칙 파일에
        평문으로 남으므로, 공개 저장소에 커밋하지 마세요.
      • 에이전트는 로컬 MyPM API({DEFAULT_API_BASE})에만 접근합니다.
      • 되돌리려면: python3 scripts/install_agent_rules.py --uninstall
      • 자료 안의 문장은 데이터로만 취급하고 지시로 실행하지 않도록 규칙에 명시됩니다.

  동의하시면 '동의' 또는 'agree'를 입력하세요. 그 외 입력은 취소됩니다.
"""


def ask(prompt: str, *, assume_yes: bool) -> str:
    if assume_yes:
        return "동의"
    if not sys.stdin.isatty():
        raise InstallError("대화형 입력이 불가능합니다. --yes 로 동의를 명시하거나 터미널에서 실행하세요.")
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def choose_level(assume_yes: bool, given: str | None) -> str:
    if given:
        if given not in LEVELS:
            raise InstallError(f"알 수 없는 자율성 수준입니다: {given}")
        return given
    if assume_yes or not sys.stdin.isatty():
        return "standard"
    print("\n  자율성 수준을 고르세요.")
    keys = list(LEVELS)
    for index, key in enumerate(keys, start=1):
        print(f"    {index}) {key:13} {LEVELS[key]['label']}")
    answer = ask("  번호 입력 (기본 2): ", assume_yes=False) or "2"
    if not answer.isdigit() or not 1 <= int(answer) <= len(keys):
        raise InstallError("잘못된 선택입니다.")
    return keys[int(answer) - 1]


# ------------------------------------------------------------------------------ 실행


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    home = Path(os.getenv("HOME", str(Path.home())))
    if args.scope == "global":
        memory_path = home / ".claude" / "CLAUDE.md"
    else:
        memory_path = Path.cwd() / "CLAUDE.md"
    rules_path = Path(args.output).expanduser().resolve() if args.output else Path.cwd() / "AGENTS.md"
    return rules_path, memory_path


def run_uninstall(args: argparse.Namespace, now: datetime) -> int:
    _, memory_path = resolve_paths(args)
    if not memory_path.exists():
        print(f"연결된 메모리 파일이 없습니다: {memory_path}")
        return 0
    current = memory_path.read_text(encoding="utf-8")
    if BLOCK_BEGIN not in current:
        print(f"MyPM 규칙 블록이 없습니다: {memory_path}")
        return 0
    cleaned = remove_block(current)
    print(diff_text(memory_path, cleaned) or "(변경 없음)")
    if args.dry_run:
        print("\n--dry-run 이라 실제로 바꾸지 않았습니다.")
        return 0
    answer = ask("\n위 블록을 제거할까요? [y/N]: ", assume_yes=args.yes)
    if answer.lower() not in {"y", "yes", "동의"}:
        print("취소했습니다.")
        return 1
    saved = write_file(memory_path, cleaned, now)
    print(f"제거했습니다: {memory_path}" + (f" (백업: {saved})" if saved else ""))
    print("규칙 파일(AGENTS.md)은 지우지 않았습니다. 필요하면 직접 삭제하세요.")
    return 0


def run_install(args: argparse.Namespace, now: datetime) -> int:
    api_base = args.api_url.rstrip("/")
    level = choose_level(args.yes, args.level)
    rules_path, memory_path = resolve_paths(args)

    projects = fetch_projects(api_base)
    kept = existing_registry(rules_path) if args.keep_registry else None
    if kept:
        print(f"기존 레지스트리 표를 유지합니다 ({rules_path}). 새 프로젝트는 반영되지 않습니다.\n")
    elif not projects:
        print(f"경고: {api_base} 에서 프로젝트 목록을 읽지 못했습니다. 레지스트리는 비워 둡니다.")
        print("      MyPM API를 켠 뒤 다시 실행하거나 --keep-registry 로 기존 표를 유지하세요.\n")
    elif args.keep_registry:
        print("유지할 기존 레지스트리 표를 찾지 못해 새로 생성합니다.\n")

    rules_text = render_rules(level, api_base, projects, now, registry_table=kept)
    memory_current = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    memory_text = apply_block(memory_current, rules_path)

    print(disclosure(level, rules_path, memory_path, len(projects)))
    if args.dry_run:
        print("  [--dry-run] 실제 변경 없이 미리보기만 출력합니다.\n")
    else:
        consent = ask("  > ", assume_yes=args.yes)
        if consent.lower() not in {"동의", "agree", "yes", "y"}:
            print("\n취소했습니다. 아무것도 바꾸지 않았습니다.")
            return 1

    print(f"\n{'-' * 74}\n  변경 미리보기\n{'-' * 74}")
    rules_diff = diff_text(rules_path, rules_text)
    print(f"\n[1] {rules_path}")
    if rules_path.exists():
        print(rules_diff or "  (내용 동일 — 변경 없음)")
    else:
        preview = "\n".join(f"  {line}" for line in rules_text.splitlines()[:12])
        print(f"  새로 생성 ({len(rules_text.splitlines())}줄). 앞부분 미리보기:\n{preview}\n  ...")
    print(f"\n[2] {memory_path}")
    print(diff_text(memory_path, memory_text) or "  (내용 동일 — 변경 없음)")

    if args.dry_run:
        print("\n--dry-run 이라 실제로 바꾸지 않았습니다. 설치하려면 --dry-run 없이 다시 실행하세요.")
        return 0

    answer = ask("\n위 내용으로 적용할까요? [y/N]: ", assume_yes=args.yes)
    if answer.lower() not in {"y", "yes", "동의"}:
        print("취소했습니다. 아무것도 바꾸지 않았습니다.")
        return 1

    rules_backup = write_file(rules_path, rules_text, now)
    memory_backup = write_file(memory_path, memory_text, now)

    print(f"\n{'=' * 74}\n  설치 완료\n{'=' * 74}")
    print(f"  규칙 파일 : {rules_path}" + (f"  (백업: {rules_backup.name})" if rules_backup else ""))
    print(f"  메모리    : {memory_path}" + (f"  (백업: {memory_backup.name})" if memory_backup else ""))
    print(f"  자율성    : {level} — {LEVELS[level]['label']}")
    print(f"  레지스트리: 프로젝트 {len(projects)}개")
    print("\n  다음 단계")
    print("    1. 규칙 파일의 '식별 키워드' 열을 실제 슬랙 채널명·고객사명으로 보완하세요.")
    print("    2. 에이전트 세션을 새로 시작하면 규칙이 로드됩니다 (기존 세션에는 적용되지 않음).")
    print("    3. 되돌리려면: python3 scripts/install_agent_rules.py --uninstall")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MyPM 에이전트 규칙(AGENTS.md)을 생성하고 에이전트 메모리에 연결합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--level", choices=sorted(LEVELS), help="자율성 수준 (기본: 대화형 선택, 비대화형은 standard)")
    parser.add_argument("--scope", choices=("global", "project"), default="global",
                        help="global: ~/.claude/CLAUDE.md 에 연결 (어느 폴더에서든 적용) / project: 현재 폴더의 CLAUDE.md")
    parser.add_argument("--output", help="규칙 파일 경로 (기본: ./AGENTS.md)")
    parser.add_argument("--api-url", default=DEFAULT_API_BASE, help=f"MyPM API 주소 (기본: {DEFAULT_API_BASE})")
    parser.add_argument("--keep-registry", action="store_true",
                        help="기존 규칙 파일의 레지스트리 표를 그대로 유지 (손으로 채운 키워드 보존)")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 미리보기만 출력")
    parser.add_argument("--yes", action="store_true", help="모든 확인에 동의 (자동화용). 동의 절차를 건너뜁니다")
    parser.add_argument("--uninstall", action="store_true", help="메모리 파일에서 MyPM 규칙 블록 제거")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(timezone.utc).astimezone()
    try:
        return run_uninstall(args, now) if args.uninstall else run_install(args, now)
    except InstallError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
