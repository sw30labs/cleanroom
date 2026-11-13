# ADR-0013: Append-only JSONL run ledger and LLM telemetry ledger

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

The dashboard shows run history and an "LLM telemetry/cost ledger" (README).

## Decision

`pipeline._log_run()` appends each run's final record to `cleanroom_runs.jsonl`; `TelemetryLogger` appends one event per LLM attempt (node, provider, model, tokens, latency, status, error class) to `generated/telemetry/llm.jsonl` under a thread lock and never raises. `build_cost_summary()` aggregates per node and the last 30 events; malformed lines are skipped. Both paths are gitignored.

## Consequences

### Positive

- Zero-dependency, crash-tolerant history.

### Negative

- Unbounded growth; the dashboard reads the whole files on every 5-second refresh.
- `spend_usd` sums a `cost_usd` field that no emitter writes, so spend is always 0 for local models.

## Alternatives considered

- SQLite (gitignore lists `*.db`, but no code uses it — not chosen).

## Evidence

- cleanroom/pipeline.py (_log_run)
- cleanroom/telemetry.py
- cleanroom/web/payload.py
- .gitignore
- commit cde8a38
