"""OpenAI-compatible LLM client — single choke point, two backends.

Backends (Settings.llm_provider):
  - "omlx": OMLX on the Mac (non-streaming required — OMLX hangs with
    streaming=True on large models).
  - "dgx":  vLLM on the NVIDIA DGX (one shared server on 127.0.0.1:8000).

All calls go through httpx for reliability. Non-streaming only.

Telemetry: every chat call emits one event per attempt to the telemetry
logger (tokens from the endpoint's ``usage`` when present, latency, status,
node label). Un-instrumented by default so tests and offline paths stay quiet.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

from .config import Settings

logger = logging.getLogger(__name__)


def _is_loopback(url: str) -> bool:
    host = httpx.URL(url).host
    return host in ("127.0.0.1", "localhost", "::1")


class LLMError(Exception):
    """Raised when the LLM backend is unreachable or returns an error."""


class LLMTruncatedError(LLMError):
    """The model stopped because it hit max_tokens, so the answer is cut off.

    Kept distinct from LLMError because the two want opposite handling: a
    connection blip is worth retrying, a budget that is too small produces
    exactly the same truncation every attempt.
    """


def _retryable(exc: BaseException) -> bool:
    """Retry transport faults, never a truncated completion."""
    if isinstance(exc, LLMTruncatedError):
        return False
    return isinstance(exc, (httpx.HTTPError, LLMError))


def _messages_to_responses(
    messages: list[dict[str, str]],
) -> tuple[str | None, list[dict[str, str]] | str]:
    """Split chat messages into Responses ``instructions`` + ``input``."""
    instructions: list[str] = []
    items: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            if content:
                instructions.append(content)
            continue
        items.append({"role": role, "content": content})
    return (
        "\n\n".join(instructions) if instructions else None,
        items if items else "",
    )


def _responses_output_text(data: dict[str, Any]) -> str:
    """Pull assistant text out of a Responses API body."""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            chunks.append(content)
            continue
        if isinstance(content, list):
            for part in content:
                if isinstance(part, str) and part.strip():
                    chunks.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("output_text") or ""
                    if text:
                        chunks.append(str(text))
        reasoning = item.get("reasoning") or item.get("summary")
        if not content and isinstance(reasoning, str) and reasoning.strip():
            chunks.append(reasoning)
    return "".join(chunks)


def _responses_truncated(data: dict[str, Any]) -> bool:
    if data.get("status") == "incomplete":
        reason = str((data.get("incomplete_details") or {}).get("reason") or "")
        if reason in {"max_output_tokens", "max_tokens", "length"}:
            return True
        if not reason:
            return True
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("status") == "incomplete":
            return True
    return False


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Map chat or Responses usage onto telemetry field names."""
    usage = usage or {}
    return {
        "prompt_tokens": int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        ),
        "completion_tokens": int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        ),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
    }


def _require_local_endpoint(base_url: str, provider: str) -> None:
    """Refuse to send prompts off-box unless the operator says so.

    Cleanroom carries file content in its prompts, so egress is opt-in. A backend
    reached over an SSH tunnel is loopback and passes.
    """
    if _is_loopback(base_url):
        return
    host = httpx.URL(base_url).host
    if os_env_allow_remote():
        logger.warning(
            "%s endpoint %s is NOT loopback: file content leaves this host "
            "(permitted by CLEANROOM_ALLOW_REMOTE_LLM)", provider, host,
        )
        return
    raise LLMError(
        f"{provider} endpoint {base_url!r} is not loopback. Prompts carry file "
        f"content, so sending them off-box is opt-in: tunnel the backend "
        f"(ssh -L 8000:127.0.0.1:8000 <host>) or set CLEANROOM_ALLOW_REMOTE_LLM=1."
    )


def os_env_allow_remote() -> bool:
    import os
    return os.environ.get("CLEANROOM_ALLOW_REMOTE_LLM", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class LLMClient:
    """Thin OpenAI-compatible client backed by OMLX or vLLM.

    Non-streaming only. All public methods retry with exponential backoff on
    transient errors.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        telemetry=None,
        node: str = "llm",
    ) -> None:
        self.settings = settings or Settings()
        self._base_url = self.settings.llm_base_url.rstrip("/")
        _require_local_endpoint(self._base_url, self.settings.llm_provider)
        self._api_key = self.settings.llm_api_key
        self._timeout = self.settings.llm_timeout
        self._client: httpx.Client | None = None
        self._json_mode_broken = False
        self._telemetry = telemetry
        self._node = node

    @property
    def _is_dgx(self) -> bool:
        return getattr(self.settings, "llm_provider", "omlx") == "dgx"

    @property
    def _backend(self) -> str:
        return "DGX" if self._is_dgx else "OMLX"

    @property
    def _provider_label(self) -> str:
        return "dgx" if self._is_dgx else "mlx"

    def _emit(
        self,
        *,
        model: str,
        status: str,
        latency_ms: int,
        usage: dict[str, Any] | None = None,
        error_class: str | None = None,
        node: str | None = None,
    ) -> None:
        if self._telemetry is None:
            return
        u = usage or {}
        try:
            self._telemetry.emit(
                node=node or self._node,
                provider=self._provider_label,
                model_id=model,
                input_tokens=int(u.get("prompt_tokens") or 0),
                output_tokens=int(u.get("completion_tokens") or 0),
                cache_read_tokens=int(u.get("cache_read_tokens") or 0),
                cache_write_tokens=int(u.get("cache_write_tokens") or 0),
                latency_ms=latency_ms,
                status=status,
                error_class=error_class,
            )
        except Exception:
            logger.warning("telemetry emit failed", exc_info=True)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
                trust_env=not _is_loopback(self._base_url),
            )
        return self._client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    # ── Public API ──

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception(_retryable),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        node: str | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant content."""
        model = model or self.settings.llm_model
        max_tokens = max_tokens or self.settings.llm_max_tokens
        use_responses = bool(self.settings.llm_use_responses_api)
        if use_responses:
            instructions, items = _messages_to_responses(messages)
            payload = {
                "model": model,
                "input": items,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "stream": False,
            }
            if instructions:
                payload["instructions"] = instructions
            if response_format and response_format.get("type"):
                payload["text"] = {"format": {"type": response_format["type"]}}
        else:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format:
                payload["response_format"] = response_format
            if self._is_dgx and not self.settings.llm_enable_thinking:
                payload["chat_template_kwargs"] = {"enable_thinking": False}

        start = time.perf_counter()
        data: dict[str, Any] | None = None
        try:
            try:
                path = "/responses" if use_responses else "/chat/completions"
                resp = self.client.post(path, json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise LLMError(f"{self._backend} HTTP {e.response.status_code}: {e.response.text[:200]}") from e
            except httpx.HTTPError as e:
                raise LLMError(f"{self._backend} connection error: {e}") from e

            data = resp.json()
            if use_responses:
                content = _responses_output_text(data)
                if _responses_truncated(data):
                    raise LLMTruncatedError(
                        f"{self._backend} hit the {max_tokens}-token ceiling and the "
                        f"response is truncated ({len(content)} chars)."
                    )
            else:
                choices = data.get("choices", [])
                if not choices:
                    raise LLMError(f"{self._backend} returned no choices: {data}")
                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content") or message.get("reasoning_content") or ""
                if choice.get("finish_reason") == "length":
                    raise LLMTruncatedError(
                        f"{self._backend} hit the {max_tokens}-token ceiling and the "
                        f"response is truncated ({len(content)} chars)."
                    )
            if not content:
                raise LLMError(f"{self._backend} returned empty content")
        except Exception as exc:
            self._emit(
                model=model,
                status="error",
                latency_ms=int((time.perf_counter() - start) * 1000),
                error_class=type(exc).__name__,
                usage=_normalize_usage((data or {}).get("usage", {})),
                node=node,
            )
            raise
        self._emit(
            model=model,
            status="ok",
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage=_normalize_usage(data.get("usage", {})),
            node=node,
        )
        return content

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Any:
        """Like chat() but parses the response as JSON.

        Try json_object mode first, fall back to extracting JSON from text.
        """
        model = model or self.settings.llm_model
        max_tokens = max_tokens or self.settings.llm_max_tokens
        text = None
        if not self._json_mode_broken:
            try:
                candidate = self.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                if self._degenerate_json(candidate):
                    self._json_mode_broken = True
                    logger.info("%s json_object mode returned a degenerate response; using plain mode", self._backend)
                else:
                    text = candidate
            except LLMTruncatedError:
                raise
            except LLMError:
                pass  # plain call below
        if text is None:
            text = self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            decoder = json.JSONDecoder()
            for i, c in enumerate(text):
                if c not in "{[":
                    continue
                try:
                    obj, _ = decoder.raw_decode(text, i)
                except json.JSONDecodeError:
                    continue
                logger.warning(
                    "%s response was not valid JSON; recovered a %s fragment at "
                    "offset %d of %d chars.",
                    self._backend, type(obj).__name__, i, len(text),
                )
                return obj
            raise LLMError(f"Cannot parse JSON from LLM response: {text[:200]}") from e

    @staticmethod
    def _degenerate_json(text: str) -> bool:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = "\n".join(
                l for l in stripped.split("\n") if not l.startswith("```")
            ).strip()
        return stripped in {"", "{}"} or stripped.startswith('{"messages":') or stripped.startswith('{"input":')

    def list_models(self) -> list[str]:
        resp = self.client.get("/models")
        resp.raise_for_status()
        return [m.get("id") for m in resp.json().get("data", [])]

    def responses_supported(self) -> tuple[bool, str]:
        try:
            resp = self.client.get("/models")
            resp.raise_for_status()
            return True, "endpoint reachable"
        except httpx.HTTPError as exc:
            return False, str(exc)