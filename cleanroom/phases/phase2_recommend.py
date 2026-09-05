"""Cleanroom — Phase 2 Recommend

Build a ranked remediation plan — lossless deterministic actions first.

Plan is proposed, never applied here; escalate to rewrite only when needed.

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
from typing import Any

from ..files import detect_marks
from ..llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

RECOMMEND_PROMPT = """You are the RECOMMEND agent in a file-hygiene pipeline.

Given the Phase 1 analysis of a file, produce a ranked remediation plan.
Return JSON only with this schema:
{{
  "objective": "one-line statement of what we are cleaning and why",
  "actions": [
    {{
      "id": "A1",
      "op": "strip_unicode|strip_metadata|strip_frontmatter|rewrite|repackage|noop",
      "description": "what to do",
      "strength": "safe|aggressive",
      "risk": "none|low|medium|high",
      "rationale": "why this helps"
    }}
  ],
  "recommended_strength": "safe|aggressive",
  "warnings": ["caveat", ...]
}}

Prefer lossless deterministic actions first; only escalate to LLM rewrite or
repackage when the marks require it. Be honest: if a channel (e.g. C2PA soft
binding, pixel watermarks) cannot be removed by this tool, say so in warnings.
"""


@dataclass
class RecommendResult:
    status: str  # ok | degraded | error
    plan: dict[str, Any]
    errors: list[str] = field(default_factory=list)


def _baseline_plan(analysis: dict[str, Any]) -> dict[str, Any]:
    """Deterministic plan from the analysis alone (no LLM required)."""
    det = analysis.get("deterministic") or {}
    marks = det.get("marks") or detect_marks("")
    actions: list[dict[str, Any]] = []

    if marks.get("total_marked_chars", 0) > 0:
        actions.append(
            {
                "id": "A1",
                "op": "strip_unicode",
                "description": "Strip invisible Unicode and fold visible Latin typography to ASCII",
                "strength": "safe",
                "risk": "none",
                "rationale": f"removes {marks['total_marked_chars']} marked chars",
            }
        )
    else:
        actions.append(
            {
                "id": "A1",
                "op": "noop",
                "description": "No editable Unicode marks found",
                "strength": "safe",
                "risk": "none",
                "rationale": "Lossless text path has nothing to remove here.",
            }
        )

    ext = det.get("extension", "")
    kind = det.get("kind", "")
    if kind in ("text", "pdf", "image", "office", "archive"):
        actions.append(
            {
                "id": "A2",
                "op": "strip_metadata",
                "description": "Strip container metadata (frontmatter / EXIF / C2PA / docProps)",
                "strength": "safe",
                "risk": "low",
                "rationale": f"{det.get('container_note') or ext} may carry provenance metadata.",
            }
        )

    recommended = det.get("recommended_strength") or (
        "aggressive" if marks.get("invisible_chars", 0) > 0 else "safe"
    )
    if recommended == "aggressive":
        actions.append(
            {
                "id": "A3",
                "op": "rewrite",
                "description": "LLM rewrite to reduce statistical text marks",
                "strength": "aggressive",
                "risk": "medium",
                "rationale": "Statistical marks live in word choice; only a rewrite moves them.",
            }
        )

    return {
        "objective": f"Clean provenance marks from {det.get('extension') or 'file'}",
        "actions": actions,
        "recommended_strength": recommended,
        "warnings": [
            "This tool cannot certify that vendor detectors will fail.",
            "C2PA soft binding / pixel watermarks are out of scope for removal.",
        ],
    }


def recommend(analysis: dict[str, Any], client: LLMClient | None = None) -> RecommendResult:
    """Build the remediation plan (baseline + optional LLM refinement)."""
    plan = _baseline_plan(analysis)
    errors: list[str] = []
    if client is None:
        return RecommendResult(status="ok", plan=plan, errors=errors)

    try:
        content = analysis.get("deterministic", {}).get("extracted_text", "") or ""
        summary = analysis.get("semantic", {}).get("summary", "") or ""
        resp = client.chat_json(
            [
                {"role": "system", "content": RECOMMEND_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "ANALYSIS SUMMARY:\n" + json.dumps(analysis.get("semantic", {}), indent=2)[:3000]
                        + "\n\nDETERMINISTIC MARKS:\n"
                        + json.dumps(analysis.get("deterministic", {}).get("marks", {}))
                        + f"\n\nEXCERPT (first 6000 chars):\n{content[:6000]}"
                    ),
                },
            ],
        )
        if isinstance(resp, dict) and resp.get("actions"):
            plan = resp
            # keep the deterministic warnings honesty floor
            plan.setdefault("warnings", [])
            plan["warnings"] = list(dict.fromkeys(plan["warnings"] + _baseline_plan(analysis)["warnings"]))
            plan.setdefault("recommended_strength", "safe")
    except LLMError as exc:
        errors.append(f"recommend semantic: {exc}")
        return RecommendResult(status="degraded", plan=plan, errors=errors)
    return RecommendResult(status="ok", plan=plan, errors=errors)