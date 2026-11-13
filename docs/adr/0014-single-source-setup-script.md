# ADR-0014: One setup-and-run script for both machines; venv chosen by platform, backend detected from the served model

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

The checkout "travels between the Mac and the DGX" (setup_and_run.sh header). Both backends default to the same 127.0.0.1:8000 endpoint, so reachability alone cannot tell them apart.

## Decision

`setup_and_run.sh` is the single source of truth: picks Python 3.12/3.13, uses `.venv` on Darwin and `.venv-dgx` elsewhere, installs `.[dev,pdf]`, detects the backend by the first `/v1/models` id (Qwen/*, *FP8 → dgx, else omlx), brings up vLLM via `~/Desktop/serve-local-llm.sh` when needed, pins `VLLM_MODEL` from the server, runs pytest, kills stale dashboard processes (python executables only) and starts the dashboard. `setup_and_run_dgx.sh` is a one-line shim ("A shim cannot rot").

## Consequences

### Positive

- One command on either machine; no venv clash between platforms.

### Negative

- Model-id heuristics will misclassify a non-Qwen model on vLLM.
- The curl probes send the literal header `Authorization: Bearer ***` (A-014).
- Only `VLLM_MODEL` is pinned; `dgx.model_judge` keeps its default.

## Alternatives considered

- Separate per-platform scripts (rejected: the DGX script is deliberately a shim).

## Evidence

- setup_and_run.sh
- setup_and_run_dgx.sh
- Makefile
- commit cde8a38
