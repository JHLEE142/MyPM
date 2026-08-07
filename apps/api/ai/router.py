from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .cli_providers import ClaudeAgentProvider, CodexCliProvider, ProviderError
from .document_analyzer import AnalysisProvider, AnthropicProvider, MockProvider
from .gemini_provider import GeminiApiProvider
from .openai_provider import OpenAiApiProvider
from .schemas import (
    DocumentAnalysis,
    DraftChatResult,
    DraftFields,
    HierarchicalTaskSet,
    ProjectAnalysis,
    TaskUpdatePlan,
)


logger = logging.getLogger(__name__)
DEFAULT_CHAT_ORDER = "anthropic_api,openai_api,gemini_api,claude_agent,codex_cli,mock"
DEFAULT_ANALYSIS_ORDER = "anthropic_api,openai_api,gemini_api,mock"
KNOWN_PROVIDERS = ("anthropic_api", "openai_api", "gemini_api", "claude_agent", "codex_cli", "mock")
_QUOTA_EXHAUSTED = object()
_QUOTA_RESERVATION_LOCK = threading.Lock()
_ROUTER_CACHE_LOCK = threading.Lock()
_ROUTER_CACHE: tuple[tuple[str | None, ...], AiRouter] | None = None


class AiRouter(AnalysisProvider):
    name = "router"

    def __init__(
        self,
        providers: dict[str, AnalysisProvider] | None = None,
        *,
        order: list[str] | None = None,
        analysis_order: list[str] | None = None,
        chat_order: list[str] | None = None,
        quotas: dict[str, int] | None = None,
        usage_recorder: Callable[[str, str, bool, int, int, int], None] | None = None,
        usage_counter: Callable[[str], int] | None = None,
    ):
        self._providers = dict(providers) if providers is not None else self._default_providers()
        self._providers.setdefault("mock", MockProvider())
        configured_chat = _parse_order(os.getenv("AI_ROUTER_ORDER", DEFAULT_CHAT_ORDER))
        configured_analysis = _parse_order(os.getenv("AI_ANALYSIS_ROUTER_ORDER", DEFAULT_ANALYSIS_ORDER))
        self.chat_order = list(order or chat_order or configured_chat)
        self.analysis_order = list(order or analysis_order or configured_analysis)
        # Backwards-compatible status/test attribute; draft chat is the general router order.
        self.order = self.chat_order
        self.quotas = quotas if quotas is not None else parse_quotas(os.getenv("AI_ROUTER_QUOTAS", ""))
        self._usage_recorder = usage_recorder
        self._usage_counter = usage_counter
        self._custom_usage_hooks = usage_recorder is not None or usage_counter is not None
        self._state = threading.local()
        self.last_provider_name: str | None = None

    @property
    def last_provider_name(self) -> str | None:
        return getattr(self._state, "last_provider_name", None)

    @last_provider_name.setter
    def last_provider_name(self, value: str | None) -> None:
        self._state.last_provider_name = value

    @staticmethod
    def _default_providers() -> dict[str, AnalysisProvider]:
        providers: dict[str, AnalysisProvider] = {
            "openai_api": OpenAiApiProvider(),
            "gemini_api": GeminiApiProvider(),
            "claude_agent": ClaudeAgentProvider(),
            "codex_cli": CodexCliProvider(),
            "mock": MockProvider(),
        }
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                providers["anthropic_api"] = AnthropicProvider()
            except Exception:
                logger.exception("Anthropic provider initialization failed")
        return providers

    def analyze_document(self, source_id: int, blocks: list[dict[str, Any]]) -> DocumentAnalysis:
        input_chars = len(json.dumps({"source_id": source_id, "blocks": blocks}, ensure_ascii=False, default=str))
        return self._call(
            "document_analysis",
            input_chars,
            self.analysis_order,
            lambda provider: provider.analyze_document(source_id, blocks),
        )

    def chat_draft(self, fields: DraftFields, conversation: list[dict[str, str]]) -> DraftChatResult:
        input_chars = len(
            json.dumps(
                {"fields": fields.model_dump(mode="json", exclude_none=True), "conversation": conversation},
                ensure_ascii=False,
            )
        )
        return self._call(
            "draft_chat",
            input_chars,
            self.chat_order,
            lambda provider: provider.chat_draft(fields, conversation),
        )

    def generate_hierarchical_tasks(
        self, analysis: ProjectAnalysis, context: dict | None = None
    ) -> HierarchicalTaskSet:
        input_chars = len(analysis.model_dump_json())
        return self._call(
            "task_generation",
            input_chars,
            self.analysis_order,
            lambda provider: provider.generate_hierarchical_tasks(analysis, context),
        )

    def plan_task_updates(
        self, text: str, tasks: list[dict[str, Any]], project: dict[str, Any]
    ) -> TaskUpdatePlan:
        input_chars = len(text) + len(json.dumps(tasks, ensure_ascii=False, default=str))
        return self._call(
            "task_update_plan",
            input_chars,
            self.analysis_order,
            lambda provider: provider.plan_task_updates(text, tasks, project),
        )

    def _call(
        self,
        operation: str,
        input_chars: int,
        order: list[str],
        invoke: Callable[[AnalysisProvider], Any],
    ):
        if self._custom_usage_hooks:
            return self._call_with_custom_hooks(operation, input_chars, order, invoke)

        from app.database import SessionLocal

        attempted_mock = False
        with SessionLocal() as db:
            for provider_name in [*order, "mock"]:
                if provider_name == "mock" and attempted_mock:
                    continue
                provider = self._providers.get(provider_name)
                if provider is None or not provider_available(provider_name, provider):
                    continue
                attempted_mock = attempted_mock or provider_name == "mock"
                reservation = self._reserve_usage(db, provider_name, operation, input_chars)
                if reservation is _QUOTA_EXHAUSTED:
                    continue
                started = time.monotonic()
                try:
                    result = invoke(provider)
                    output_chars = (
                        len(result.model_dump_json()) if hasattr(result, "model_dump_json") else len(str(result))
                    )
                    self._finalize_usage(db, reservation, True, started, output_chars)
                    self.last_provider_name = provider_name
                    return result
                except Exception:
                    self._finalize_usage(db, reservation, False, started, 0)
                    logger.exception("AI provider %s failed for %s", provider_name, operation)
        raise ProviderError("no AI provider could complete the request")

    def _call_with_custom_hooks(
        self,
        operation: str,
        input_chars: int,
        order: list[str],
        invoke: Callable[[AnalysisProvider], Any],
    ):
        attempted_mock = False
        for provider_name in [*order, "mock"]:
            if provider_name == "mock" and attempted_mock:
                continue
            provider = self._providers.get(provider_name)
            if provider is None or not provider_available(provider_name, provider):
                continue
            if self._quota_exhausted_with_hook(provider_name):
                continue
            attempted_mock = attempted_mock or provider_name == "mock"
            started = time.monotonic()
            try:
                result = invoke(provider)
                output_chars = len(result.model_dump_json()) if hasattr(result, "model_dump_json") else len(str(result))
                self._record_with_hook(provider_name, operation, True, started, input_chars, output_chars)
                self.last_provider_name = provider_name
                return result
            except Exception:
                self._record_with_hook(provider_name, operation, False, started, input_chars, 0)
                logger.exception("AI provider %s failed for %s", provider_name, operation)
        raise ProviderError("no AI provider could complete the request")

    def _reserve_usage(self, db: Session, provider: str, operation: str, input_chars: int):
        from app.models import AiUsage

        quota = None if provider == "mock" else self.quotas.get(provider)
        with _QUOTA_RESERVATION_LOCK:
            try:
                if quota is not None and today_provider_calls(provider, db=db) >= quota:
                    return _QUOTA_EXHAUSTED
                usage = AiUsage(
                    provider=provider,
                    operation=operation,
                    success=None,
                    duration_ms=0,
                    input_chars=input_chars,
                    output_chars=0,
                )
                db.add(usage)
                # The pending row is committed before invocation so concurrent quota checks include it.
                db.commit()
                return usage
            except Exception as exc:
                db.rollback()
                raise ProviderError("AI usage reservation failed") from exc

    @staticmethod
    def _finalize_usage(db: Session, usage: Any, success: bool, started: float, output_chars: int) -> None:
        usage.success = success
        usage.duration_ms = max(0, round((time.monotonic() - started) * 1000))
        usage.output_chars = output_chars
        with _QUOTA_RESERVATION_LOCK:
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not finalize AI usage")

    def _quota_exhausted_with_hook(self, provider_name: str) -> bool:
        if provider_name == "mock":
            return False
        quota = self.quotas.get(provider_name)
        counter = self._usage_counter or today_provider_calls
        return quota is not None and counter(provider_name) >= quota

    def _record_with_hook(
        self,
        provider: str,
        operation: str,
        success: bool,
        started: float,
        input_chars: int,
        output_chars: int,
    ) -> None:
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        recorder = self._usage_recorder or record_ai_usage
        try:
            recorder(provider, operation, success, duration_ms, input_chars, output_chars)
        except Exception:
            logger.exception("Could not persist AI usage")


def _parse_order(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_quotas(value: str) -> dict[str, int]:
    quotas: dict[str, int] = {}
    for item in value.split(","):
        if not item.strip() or "=" not in item:
            continue
        name, raw_quota = (part.strip() for part in item.split("=", 1))
        try:
            quota = int(raw_quota)
        except ValueError:
            continue
        if name and quota >= 0:
            quotas[name] = quota
    return quotas


def provider_available(name: str, provider: AnalysisProvider) -> bool:
    if name == "anthropic_api":
        return bool(os.getenv("ANTHROPIC_API_KEY")) or not isinstance(provider, AnthropicProvider)
    available = getattr(provider, "available", True)
    return bool(available)


def _today_start() -> datetime:
    local_now = datetime.now().astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


def record_ai_usage(
    provider: str,
    operation: str,
    success: bool | None,
    duration_ms: int,
    input_chars: int,
    output_chars: int,
    *,
    db: Session | None = None,
) -> Any:
    from app.database import SessionLocal
    from app.models import AiUsage

    usage = AiUsage(
        provider=provider,
        operation=operation,
        success=success,
        duration_ms=duration_ms,
        input_chars=input_chars,
        output_chars=output_chars,
    )
    if db is not None:
        db.add(usage)
        db.commit()
        return usage
    with SessionLocal() as owned_db:
        owned_db.add(usage)
        owned_db.commit()
        return usage


def today_provider_calls(provider: str, *, db: Session | None = None) -> int:
    from app.database import SessionLocal
    from app.models import AiUsage

    statement = select(func.count(AiUsage.id)).where(
        AiUsage.provider == provider,
        AiUsage.created_at >= _today_start(),
    )
    if db is not None:
        return int(db.scalar(statement) or 0)
    with SessionLocal() as owned_db:
        return int(owned_db.scalar(statement) or 0)


def _router_cache_key() -> tuple[str | None, ...]:
    return tuple(
        os.getenv(name)
        for name in (
            "AI_ROUTER_ORDER",
            "AI_ANALYSIS_ROUTER_ORDER",
            "AI_ROUTER_QUOTAS",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "PATH",
        )
    )


def get_ai_router() -> AiRouter:
    global _ROUTER_CACHE

    key = _router_cache_key()
    with _ROUTER_CACHE_LOCK:
        if _ROUTER_CACHE is None or _ROUTER_CACHE[0] != key:
            _ROUTER_CACHE = (key, AiRouter())
        return _ROUTER_CACHE[1]


def reset_router_cache() -> None:
    global _ROUTER_CACHE

    with _ROUTER_CACHE_LOCK:
        _ROUTER_CACHE = None


def router_status() -> list[dict[str, Any]]:
    from app.database import SessionLocal
    from app.models import AiUsage

    router = get_ai_router()
    names = list(dict.fromkeys([*router.chat_order, *router.analysis_order, *KNOWN_PROVIDERS]))
    with SessionLocal() as db:
        result = []
        for name in names:
            provider = router._providers.get(name)
            today_calls = today_provider_calls(name, db=db)
            last_success = db.scalar(
                select(func.max(AiUsage.created_at)).where(AiUsage.provider == name, AiUsage.success.is_(True))
            )
            result.append(
                {
                    "name": name,
                    "available": provider is not None and provider_available(name, provider),
                    "today_calls": today_calls,
                    "quota": router.quotas.get(name),
                    "last_success_at": last_success,
                }
            )
        return result
