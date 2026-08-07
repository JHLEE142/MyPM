from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .cli_providers import ProviderError
from .document_analyzer import AnalysisProvider, build_document_prompt, build_draft_prompt
from .schemas import DocumentAnalysis, DraftChatResult, DraftFields, HierarchicalTaskSet, ProjectAnalysis
from .structured import complete_structured


logger = logging.getLogger(__name__)
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class GeminiApiProvider(AnalysisProvider):
    """GEMINI_API_KEY 기반 provider. JSON 응답 강제 + 기존 파서/Pydantic 재검증."""

    name = "gemini_api"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _complete(self, prompt: str, max_tokens: int = 4096) -> str:
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY is not configured")
        try:
            response = httpx.post(
                f"{GEMINI_API_BASE}/{self.model}:generateContent",
                # 키는 URL 쿼리 대신 헤더로 전달 (로그·프록시 노출 방지)
                headers={"x-goog-api-key": self.api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": max_tokens,
                    },
                },
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("Gemini API request failed") from exc
        if response.status_code != 200:
            logger.warning("gemini_api status=%d body_len=%d", response.status_code, len(response.text))
            raise ProviderError(f"Gemini API returned status {response.status_code}")
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            content = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ProviderError("Gemini API response shape was unexpected") from exc
        if not content.strip():
            raise ProviderError("Gemini API returned empty content")
        return content

    def _complete_structured(self, prompt: str, schema: type, max_tokens: int = 4096):
        return complete_structured(
            lambda p, tokens: self._complete(p, max_tokens=tokens), prompt, schema, max_tokens
        )

    def analyze_document(self, source_id: int, blocks: list[dict[str, Any]]) -> DocumentAnalysis:
        return self._complete_structured(build_document_prompt(source_id, blocks), DocumentAnalysis, max_tokens=8000)

    def chat_draft(self, fields: DraftFields, conversation: list[dict[str, str]]) -> DraftChatResult:
        return self._complete_structured(build_draft_prompt(fields, conversation), DraftChatResult)

    def generate_hierarchical_tasks(
        self, analysis: ProjectAnalysis, context: dict | None = None
    ) -> HierarchicalTaskSet:
        from .task_generator import build_hierarchical_task_prompt

        return self._complete_structured(
            build_hierarchical_task_prompt(analysis, context), HierarchicalTaskSet, max_tokens=16000
        )
