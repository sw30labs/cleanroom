# ADR-0015: Animated persona avatars as a dependency-free plain-script canvas widget

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

Commit f674a64 gives each phase card a procedural avatar that spins while its phase is active.

## Decision

`web/static/persona-avatar.js` (1,030 lines) is a plain script with no build step or modules, rendering on canvas 2D with a single requestAnimationFrame loop; pure helpers are exposed for Node. `index.html` creates one avatar per stage and bumps `spinSignal` every 900 ms while active. `server.py` gains an explicit route to serve it.

## Consequences

### Positive

- No front-end toolchain; works offline.

### Negative

- `pyproject.toml` package-data lists only `*.html` and `*.svg`, so a non-editable install ships without `persona-avatar.js` and the route returns 404 (A-009).
- The file header says it is a clean-room reimplementation of a technique seen in another repo; licensing was not reviewed (A-015).

## Alternatives considered

- A JS framework/bundler (not chosen).

## Evidence

- cleanroom/web/static/persona-avatar.js (header)
- cleanroom/web/static/index.html
- cleanroom/web/server.py (persona-avatar route)
- pyproject.toml (package-data)
- commit f674a64
