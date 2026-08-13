"""Cleanroom — Phase 1 Analyze

Classify the file, extract content, and detect provenance marks.

Deterministic detection always runs; semantic read is LLM-gated.

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

ANALYZE_PROMPT = """You are the ANALYZE agent in a file-hygiene pipeline.

Given the profile and extracted content of a single input file, produce a
structured analysis. Return JSON only with this schema:
{{
  "summary": "2-3 sentence plain-language summary of what the file is",
  "content_type": "e.g. report / code / data / document / image / archive",
  "sensitivity": "low|medium|high",
  "provenance_marks": [
    {{
      "channel": "unicode|metadata|c2pa|statistical|none",
      "confidence": 0.0-1.0,
      "detail": "what mark and where"
    }}
  ],
  "recommended_strength": "safe|aggressive",
  "notes": ["observation1", "observation2"]
}}

Preserve the facts in the content. Do not invent marks that are not present.
If the LLM is unavailable, the caller falls back to deterministic analysis.
"""


@dataclass
class AnalyzeResult:
    status: str  # ok | degraded | skipped
    profile: dict[str, Any]
    deterministic: dict[str, Any]
    semantic: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def analyze_file(path: Path, client: LLMClient | None = None) -> AnalyzeResult:
    """Run Phase 1 on a single file."""
    errors: list[str] = []
    profile = classify(path)
    if not profile.exists:
        return AnalyzeResult(
            status="error",
            profile=profile.__dict__,
            deterministic={},
            errors=[f"input file not found: {path}"],
        )
    profile = extract_text(profile)

    deterministic: dict[str, Any] = {
        "kind": profile.kind,
        "extension": profile.extension,
        "size_bytes": profile.size_bytes,
        "detected_charset": profile.detected_charset,
        "is_text_like": profile.is_text_like,
        "container_note": profile.container_note,
        "extracted_chars": len(profile.extracted_text),
        "extraction_error": profile.extraction_error,
        "marks": profile.mark_report,
        "metadata_hints": profile.metadata_hints,
    }

    semantic: dict[str, Any] = {}
    if client is not None and profile.is_text_like and profile.extracted_text.strip():
        try:
            content = profile.extracted_text[:120_000]
            resp = client.chat_json(
                [
                    {"role": "system", "content": ANALYZE_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"FILE: {profile.path.name} (type={profile.kind}, "
                            f"ext={profile.extension})\n\n"
                            f"Deterministic mark report: {json.dumps(deterministic['marks'])}\n\n"
                            f"CONTENT:\n{content}"
                        ),
                    },
                ],
            )
            semantic = resp if isinstance(resp, dict) else {"raw": str(resp)[:2000]}
            deterministic["recommended_strength"] = semantic.get("recommended_strength", "safe")
        except LLMError as exc:
            errors.append(f"analyze semantic: {exc}")
            status = "degraded"
            deterministic["recommended_strength"] = (
                "aggressive" if deterministic["marks"]["total_marked_chars"] > 0 else "safe"
            )
            return AnalyzeResult(status=status, profile=profile.__dict__, deterministic=deterministic, semantic=semantic, errors=errors)
    elif profile.is_text_like and profile.extracted_text.strip():
        deterministic["recommended_strength"] = (
            "aggressive" if deterministic["marks"]["total_marked_chars"] > 0 else "safe"
        )
    return AnalyzeResult(status="ok", profile=profile.__dict__, deterministic=deterministic, semantic=semantic, errors=errors)