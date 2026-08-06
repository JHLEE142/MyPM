from __future__ import annotations

import json
import logging
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .document_analyzer import AnalysisProvider, build_document_prompt, build_draft_prompt
from .schemas import DocumentAnalysis, DraftChatResult, DraftFields


logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)
_CLI_SEMAPHORE = threading.BoundedSemaphore(2)


class ProviderError(RuntimeError):
    """A provider failed without exposing its raw process output to API callers."""


def _minimal_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}


def _json_candidates(text: str) -> list[str]:
    candidates = [match.group(1).strip() for match in re.finditer(r"```json\s*([\s\S]*?)```", text, re.I)]
    if text.strip():
        candidates.append(text.strip())
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except (json.JSONDecodeError, RecursionError):
            continue
        candidates.append(text[index : index + end])
    return candidates


def parse_structured_json(text: str, schema: type[SchemaT]) -> SchemaT:
    seen: set[str] = set()
    for candidate in _json_candidates(text):
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return schema.model_validate(json.loads(candidate))
        except (json.JSONDecodeError, ValidationError, TypeError, RecursionError):
            continue
    raise ProviderError("provider returned invalid structured output")


class _CliProvider(AnalysisProvider):
    executable_name: str

    def __init__(self, executable: str | None = None, timeout: int = 180):
        self.executable = executable or shutil.which(self.executable_name)
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def _args(self, workdir: Path) -> list[str]:
        raise NotImplementedError

    def _response_text(self, stdout: str) -> str:
        return stdout

    def _execute(self, prompt: str) -> str:
        if not self.executable:
            raise ProviderError(f"{self.name} is unavailable")
        if not _CLI_SEMAPHORE.acquire(blocking=False):
            raise ProviderError(f"{self.name} is busy")
        try:
            with tempfile.TemporaryDirectory(prefix="pacepm-ai-") as directory:
                workdir = Path(directory)
                process = subprocess.Popen(
                    self._args(workdir),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=workdir,
                    env=_minimal_environment(),
                    shell=False,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = process.communicate(prompt, timeout=self.timeout)
                except subprocess.TimeoutExpired as exc:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                    raise ProviderError(f"{self.name} timed out") from exc
        except (OSError, ValueError) as exc:
            raise ProviderError(f"{self.name} could not be started") from exc
        finally:
            _CLI_SEMAPHORE.release()
        if stderr:
            logger.warning("%s stderr_len=%d returncode=%d", self.name, len(stderr), process.returncode)
            logger.debug("%s stderr_prefix=%r", self.name, stderr[:500])
        if process.returncode != 0:
            raise ProviderError(f"{self.name} exited unsuccessfully")
        return self._response_text(stdout)

    def analyze_document(self, source_id: int, blocks: list[dict[str, Any]]) -> DocumentAnalysis:
        return parse_structured_json(self._execute(build_document_prompt(source_id, blocks)), DocumentAnalysis)

    def chat_draft(self, fields: DraftFields, conversation: list[dict[str, str]]) -> DraftChatResult:
        return parse_structured_json(self._execute(build_draft_prompt(fields, conversation)), DraftChatResult)


class ClaudeAgentProvider(_CliProvider):
    name = "claude_agent"
    executable_name = "claude"

    def _args(self, workdir: Path) -> list[str]:
        # Verified against `claude --help` on 2026-08-06: every hardening flag below is supported.
        # Final argv disables tools and MCP, avoids session persistence, and skips user/project customization.
        return [
            str(self.executable),
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--safe-mode",
        ]

    def _response_text(self, stdout: str) -> str:
        try:
            payload = json.loads(stdout)
            result = payload["result"]
        except (json.JSONDecodeError, KeyError, TypeError, RecursionError) as exc:
            raise ProviderError("claude_agent returned invalid output") from exc
        if not isinstance(result, str):
            raise ProviderError("claude_agent returned invalid output")
        return result


class CodexCliProvider(_CliProvider):
    name = "codex_cli"
    executable_name = "codex"

    def _args(self, workdir: Path) -> list[str]:
        # Verified against `codex exec --help` on 2026-08-06: all three hardening flags are supported.
        # Final argv keeps read-only sandboxing and disables config/rules loading and session persistence.
        return [
            str(self.executable),
            "exec",
            "-s",
            "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "-C",
            str(workdir),
            "-m",
            "gpt-5.6-sol",
        ]
