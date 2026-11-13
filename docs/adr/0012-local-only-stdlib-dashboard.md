# ADR-0012: Local-only stdlib dashboard with one job at a time

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

README: "The dashboard is local-only (loopback binding + loopback Host check)." pyproject.toml lists no web framework (inferred preference for stdlib).

## Decision

`web/server.py` uses `ThreadingHTTPServer`; `main()` refuses non-loopback `--host`. Every request must come from a loopback peer with a loopback `Host` header (a DNS-rebinding defence), else 403. Routes are an explicit whitelist (index, favicon, persona-avatar.js, `/api/payload|settings|jobs|jobs/<id>|cost`, POST `/api/run`). Uploads arrive base64 in JSON (~100 MiB cap), are written to `generated/inbox/<rand>_<basename>`, and run in a daemon thread; a non-blocking lock allows one job at a time; up to 25 jobs are kept in memory. Responses send `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `no-store`.

## Consequences

### Positive

- No framework dependency; small attack surface.
- Path traversal in filenames is neutralised by `Path(filename).name`.

### Negative

- `_read_json_body` caps bodies at 256,000 bytes, while `/api/run` expects up to 110,000,000 bytes of base64, so uploads above ~190 KB are rejected as "invalid body length" (A-012).
- Uploaded inputs accumulate in `generated/inbox` with no cleanup (A-013).
- Job history is lost on restart (run ledger survives).

## Alternatives considered

- FastAPI/Flask (not chosen; inferred).
- Binding 0.0.0.0 (explicitly refused).

## Evidence

- cleanroom/web/server.py
- cleanroom/web/runner.py
- cleanroom/web/payload.py
- cleanroom/web/static/index.html (fetch calls)
- README.md (Dashboard)
- commits cde8a38, f674a64
