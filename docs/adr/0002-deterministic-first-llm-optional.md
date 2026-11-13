# ADR-0002: Deterministic-first processing; every LLM step is optional and degrades gracefully

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

The README promises that a `safe` run produces a working cleaned file with no model at all, and that semantic, rewrite and judge steps degrade when the endpoint is offline.

## Decision

`_client_from_state()` returns no client when `skip_llm` is set (`--offline`) or when a 2-second TCP connect to the active endpoint fails (`runner.omlx_reachable`), "instead of burning retry backoff on a dead connection". Each phase computes a deterministic result first (mark report, baseline plan, deterministic remediation, deterministic QA) and only layers LLM output on top when a client exists; `LLMError` downgrades the phase status to `degraded`.

## Consequences

### Positive

- The core hygiene path works fully offline and is covered by an offline end-to-end test.
- No retry stalls when no server is running.

### Negative

- TCP reachability does not prove the right model is served; a wrong-model server still gets every call (setup_and_run.sh comment makes the same point).
- Without an LLM, Phase 1 recommends `aggressive` whenever invisible characters exist, which then plans a rewrite that cannot run.

## Alternatives considered

- Requiring an LLM for every run (rejected by the README's offline promise).

## Evidence

- cleanroom/pipeline.py (_client_from_state)
- cleanroom/web/runner.py (omlx_reachable)
- cleanroom/phases/*.py (degraded paths)
- cleanroom/cli.py (--offline)
- README.md
- commit cde8a38
