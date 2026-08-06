from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ai.cli_providers import ClaudeAgentProvider, CodexCliProvider, ProviderError
from ai.document_analyzer import AnalysisProvider
from ai.drafts import calculate_completeness, merge_draft_fields
from ai.router import AiRouter
from ai.schemas import DocumentAnalysis, DraftChatResult, DraftFields
from app.database import SessionLocal
from app.models import AiUsage, ProjectDraft, ProjectFact, Task


def test_draft_mock_chat_confirm_creates_project_and_manual_tasks(client):
    created = client.post("/api/project-drafts")
    assert created.status_code == 201
    draft = created.json()
    assert draft["completeness_percent"] == 0
    assert draft["conversation"][0]["role"] == "assistant"

    chatted = client.post(
        f"/api/project-drafts/{draft['id']}/chat",
        json={
            "message": (
                "프로젝트명: 고객 포털, 시작일은 2099-01-05, 목표일은 2099-02-01, "
                "하루 4시간, 평일 작업, 목표: 정식 출시, 업무: API 구현 2시간, 테스트 작성 1시간"
            )
        },
    )
    assert chatted.status_code == 200, chatted.text
    fields = chatted.json()["fields"]
    assert fields["name"] == "고객 포털"
    assert fields["start_date"] == "2099-01-05"
    assert fields["target_date"] == "2099-02-01"
    assert fields["daily_capacity_hours"] == 4
    assert chatted.json()["completeness_percent"] > draft["completeness_percent"]

    confirmed = client.post(f"/api/project-drafts/{draft['id']}/confirm")
    assert confirmed.status_code == 201, confirmed.text
    project = confirmed.json()
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task).where(Task.project_id == project["id"]).order_by(Task.id)))
        assert [task.title for task in tasks] == ["API 구현", "테스트 작성"]
        assert all(task.ai_generated is False and task.confidence is None for task in tasks)
        goal = db.scalar(select(ProjectFact).where(ProjectFact.project_id == project["id"]))
        assert goal.content == "정식 출시"
        assert goal.review_status == "approved"
        assert goal.source_block_id is None
        assert db.get(ProjectDraft, draft["id"]).status == "confirmed"
        usage = list(db.scalars(select(AiUsage).where(AiUsage.operation == "draft_chat")))
        assert len(usage) == 1
        assert usage[0].provider == "mock"
        assert usage[0].success is True


def test_confirm_incomplete_draft_returns_missing_fields(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    response = client.post(f"/api/project-drafts/{draft_id}/confirm")
    assert response.status_code == 422
    assert response.json()["detail"]["missing_fields"] == [
        "name",
        "start_date",
        "target_date",
        "daily_capacity_hours",
    ]


def test_chat_on_confirmed_draft_is_conflict(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    patched = client.patch(
        f"/api/project-drafts/{draft_id}",
        json={
            "fields": {
                "name": "확정 테스트",
                "start_date": "2099-01-01",
                "target_date": "2099-01-02",
                "daily_capacity_hours": 1,
            }
        },
    )
    assert patched.status_code == 200
    assert client.post(f"/api/project-drafts/{draft_id}/confirm").status_code == 201
    assert client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": "수정"}).status_code == 409


def test_draft_field_merge_ignores_none_and_updates_only_supplied_fields():
    current = {"name": "기존 이름", "goal": "기존 목표", "daily_capacity_hours": 2}
    merged = merge_draft_fields(current, DraftFields(name=None, goal="새 목표"))
    assert merged == {"name": "기존 이름", "goal": "새 목표", "daily_capacity_hours": 2.0}
    assert calculate_completeness(merged) > 0


def test_message_over_4000_characters_is_rejected(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    response = client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": "가" * 4001})
    assert response.status_code == 422


def test_draft_chat_rejects_untrusted_origin(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    response = client.post(
        f"/api/project-drafts/{draft_id}/chat",
        json={"message": "프로젝트명: 공격"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_router_status_has_no_sensitive_values(client):
    response = client.get("/api/ai/router/status")
    assert response.status_code == 200
    body = response.json()
    assert {item["name"] for item in body["providers"]} >= {"anthropic_api", "claude_agent", "codex_cli", "mock"}
    assert "key" not in response.text.casefold()
    assert "/users/" not in response.text.casefold()


class _FakeProvider(AnalysisProvider):
    def __init__(self, name: str, *, fail: bool = False):
        self.name = name
        self.fail = fail

    def analyze_document(self, source_id, blocks):
        if self.fail:
            raise ProviderError("failure")
        return DocumentAnalysis(document_summary=self.name)

    def chat_draft(self, fields, conversation):
        if self.fail:
            raise ProviderError("failure")
        return DraftChatResult(
            reply=self.name,
            updated_fields=DraftFields(),
            completeness_percent=0,
            next_question=None,
        )


def test_router_falls_back_skips_quota_and_records_every_attempt():
    records = []
    counts = {"quota": 1}
    router = AiRouter(
        providers={
            "failed": _FakeProvider("failed", fail=True),
            "quota": _FakeProvider("quota"),
            "next": _FakeProvider("next"),
        },
        order=["failed", "quota", "next"],
        quotas={"quota": 1},
        usage_counter=lambda name: counts.get(name, 0),
        usage_recorder=lambda *values: records.append(values),
    )
    result = router.analyze_document(1, [])
    assert result.document_summary == "next"
    assert router.last_provider_name == "next"
    assert [item[0] for item in records] == ["failed", "next"]
    assert [item[2] for item in records] == [False, True]


def test_router_uses_mock_when_all_configured_providers_fail():
    router = AiRouter(
        providers={"failed": _FakeProvider("failed", fail=True)},
        order=["failed"],
        usage_counter=lambda _: 0,
        usage_recorder=lambda *args: None,
    )
    result = router.chat_draft(DraftFields(), [{"role": "user", "content": "프로젝트명: 폴백"}])
    assert result.updated_fields.name == "폴백"
    assert router.last_provider_name == "mock"


def _draft_result_json(reply: str = "완료") -> str:
    return json.dumps(
        {
            "reply": reply,
            "updated_fields": {"name": "CLI 프로젝트"},
            "completeness_percent": 15,
            "next_question": None,
        },
        ensure_ascii=False,
    )


def test_codex_cli_uses_stdin_args_temp_cwd_and_parses_json(monkeypatch):
    observed = {}

    class FakeProcess:
        returncode = 0
        pid = 12345

        def communicate(self, prompt=None, timeout=None):
            observed["input"] = prompt
            observed["timeout"] = timeout
            return f"progress\n```json\n{_draft_result_json()}\n```", ""

    def fake_popen(args, **kwargs):
        observed.update({"args": args, **kwargs})
        assert isinstance(args, list)
        assert kwargs["shell"] is False
        assert list(Path(kwargs["cwd"]).iterdir()) == []
        return FakeProcess()

    monkeypatch.setattr("ai.cli_providers.subprocess.Popen", fake_popen)
    provider = CodexCliProvider(executable="/usr/local/bin/codex")
    result = provider.chat_draft(DraftFields(), [{"role": "user", "content": "이름"}])
    assert result.updated_fields.name == "CLI 프로젝트"
    assert observed["args"][:4] == ["/usr/local/bin/codex", "exec", "-s", "read-only"]
    assert {"--ignore-user-config", "--ignore-rules", "--ephemeral"} <= set(observed["args"])
    assert "-C" in observed["args"]
    assert observed["input"]
    assert observed["timeout"] == 180
    assert observed["start_new_session"] is True
    assert not Path(observed["cwd"]).exists()
    assert set(observed["env"]) <= {"PATH", "HOME"}


def test_claude_agent_extracts_result_json(monkeypatch):
    observed = {}

    class FakeProcess:
        returncode = 0
        pid = 12345

        def communicate(self, prompt=None, timeout=None):
            return json.dumps({"result": _draft_result_json("Claude 완료")}, ensure_ascii=False), "diagnostic"

    def fake_popen(args, **kwargs):
        observed["args"] = args
        return FakeProcess()

    monkeypatch.setattr("ai.cli_providers.subprocess.Popen", fake_popen)
    result = ClaudeAgentProvider(executable="/usr/local/bin/claude").chat_draft(
        DraftFields(), [{"role": "user", "content": "이름"}]
    )
    assert result.reply == "Claude 완료"
    assert {"--tools", "--strict-mcp-config", "--no-session-persistence", "--setting-sources", "--safe-mode"} <= set(
        observed["args"]
    )
    assert '{"mcpServers":{}}' in observed["args"]


def test_cli_timeout_becomes_provider_error(monkeypatch):
    killed = []

    class TimeoutProcess:
        returncode = -9
        pid = 54321

        def __init__(self):
            self.calls = 0

        def communicate(self, prompt=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout)
            return "", ""

    monkeypatch.setattr("ai.cli_providers.subprocess.Popen", lambda *args, **kwargs: TimeoutProcess())
    monkeypatch.setattr("ai.cli_providers.os.killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(ProviderError, match="timed out"):
        CodexCliProvider(executable="/usr/local/bin/codex").chat_draft(DraftFields(), [])
    assert killed and killed[0][0] == 54321
