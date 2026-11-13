# ADR-0007: Two strength levels: `safe` (deterministic only, default) and `aggressive` (adds LLM rewrite)

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

Statistical token-sampling watermarks live in word choice and can only be moved by a rewrite, which risks changing facts (README marks it "❌ best-effort").

## Decision

`clean.default_strength` is `safe`. With `aggressive`, Remediate calls the LLM with `REWRITE_PROMPT` (preserve every fact, number, name, identifier; temperature 0.7, first 120,000 characters) only when the plan contains a `rewrite` action and a client exists; otherwise it logs `rewrite_skipped`. Rewrites are marked `verifiable: false, best_effort: true`.

## Consequences

### Positive

- The default never changes wording beyond character folding.
- The plan, not just the flag, must ask for a rewrite.

### Negative

- Input over 120,000 characters is silently truncated before rewriting, and the truncated rewrite replaces the whole output.
- Strength from the dashboard request is not validated against `safe|aggressive` (CLI is).

## Alternatives considered

- Always rewriting (rejected; README frames rewrite as best-effort and opt-in).

## Evidence

- cleanroom.yaml (clean.default_strength)
- cleanroom/phases/phase3_remediate.py
- cleanroom/cli.py (--strength choices)
- cleanroom/web/server.py (_api_run)
- README.md
- commit cde8a38
