"""API provider 공용: 구조화 출력 호출 + 검증 실패 시 오류 피드백 재시도."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

from pydantic import ValidationError

from .cli_providers import ProviderError, parse_structured_json


logger = logging.getLogger(__name__)


def validation_detail(content: str, schema: type) -> str:
    """검증 실패 원인을 재시도 프롬프트에 넣을 수 있는 짧은 설명으로 만든다."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return f"JSON 파싱 실패 (line {exc.lineno}, col {exc.colno}): {exc.msg}"
    try:
        schema.model_validate(data)
    except ValidationError as exc:
        return "; ".join(
            f"{'/'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False, include_context=False)[:10]
        )
    return "구조화 출력 검증 실패"


def complete_structured(
    complete: Callable[[str, int], str], prompt: str, schema: type, max_tokens: int = 4096
):
    """`complete(prompt, max_tokens)`를 호출해 schema로 검증. 실패 시 오류를 담아 1회 재시도."""
    last_error: str | None = None
    for attempt in range(2):
        attempt_prompt = prompt
        if last_error is not None:
            attempt_prompt = (
                f"{prompt}\n\n직전 응답이 스키마 검증에 실패했다. 오류: {last_error[:1500]}\n"
                "스키마를 정확히 지켜 완전한 JSON 객체 하나만 다시 반환하라."
            )
        content = complete(attempt_prompt, max_tokens)
        try:
            return parse_structured_json(content, schema)
        except ProviderError:
            last_error = validation_detail(content, schema)
            logger.warning(
                "structured output invalid (schema=%s, attempt=%d, content_len=%d): %s",
                schema.__name__, attempt + 1, len(content), (last_error or "")[:300],
            )
    raise ProviderError("provider returned invalid structured output")
