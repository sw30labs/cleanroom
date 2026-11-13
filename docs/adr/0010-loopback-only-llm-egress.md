# ADR-0010: LLM endpoints must be loopback unless egress is explicitly allowed

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

llm.py: "Cleanroom carries file content in its prompts, so egress is opt-in. A backend reached over an SSH tunnel is loopback and passes."

## Decision

`_require_local_endpoint()` raises `LLMError` at client construction when the base URL host is not 127.0.0.1/localhost/::1, unless `CLEANROOM_ALLOW_REMOTE_LLM` is truthy (then it logs a warning). For loopback endpoints the httpx client disables `trust_env`, so proxy environment variables cannot reroute local traffic.

## Consequences

### Positive

- File contents cannot leave the host by misconfiguration.
- SSH tunnelling is the documented path to a remote DGX.

### Negative

- The reachability check in `_client_from_state` runs before this guard; a remote endpoint fails with an exception caught as a phase error rather than a clean offline fallback.

## Alternatives considered

- Allow any endpoint (rejected by the stated egress rationale).

## Evidence

- cleanroom/llm.py (_is_loopback, _require_local_endpoint, os_env_allow_remote, client)
- commit cde8a38
