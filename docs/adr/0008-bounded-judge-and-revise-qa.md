# ADR-0008: QA uses deterministic checks plus a bounded LLM judge-and-revise loop

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

The README says the judge verdict is best-effort and no tool can certify that a detector will fail; fidelity (facts preserved) must be judged as well as residual marks.

## Decision

`_deterministic_qa()` reports marks before/after and length delta. Without an LLM, QA returns PASS (score 1.0) if no marked characters remain, else REVISE. With an LLM, up to `max_judge_iterations` (default 3) judge calls score the cleaned text against the original (`QA_PROMPT` rubric, strict on fidelity); the loop stops on PASS or score ≥ `judge_score_threshold` (0.80), otherwise the pipeline's remediator rewrites the current text (temperature 0.7) and the judge runs again.

## Consequences

### Positive

- Bounded cost; deterministic floor always reported.
- Fidelity is explicitly part of the rubric.

### Negative

- Revised text from the loop is kept in memory only and never written to the `.cleaned` file, so the verdict may describe text the user does not receive (A-002).
- A score at or above threshold ends the loop, but status follows the verdict string, so a run can end as `revise` with a passing score (A-003).
- The judge call uses the default model, not `model_judge` (A-004).

## Alternatives considered

- Single-shot judge (not chosen).
- Conditional graph edge back to Remediate (not chosen; see ADR-0001).

## Evidence

- cleanroom/phases/phase4_qa.py
- cleanroom/pipeline.py (node_qa, _revise)
- cleanroom.yaml (max_judge_iterations, judge_score_threshold)
- commit cde8a38
