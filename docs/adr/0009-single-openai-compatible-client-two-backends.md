# ADR-0009: One OpenAI-compatible httpx client for two local backends (OMLX on the Mac, vLLM on the DGX)

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

The checkout runs on a Mac and on a DGX (setup_and_run.sh: "the checkout travels between the Mac and the DGX"). llm.py: OMLX "hangs with streaming=True on large models"; vLLM runs one shared server on 127.0.0.1:8000.

## Decision

`LLMClient` is the single choke point: non-streaming httpx POSTs to `/chat/completions` (or `/responses` when `omlx.use_responses_api`), bearer auth from env, provider-specific tweaks (`chat_template_kwargs.enable_thinking=false` on DGX unless enabled). Tenacity retries transport/LLM errors 3 times with exponential backoff but never `LLMTruncatedError` ("a budget that is too small produces exactly the same truncation every attempt"). `chat_json()` tries `json_object` mode, falls back to plain mode when the reply is degenerate, strips code fences and recovers the first JSON fragment. Each attempt emits a telemetry event.

## Consequences

### Positive

- Same code on both machines; provider is a config switch (aliases mlx/vllm).
- Truncation is surfaced instead of retried.

### Negative

- No streaming, so long generations block up to the 600-second timeout.
- Prompts use doubled braces (`{{ }}`) but are never `.format()`ted, so models see literal `{{` in the schema text (minor).

## Alternatives considered

- Vendor SDKs (openai, langchain) (not chosen; dependencies are httpx + tenacity only).
- Streaming responses (rejected for OMLX per docstring).

## Evidence

- cleanroom/llm.py
- cleanroom/config.py (llm_* accessors)
- cleanroom.yaml
- pyproject.toml (dependencies)
- commit cde8a38
