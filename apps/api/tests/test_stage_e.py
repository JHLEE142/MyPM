from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from ai import cli_providers
from ai.cli_providers import CodexCliProvider, ProviderError, parse_structured_json
from ai.document_analyzer import AnalysisProvider, MockProvider
from ai.drafts import calculate_completeness
from ai.router import AiRouter, _today_start, get_ai_router, provider_available, reset_router_cache
from ai.schemas import DocumentAnalysis, DraftChatResult, DraftFields
from app import database
from app.database import SessionLocal
from app.models import AiUsage, ProjectDraft


class FakeProvider(AnalysisProvider):
    def __init__(self, name: str = "fake"):
        self.name = name
        self.calls = 0
        self._lock = threading.Lock()

    def analyze_document(self, source_id, blocks):
        with self._lock:
            self.calls += 1
        return DocumentAnalysis(document_summary=self.name)

    def chat_draft(self, fields, conversation):
        with self._lock:
            self.calls += 1
        return DraftChatResult(
            reply=self.name,
            updated_fields=DraftFields(),
            completeness_percent=0,
            next_question=None,
        )


def mock_chat(message: str) -> DraftFields:
    result = MockProvider().chat_draft(DraftFields(), [{"role": "user", "content": message}])
    return result.updated_fields


def test_invalid_explicit_start_date_is_ignored_instead_of_500(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    response = client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": "시작일은 2099-13-45"})
    assert response.status_code == 200, response.text
    assert response.json()["fields"].get("start_date") is None


def test_chat_provider_error_is_safe_503(client, monkeypatch):
    draft_id = client.post("/api/project-drafts").json()["id"]

    class FailingRouter:
        def chat_draft(self, fields, conversation):
            raise ProviderError("paid provider detail")

    monkeypatch.setattr("app.api.routes.get_ai_router", lambda: FailingRouter())
    response = client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": "프로젝트명: 오류"})
    assert response.status_code == 503
    assert response.json()["detail"] == "AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해 주세요"
    assert "paid provider detail" not in response.text


def test_confirm_start_after_target_is_serializable_422(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    patched = client.patch(
        f"/api/project-drafts/{draft_id}",
        json={
            "fields": {
                "name": "역전 일정",
                "start_date": "2099-02-01",
                "target_date": "2099-01-01",
                "daily_capacity_hours": 2,
            }
        },
    )
    assert patched.status_code == 200
    response = client.post(f"/api/project-drafts/{draft_id}/confirm")
    assert response.status_code == 422
    assert "target_date must be on or after start_date" in response.text


def test_korean_safe_date_boundaries_and_explicit_range_context():
    fields = mock_chat("2026-09-01부터 2026-09-30까지 해")
    assert fields.start_date.isoformat() == "2026-09-01"
    assert fields.target_date.isoformat() == "2026-09-30"

    analysis = MockProvider().analyze_document(
        1,
        [{"id": 10, "block_type": "paragraph", "block_order": 0, "content": "2026-09-01부터 진행"}],
    )
    assert [item.date.isoformat() for item in analysis.fixed_dates] == ["2026-09-01"]


@pytest.mark.parametrize("message", ["주말은 쉬어", "주말도 작업 안 해요", "주말은 작업하지 않아요"])
def test_weekend_negative_intent_uses_weekdays(message):
    assert mock_chat(message).work_days == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("message", ["주말 포함", "주말도 작업해요"])
def test_weekend_positive_intent_uses_all_days(message):
    assert mock_chat(message).work_days == [0, 1, 2, 3, 4, 5, 6]


def test_exclusion_dates_are_not_fabricated_as_start_or_target():
    fields = mock_chat("제외일은 2099-03-01이야")
    assert fields.excluded_dates == [datetime(2099, 3, 1).date()]
    assert fields.start_date is None
    assert fields.target_date is None

    unrelated = mock_chat("회의는 2099-03-01과 2099-03-02야")
    assert unrelated.start_date is None
    assert unrelated.target_date is None

    vacation = mock_chat("휴가는 2099-03-01부터 2099-03-05까지야")
    assert [value.isoformat() for value in vacation.excluded_dates] == ["2099-03-01", "2099-03-05"]
    assert vacation.start_date is None
    assert vacation.target_date is None


@pytest.mark.parametrize(
    ("message", "field"),
    [
        ("이름은 나중에 정할게", "name"),
        ("프로젝트 이름은 아직 모르겠습니다", "name"),
        ("업무는 없습니다", "task_candidates"),
        ("업무는 나중에 정할게", "task_candidates"),
    ],
)
def test_unknown_or_negative_sentences_do_not_create_garbage(message, field):
    assert getattr(mock_chat(message), field) is None


def test_patch_bounds_and_draft_collection_limits(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    for fields in (
        {"daily_capacity_hours": 100},
        {"buffer_ratio": 5},
        {"start_date": "1900-01-01"},
    ):
        assert client.patch(f"/api/project-drafts/{draft_id}", json={"fields": fields}).status_code == 422

    with pytest.raises(ValidationError):
        DraftFields(excluded_dates=["2099-01-01"] * 201)
    with pytest.raises(ValidationError):
        DraftFields(task_candidates=[{"title": f"업무 {index}", "estimated_hours": 1} for index in range(101)])
    with pytest.raises(ValidationError):
        DraftFields(task_candidates=[{"title": "가" * 256, "estimated_hours": 1}])
    with pytest.raises(ValidationError):
        DraftChatResult(reply="가" * 8001, completeness_percent=0)


def test_prompt_character_limit_returns_422_before_provider(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    with SessionLocal() as db:
        draft = db.get(ProjectDraft, draft_id)
        draft.conversation = [
            {"role": "assistant", "content": "안내"},
            *({"role": "assistant", "content": "가" * 4000} for _ in range(25)),
        ]
        db.commit()
    response = client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": "계속"})
    assert response.status_code == 422
    assert response.json()["detail"] == "대화 내용이 너무 깁니다"


def test_global_active_draft_limit_returns_cleanup_guidance(client):
    with SessionLocal() as db:
        db.add_all(
            ProjectDraft(fields={}, completeness_percent=0, conversation=[], status="active") for _ in range(200)
        )
        db.commit()
    response = client.post("/api/project-drafts")
    assert response.status_code == 409
    assert "오래된 draft를 정리" in response.json()["detail"]


def test_101st_user_turn_is_rejected(client):
    draft_id = client.post("/api/project-drafts").json()["id"]
    with SessionLocal() as db:
        draft = db.get(ProjectDraft, draft_id)
        draft.conversation = [
            {"role": "assistant", "content": "시작"},
            *({"role": "user", "content": str(index)} for index in range(100)),
        ]
        db.commit()
    response = client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": "101번째"})
    assert response.status_code == 409
    assert "100턴" in response.json()["detail"]


def test_empty_arrays_do_not_increase_completeness():
    base = calculate_completeness({"name": "프로젝트"})
    assert calculate_completeness({"name": "프로젝트", "excluded_dates": [], "task_candidates": []}) == base


def test_same_draft_concurrent_chat_rejects_inflight_without_losing_accepted_message(client, monkeypatch):
    draft_id = client.post("/api/project-drafts").json()["id"]
    entered = threading.Event()
    release = threading.Event()

    class BlockingRouter:
        def chat_draft(self, fields, conversation):
            entered.set()
            assert release.wait(5)
            return DraftChatResult(reply="완료", completeness_percent=0)

    monkeypatch.setattr("app.api.routes.get_ai_router", lambda: BlockingRouter())

    def send(value: str):
        return client.post(f"/api/project-drafts/{draft_id}/chat", json={"message": value})

    with ThreadPoolExecutor(max_workers=4) as executor:
        accepted = executor.submit(send, "첫 요청")
        assert entered.wait(5)
        rejected = [executor.submit(send, f"경쟁 {index}") for index in range(3)]
        rejected_responses = [future.result(timeout=5) for future in rejected]
        release.set()
        accepted_response = accepted.result(timeout=5)

    assert accepted_response.status_code == 200
    assert [response.status_code for response in rejected_responses] == [409, 409, 409]
    assert all(response.json()["detail"] == "이미 응답을 생성 중입니다" for response in rejected_responses)
    stored = client.get(f"/api/project-drafts/{draft_id}").json()["conversation"]
    assert [item["content"] for item in stored if item["role"] == "user"] == ["첫 요청"]
    assert stored[-1] == {"role": "assistant", "content": "완료"}


def test_cli_busy_skips_to_fallback_without_starting_subprocess(monkeypatch):
    class BusySemaphore:
        def acquire(self, blocking=False):
            assert blocking is False
            return False

        def release(self):
            raise AssertionError("unacquired semaphore must not be released")

    monkeypatch.setattr(cli_providers, "_CLI_SEMAPHORE", BusySemaphore())
    monkeypatch.setattr(cli_providers.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start CLI"))
    fallback = FakeProvider("mock")
    router = AiRouter(
        providers={"codex_cli": CodexCliProvider(executable="/fake/codex"), "mock": fallback},
        order=["codex_cli"],
    )
    result = router.chat_draft(DraftFields(), [{"role": "user", "content": "안녕"}])
    assert result.reply == "mock"
    assert fallback.calls == 1


def test_quota_reservation_limits_twelve_concurrent_calls_to_two_provider_invocations():
    provider = FakeProvider("limited")
    router = AiRouter(providers={"limited": provider}, order=["limited"], quotas={"limited": 2})

    def invoke(index: int):
        return router.chat_draft(DraftFields(), [{"role": "user", "content": str(index)}])

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(invoke, range(12)))

    assert len(results) == 12
    assert provider.calls == 2
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AiUsage.id)).where(AiUsage.provider == "limited")) == 2
        assert db.scalar(
            select(func.count(AiUsage.id)).where(AiUsage.provider == "limited", AiUsage.success.is_(True))
        ) == 2


@pytest.mark.parametrize(
    ("has_key", "operation", "expected"),
    [
        (False, "analysis", "mock"),
        (False, "chat", "claude_agent"),
        (True, "analysis", "anthropic_api"),
        (True, "chat", "anthropic_api"),
    ],
)
def test_default_router_resolution_for_key_and_operation(monkeypatch, has_key, operation, expected):
    monkeypatch.delenv("AI_ROUTER_ORDER", raising=False)
    monkeypatch.delenv("AI_ANALYSIS_ROUTER_ORDER", raising=False)
    if has_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("ai.cli_providers.shutil.which", lambda name: f"/fake/{name}")
    monkeypatch.setattr("ai.router.AnthropicProvider", lambda: FakeProvider("anthropic_api"))
    router = AiRouter()
    order = router.analysis_order if operation == "analysis" else router.chat_order
    resolved = next(
        name
        for name in order
        if (provider := router._providers.get(name)) is not None and provider_available(name, provider)
    )
    assert resolved == expected
    assert "claude_agent" not in router.analysis_order
    assert "codex_cli" not in router.analysis_order


def test_router_cache_reuses_instance_until_environment_changes(monkeypatch):
    reset_router_cache()
    monkeypatch.setenv("AI_ROUTER_ORDER", "mock")
    first = get_ai_router()
    assert get_ai_router() is first
    monkeypatch.setenv("AI_ROUTER_ORDER", "mock,codex_cli")
    assert get_ai_router() is not first


def test_analysis_router_order_can_be_overridden_independently(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_ORDER", "mock")
    monkeypatch.setenv("AI_ANALYSIS_ROUTER_ORDER", "codex_cli,mock")
    router = AiRouter(providers={"codex_cli": FakeProvider("codex_cli"), "mock": FakeProvider("mock")})
    assert router.chat_order == ["mock"]
    assert router.analysis_order == ["codex_cli", "mock"]


def test_quota_query_and_usage_update_reuse_one_session(monkeypatch):
    calls = 0
    original = database.SessionLocal

    def counted_session():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(database, "SessionLocal", counted_session)
    provider = FakeProvider("one_session")
    router = AiRouter(providers={"one_session": provider}, order=["one_session"], quotas={"one_session": 3})
    router.analyze_document(1, [])
    assert calls == 1


def test_quota_day_starts_at_local_midnight_converted_to_utc():
    start = _today_start()
    assert start.tzinfo == timezone.utc
    assert start.astimezone().date() == datetime.now().astimezone().date()
    assert start.astimezone().hour == 0


def test_structured_json_prefers_json_fence_then_first_valid_candidate():
    fenced = json.dumps({"reply": "fenced", "completeness_percent": 0})
    later = json.dumps({"reply": "later", "completeness_percent": 0})
    result = parse_structured_json(f"noise```json\n{fenced}\n```\n{later}", DraftChatResult)
    assert result.reply == "fenced"

    first = json.dumps({"reply": "first", "completeness_percent": 0})
    second = json.dumps({"reply": "second", "completeness_percent": 0})
    result = parse_structured_json(f"progress {first} trailing {second}", DraftChatResult)
    assert result.reply == "first"


def test_document_analysis_forbids_extra_cli_fields():
    with pytest.raises(ValidationError):
        DocumentAnalysis.model_validate({"document_summary": "ok", "unexpected": True})


def test_stderr_default_log_hides_content(monkeypatch, caplog):
    secret = "sensitive stderr detail"

    class FakeProcess:
        returncode = 0
        pid = 123

        def communicate(self, prompt=None, timeout=None):
            payload = json.dumps({"reply": "ok", "completeness_percent": 0})
            return payload, secret

    monkeypatch.setattr(cli_providers.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    caplog.set_level(logging.WARNING, logger="ai.cli_providers")
    result = CodexCliProvider(executable="/fake/codex").chat_draft(DraftFields(), [])
    assert result.reply == "ok"
    assert secret not in caplog.text
    assert f"stderr_len={len(secret)} returncode=0" in caplog.text
