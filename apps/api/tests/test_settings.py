from __future__ import annotations

import os

from ai.gemini_provider import GeminiApiProvider


def test_ai_settings_lists_three_providers(client, monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    data = client.get("/api/settings/ai").json()["providers"]
    assert [p["provider"] for p in data] == ["anthropic", "openai", "gemini"]
    for entry in data:
        assert entry["configured"] is False
        assert entry["masked_key"] is None
        assert entry["model"]


def test_put_key_masks_and_activates_provider(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.put("/api/settings/ai/gemini", json={"api_key": "AIzaSyTest-Key-1234567890"})
    assert response.status_code == 200
    entry = next(p for p in response.json()["providers"] if p["provider"] == "gemini")
    assert entry["configured"] is True
    assert entry["source"] == "ui"
    assert entry["masked_key"] == "AIza…7890"
    assert "AIzaSyTest-Key-1234567890" not in response.text  # 키 원문은 절대 반환 금지
    assert os.environ["GEMINI_API_KEY"] == "AIzaSyTest-Key-1234567890"

    status = client.get("/api/ai/router/status").json()["providers"]
    gemini = next(p for p in status if p["name"] == "gemini_api")
    assert gemini["available"] is True

    assert client.delete("/api/settings/ai/gemini").status_code == 200
    assert os.environ.get("GEMINI_API_KEY") is None
    entry = next(p for p in client.get("/api/settings/ai").json()["providers"] if p["provider"] == "gemini")
    assert entry["configured"] is False


def test_put_key_rejects_bad_format(client):
    assert client.put("/api/settings/ai/openai", json={"api_key": "  짧음 "}).status_code == 422
    assert client.put("/api/settings/ai/unknown", json={"api_key": "valid-key-123"}).status_code == 404


def test_stored_key_applied_on_startup(client, monkeypatch):
    from app.ai_settings import apply_stored_ai_keys
    from app.database import SessionLocal

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client.put("/api/settings/ai/openai", json={"api_key": "sk-test-abcdef123456"})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # 프로세스 재시작 상황 가정
    with SessionLocal() as db:
        apply_stored_ai_keys(db)
    assert os.environ["OPENAI_API_KEY"] == "sk-test-abcdef123456"
    client.delete("/api/settings/ai/openai")


def test_gemini_provider_parses_generate_content(monkeypatch):
    import httpx

    captured = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": '{"documen'}, {"text": 't_summary": "요약"}'}]}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = GeminiApiProvider(api_key="test-key-123456", model="gemini-2.5-flash")
    analysis = provider.analyze_document(1, [{"id": 1, "content": "테스트"}])
    assert analysis.document_summary == "요약"
    assert "gemini-2.5-flash:generateContent" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "test-key-123456"
    assert "test-key-123456" not in captured["url"]  # 키는 URL이 아닌 헤더로
    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
