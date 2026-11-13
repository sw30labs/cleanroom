# ADR-0001: Four-phase linear LangGraph pipeline: Analyze → Recommend → Remediate → QA

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

Cleanroom processes one input file at a time (README "for any single file"). The initial commit describes a "four-agent LangGraph workflow".

## Decision

`pipeline.build_graph()` wires a `StateGraph(CleanerState)` with nodes analyze → recommend → remediate → qa → END. Each node copies state, records `current_phase`, runs its phase module, appends phase-prefixed errors rather than raising, and closes its own LLM client. `run_cleanroom()` resolves the input, creates the output dir, seeds state (strength, apply, skip_llm, judge limits, a redacted settings dict), invokes the compiled graph (or a sequential loop when `compile_graph=False`), and appends the final record to the run ledger.

## Consequences

### Positive

- Each phase is an isolated module with a dataclass result, testable alone.
- A failing phase degrades the run instead of aborting it; errors are collected for the CLI and dashboard.

### Negative

- Strictly linear: there is no conditional edge (e.g. QA → Remediate); revision happens inside the QA node instead (ADR-0008).
- `compile_graph=False` is documented as "for tests without langgraph", but `pipeline.py` imports langgraph at module top, so it cannot run without it (A-010).
- A new LLM client is built per node.

## Alternatives considered

- Plain sequential functions only (kept as the `compile_graph=False` fallback, not the default).
- A cyclic graph with a QA→Remediate edge (not chosen; inferred, no rationale recorded).

## Evidence

- cleanroom/pipeline.py
- cleanroom/phases/phase1_analyze.py … phase4_qa.py
- README.md ("The workflow", topology)
- commit cde8a38 message
- tests/test_core.py::test_pipeline_runs_offline
