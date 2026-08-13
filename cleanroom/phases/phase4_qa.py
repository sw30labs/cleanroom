"""Cleanroom — Phase 4 QA

Verify the output with deterministic checks plus a bounded judge-and-revise loop.

Judge verdict is best-effort; deterministic counts are verifiable.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..files import classify, extract_text, detect_marks
from ..llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

QA_PROMPT = """You are the QA JUDGE in a file-hygiene pipeline.

You are given the ORIGINAL content and the CLEANED content after remediation.
Score the cleaned content and decide whether it passes.

Return JSON only:
{{
  "score": 0.0-1.0,
  "residual_marks": "none|low|medium|high",
  "fidelity": "preserved|partially_preserved|compromised",
  "verdict": "PASS|REVISE|FAIL",
  "findings": ["issue1", ...],
  "recommended_revision": "what to fix if REVISE"
}}

Score rubric:
- 1.0 = no residual marks, all facts/numbers/identifiers preserved.
- 0.7-0.9 = minor cosmetic differences, facts intact.
- 0.4-0.6 = some facts altered or notable residual marks → REVISE.
- < 0.4 = seriously degraded or still heavily marked → FAIL.

Be strict about fidelity: a rewrite that changes numbers, names, or claims is
compromised regardless of how clean it looks.
"""


@dataclass
class QAResult:
    status: str  # pass | revise | fail | degraded | error
    verdict: str
    score: float | None
    iterations: int
    deterministic: dict[str, Any]
    findings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _deterministic_qa(orig: str, cleaned: str) -> dict[str, Any]:
    """Verifiable checks that never need an LLM."""
    before = detect_marks(orig)
    after = detect_marks(cleaned)
    return {
        "marks_before": before["total_marked_chars"],
        "marks_after": after["total_marked_chars"],
        "marks_removed": max(0, before["total_marked_chars"] - after["total_marked_chars"]),
        "residual_marked_chars": after["total_marked_chars"],
        "chars_before": len(orig),
        "chars_after": len(cleaned),
        "length_delta_ratio": (
            round((len(cleaned) - len(orig)) / max(len(orig), 1), 4)
        ),
    }


def qa_file(
    original_path: Path,
    cleaned_path: Path,
    *,
    client: LLMClient | None = None,
    max_iterations: int = 3,
    threshold: float = 0.80,
    remediator=None,
) -> QAResult:
    """Run Phase 4 QA on a cleaned output."""
    errors: list[str] = []
    op = classify(original_path)
    cp = classify(cleaned_path)
    if not op.exists or not cp.exists:
        return QAResult(
            status="error", verdict="FAIL", score=None, iterations=0,
            deterministic={}, errors=["original or cleaned path missing"],
        )
    op = extract_text(op)
    cp = extract_text(cp)
    det = _deterministic_qa(op.extracted_text, cp.extracted_text)

    # No LLM → rely on deterministic checks.
    if client is None:
        residual = det["residual_marked_chars"]
        if residual > 0:
            return QAResult(status="revise", verdict="REVISE", score=None, iterations=0,
                            deterministic=det,
                            findings=[f"{residual} marked chars remain"], errors=[])
        return QAResult(status="pass", verdict="PASS", score=1.0, iterations=0,
                        deterministic=det, findings=["deterministic checks clean"], errors=[])

    # Judge-and-revise loop.
    verdict = "REVISE"
    score = 0.0
    findings: list[str] = []
    used = 0
    current_text = cp.extracted_text
    for iteration in range(1, max_iterations + 1):
        used = iteration
        try:
            resp = client.chat_json(
                [
                    {"role": "system", "content": QA_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"ORIGINAL ({len(op.extracted_text)} chars):\n{op.extracted_text[:120_000]}\n\n"
                            f"CLEANED ({len(current_text)} chars):\n{current_text[:120_000]}"
                        ),
                    },
                ],
            )
        except LLMError as exc:
            errors.append(f"qa judge: {exc}")
            break
        if not isinstance(resp, dict):
            errors.append("qa judge returned non-dict")
            break
        try:
            score = float(resp.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        verdict = str(resp.get("verdict") or "REVISE").upper()
        findings = list(resp.get("findings") or [])
        if verdict == "PASS" or score >= threshold:
            break
        # REVISE → try a remediation pass on the current text.
        if remediator is not None and iteration < max_iterations:
            try:
                revised = remediator(current_text)
                if revised and revised.strip():
                    current_text = revised
                    findings.append("revised once by remediator")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"revise pass: {exc}")
                break

    status = "pass" if verdict == "PASS" else ("fail" if verdict == "FAIL" else "revise")
    return QAResult(
        status=status, verdict=verdict, score=round(score, 3),
        iterations=used, deterministic=det, findings=findings, errors=errors,
    )