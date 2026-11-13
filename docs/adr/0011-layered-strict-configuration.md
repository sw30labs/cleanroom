# ADR-0011: Layered configuration (code > env > YAML > defaults), strict YAML, secrets only in env

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

cleanroom.yaml documents the precedence and states "a config file with a silent typo is worse than none"; API keys are "intentionally NOT in this file".

## Decision

`Settings` (pydantic) holds defaults; a model validator overlays `cleanroom.yaml` (or `CLEANROOM_CONFIG`) for fields not set explicitly, then env vars, then resolves paths to absolutes under the project root (checkout root if `pyproject.toml` is beside the package, else CWD — "A wheel must never default runtime writes into site-packages"). The YAML loader rejects unknown sections/keys and wrong types, coerces booleans strictly, and has no key for API keys.

## Consequences

### Positive

- Typos fail loudly; secrets stay out of the repo.
- Same file works on both machines with a one-key provider switch.

### Negative

- Overlays use `object.__setattr__`, bypassing field validation for YAML/env values (e.g. `default_strength` is never checked) (A-011).
- `auto_apply` is configurable and shown in the dashboard but no code path reads it (A-005).
- DGX `max_tokens` default is 4096 in code but 16384 in YAML, so behaviour differs when the YAML is absent.

## Alternatives considered

- pydantic-settings (not used; inferred alternative).

## Evidence

- cleanroom/config.py
- cleanroom.yaml
- README.md (Configuration)
- commit cde8a38
