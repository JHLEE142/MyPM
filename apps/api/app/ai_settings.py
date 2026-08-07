"""AI provider API 키 설정: DB 저장 + 프로세스 환경 반영.

우선순위: 설정 화면에서 저장한 키(DB) > .env / 프로세스 환경 변수.
키 원문은 조회 API로 절대 반환하지 않는다(마스킹만).
"""
from __future__ import annotations

import os
import re

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from ai.router import reset_router_cache

from .models import AppSetting


# provider 식별자 → (환경변수, 모델 환경변수, 기본 모델, 표시 이름)
AI_PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-opus-5",
        "label": "Claude (Anthropic)",
        "router_name": "anthropic_api",
    },
    "openai": {
        "env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
        "label": "OpenAI",
        "router_name": "openai_api",
    },
    "gemini": {
        "env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "label": "Gemini (Google)",
        "router_name": "gemini_api",
    },
}

_SETTING_PREFIX = "ai_key_"
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,400}$")


def _setting_key(provider: str) -> str:
    return f"{_SETTING_PREFIX}{provider}"


def validate_api_key(value: str) -> str:
    cleaned = value.strip()
    if not _KEY_PATTERN.fullmatch(cleaned):
        raise ValueError("API 키 형식이 올바르지 않습니다 (8~400자, 공백 없는 영숫자·.-_)")
    return cleaned


def mask_key(value: str) -> str:
    if len(value) < 12:
        return "설정됨"
    return f"{value[:4]}…{value[-4:]}"


def apply_stored_ai_keys(db: Session) -> None:
    """앱 시작 시 DB에 저장된 키를 프로세스 환경에 반영한다(.env보다 우선)."""
    for provider, info in AI_PROVIDERS.items():
        row = db.get(AppSetting, _setting_key(provider))
        if row is not None and row.value:
            os.environ[info["env"]] = row.value


def set_ai_key(db: Session, provider: str, api_key: str) -> None:
    info = AI_PROVIDERS[provider]
    cleaned = validate_api_key(api_key)
    row = db.get(AppSetting, _setting_key(provider))
    if row is None:
        db.add(AppSetting(key=_setting_key(provider), value=cleaned))
    else:
        row.value = cleaned
    db.commit()
    os.environ[info["env"]] = cleaned
    reset_router_cache()


def delete_ai_key(db: Session, provider: str) -> None:
    info = AI_PROVIDERS[provider]
    row = db.get(AppSetting, _setting_key(provider))
    if row is not None:
        db.delete(row)
        db.commit()
    os.environ.pop(info["env"], None)
    # UI 키를 지우면 .env에 있던 값으로 복귀시킨다
    load_dotenv(override=False)
    reset_router_cache()


def ai_key_status(db: Session) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for provider, info in AI_PROVIDERS.items():
        row = db.get(AppSetting, _setting_key(provider))
        env_value = os.getenv(info["env"], "")
        if row is not None and row.value:
            source, key_value = "ui", row.value
        elif env_value:
            source, key_value = "env", env_value
        else:
            source, key_value = None, ""
        result.append(
            {
                "provider": provider,
                "label": info["label"],
                "router_name": info["router_name"],
                "model": os.getenv(info["model_env"], info["default_model"]),
                "configured": bool(key_value),
                "masked_key": mask_key(key_value) if key_value else None,
                "source": source,
            }
        )
    return result
