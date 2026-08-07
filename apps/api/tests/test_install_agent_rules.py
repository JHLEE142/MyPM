"""에이전트 규칙 설치기(scripts/install_agent_rules.py) 단위 테스트."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import install_agent_rules as installer  # noqa: E402


NOW = datetime(2026, 8, 7, 15, 0, 0)
SAMPLE_PROJECTS = [
    {
        "id": 5,
        "name": "Dcty-Sprint",
        "start_date": "2026-07-10",
        "target_date": "2026-08-31",
        "description": "Slack sol-xpt-sme_o2o-2510 채널에서 진행",
    },
    {"id": 6, "name": "파이프 | 이름", "start_date": "2026-07-28", "target_date": "2026-10-31", "description": None},
]


def test_render_rules_substitutes_every_placeholder():
    text = installer.render_rules("standard", "http://localhost:8000", SAMPLE_PROJECTS, NOW)
    assert "{{" not in text
    assert "Dcty-Sprint" in text
    assert "sol-xpt-sme_o2o-2510" in text
    assert "2026-08-07 15:00" in text


def test_render_rules_reflects_autonomy_level():
    conservative = installer.render_rules("conservative", "http://localhost:8000", SAMPLE_PROJECTS, NOW)
    autonomous = installer.render_rules("autonomous", "http://localhost:8000", SAMPLE_PROJECTS, NOW)
    # 보수적 수준에서는 진행률 변경조차 확인 목록에 있어야 한다.
    confirm_block = conservative.split("## 반드시 먼저 확인받을 것")[1]
    assert "업무 진행률·상태 변경" in confirm_block
    # 자율 수준에서는 목표일 변경이 자동 목록에 있어야 한다.
    auto_block = autonomous.split("## 확인 없이 바로 해도 되는 것")[1].split("## 반드시")[0]
    assert "목표일" in auto_block


def test_render_rules_rejects_unknown_level():
    with pytest.raises(installer.InstallError):
        installer.render_rules("reckless", "http://localhost:8000", SAMPLE_PROJECTS, NOW)


def test_registry_escapes_pipe_and_notes_empty_api():
    table = installer.render_registry(SAMPLE_PROJECTS)
    assert "파이프 \\| 이름" in table
    empty = installer.render_registry([])
    assert "API에 연결하지 못해" in empty


def test_apply_block_is_idempotent_and_preserves_user_content():
    rules = Path("/tmp/AGENTS.md")
    original = "# 내 메모\n\n- 지켜져야 함\n"
    once = installer.apply_block(original, rules)
    twice = installer.apply_block(once, rules)
    assert once == twice
    assert once.count(installer.BLOCK_BEGIN) == 1
    assert "- 지켜져야 함" in twice


def test_apply_block_replaces_stale_path():
    first = installer.apply_block("", Path("/old/AGENTS.md"))
    second = installer.apply_block(first, Path("/new/AGENTS.md"))
    assert "@/new/AGENTS.md" in second
    assert "/old/AGENTS.md" not in second
    assert second.count(installer.BLOCK_BEGIN) == 1


def test_remove_block_restores_original_content():
    original = "# 내 메모\n\n- 지켜져야 함\n"
    installed = installer.apply_block(original, Path("/tmp/AGENTS.md"))
    assert installer.remove_block(installed).strip() == original.strip()
    # 블록이 없으면 그대로 둔다.
    assert installer.remove_block(original) == original


def test_keep_registry_preserves_hand_edited_table(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(installer, "fetch_projects", lambda *a, **k: SAMPLE_PROJECTS)
    assert installer.main(["--level", "standard", "--yes"]) == 0

    rules = tmp_path / "AGENTS.md"
    edited = rules.read_text(encoding="utf-8").replace(
        "_(비어 있음 — 직접 채우세요)_", "clt-lge-b2b-iot-device-2607"
    )
    rules.write_text(edited, encoding="utf-8")

    assert installer.main(["--level", "autonomous", "--yes", "--keep-registry"]) == 0
    after = rules.read_text(encoding="utf-8")
    assert "clt-lge-b2b-iot-device-2607" in after, "손으로 채운 키워드가 유지되어야 한다"
    assert "자율성 수준: autonomous" in after, "레벨은 새로 반영되어야 한다"


def test_existing_registry_ignores_file_without_table(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("# 규칙\n\n## 대상 프로젝트 레지스트리\n\n표가 없습니다.\n\n## 다음\n", encoding="utf-8")
    assert installer.existing_registry(path) is None
    assert installer.existing_registry(tmp_path / "없는파일.md") is None


def test_fetch_projects_returns_empty_when_api_unreachable():
    assert installer.fetch_projects("http://127.0.0.1:9", timeout=0.2) == []


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(installer, "fetch_projects", lambda *a, **k: SAMPLE_PROJECTS)
    exit_code = installer.main(["--dry-run", "--level", "standard"])
    assert exit_code == 0
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
    assert "실제로 바꾸지 않았습니다" in capsys.readouterr().out


def test_install_then_uninstall_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(installer, "fetch_projects", lambda *a, **k: SAMPLE_PROJECTS)
    memory = tmp_path / ".claude" / "CLAUDE.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# 기존\n\n- 유지\n", encoding="utf-8")

    assert installer.main(["--level", "standard", "--yes"]) == 0
    rules = tmp_path / "AGENTS.md"
    assert rules.exists()
    assert f"@{rules}" in memory.read_text(encoding="utf-8")
    backups = list(memory.parent.glob("CLAUDE.md.bak.*"))
    assert len(backups) == 1, "덮어쓰기 전 백업이 남아야 한다"
    assert "- 유지" in backups[0].read_text(encoding="utf-8")

    assert installer.main(["--uninstall", "--yes"]) == 0
    after = memory.read_text(encoding="utf-8")
    assert installer.BLOCK_BEGIN not in after
    assert "- 유지" in after
    # 규칙 파일은 남겨 둔다(사용자가 편집했을 수 있으므로).
    assert rules.exists()


def test_install_refused_without_consent_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(installer, "fetch_projects", lambda *a, **k: SAMPLE_PROJECTS)
    monkeypatch.setattr(installer, "ask", lambda prompt, *, assume_yes: "아니오")
    assert installer.main(["--level", "standard"]) == 1
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()


def test_install_stops_at_second_confirmation(tmp_path, monkeypatch):
    """1단계 동의는 했지만 2단계 적용 확인에서 거부하면 아무것도 쓰지 않는다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(installer, "fetch_projects", lambda *a, **k: SAMPLE_PROJECTS)
    answers = iter(["동의", "n"])
    monkeypatch.setattr(installer, "ask", lambda prompt, *, assume_yes: next(answers))
    assert installer.main(["--level", "standard"]) == 1
    assert not (tmp_path / "AGENTS.md").exists()


def test_project_scope_targets_local_claude_md(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(installer, "fetch_projects", lambda *a, **k: SAMPLE_PROJECTS)
    assert installer.main(["--level", "standard", "--scope", "project", "--yes"]) == 0
    assert (workdir / "CLAUDE.md").exists()
    assert not (tmp_path / "home" / ".claude" / "CLAUDE.md").exists()
