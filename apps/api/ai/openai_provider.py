from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .cli_providers import ProviderError, parse_structured_json
from .document_analyzer import AnalysisProvider, build_document_prompt, build_draft_prompt
from .schemas import DocumentAnalysis, DraftChatResult, DraftFields, HierarchicalTaskSet, ProjectAnalysis


logger = logging.getLogger(__name__)
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class OpenAiApiProvider(AnalysisProvider):
    """OPENAI_API_KEY 기반 provider. 구조화 출력은 json_object 강제 + 기존 파서/Pydantic 재검증."""

    name = "openai_api"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _complete(self, prompt: str, max_tokens: int = 4096) -> str:
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        try:
            response = httpx.post(
                OPENAI_API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": max_tokens,
                },
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("OpenAI API request failed") from exc
        if response.status_code != 200:
            logger.warning("openai_api status=%d body_len=%d", response.status_code, len(response.text))
            raise ProviderError(f"OpenAI API returned status {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError("OpenAI API response shape was unexpected") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("OpenAI API returned empty content")
        return content

    def _complete_structured(self, prompt: str, schema: type, max_tokens: int = 4096):
        from .structured import complete_structured

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


MAX_CAPTION_IMAGE_BYTES = 10 * 1024 * 1024


def describe_image_safely(image_bytes: bytes, mime_type: str) -> str | None:
    """업로드된 이미지의 내용을 한국어로 요약. 키 없음·실패 시 None (업로드는 계속 진행)."""
    import base64

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or len(image_bytes) > MAX_CAPTION_IMAGE_BYTES or mime_type == "image/svg+xml":
        return None
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    try:
        response = httpx.post(
            OPENAI_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "이 이미지는 프로젝트 참고 자료다. 이미지 안의 텍스트·표·일정·수치를 포함해 "
                                    "내용을 한국어 5문장 이내로 요약하라. 이미지에 없는 내용은 추측하지 마라."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                        ],
                    }
                ],
                "max_tokens": 500,
            },
            timeout=60,
        )
        if response.status_code != 200:
            logger.warning("image caption status=%d", response.status_code)
            return None
        content = response.json()["choices"][0]["message"]["content"]
        return content.strip()[:2000] if isinstance(content, str) and content.strip() else None
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        logger.warning("image caption request failed", exc_info=True)
        return None
