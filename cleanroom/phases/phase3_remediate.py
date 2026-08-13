"""Cleanroom — Phase 3 Remediate

Apply the approved plan and write a cleaned sibling via safe atomic writes.

Never mutates the original in place unless the operator asks.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..files import (
    classify,
    extract_text,
    remediate_deterministic,
    safe_write_bytes,
    safe_write_text,
    write_backup,
)
from ..llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

REWRITE_PROMPT = """You are the REMEDIATE agent in a file-hygiene pipeline.

Rewrite the following content to reduce statistical token-level watermarking
while preserving every fact, number, name, and technical identifier. Change
word choice, clause order, connectors, and sentence boundaries where meaning
allows. Do not add or remove claims. Do not add watermarks of your own.
Output only the rewritten content.
"""


@dataclass
class RemediateResult:
    status: str  # ok | skipped | degraded | error
    output_path: str = ""
    backup_path: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _suggest_output(path: Path, output_dir: Path) -> Path:
    """Default output location: <output_dir>/<stem>.cleaned<ext>."""
    return output_dir / f"{path.stem}.cleaned{path.suffix or '.txt'}"


def remediate_file(
    input_path: Path,
    plan: dict[str, Any],
    *,
    client: LLMClient | None = None,
    output_dir: Path | None = None,
    out_path: Path | None = None,
    strength: str | None = None,
    apply: bool = True,
) -> RemediateResult:
    """Execute the plan against one file."""
    profile = classify(input_path)
    if not profile.exists:
        return RemediateResult(status="error", errors=[f"input not found: {input_path}"])
    output_dir = output_dir or input_path.parent
    out_path = out_path or _suggest_output(input_path, output_dir)
    strength = strength or plan.get("recommended_strength", "safe")

    actions: list[dict[str, Any]] = []
    errors: list[str] = []
    backup: str = ""

    if not apply:
        # Preview mode: do the deterministic work, return actions without writing.
        data = input_path.read_bytes()
        if profile.is_text_like or profile.kind == "text":
            r = remediate_deterministic(profile, data)
            actions = r["actions"]
        return RemediateResult(status="preview", actions=actions)

    backup = str(write_backup(out_path) or "") if out_path.exists() else ""

    # 1. Deterministic remediation (always, lossless first)
    data = input_path.read_bytes()
    deterministic: dict[str, Any] = {}
    if profile.is_text_like or profile.kind == "text":
        deterministic = remediate_deterministic(profile, data)
        actions.extend(deterministic["actions"])
    else:
        deterministic = remediate_deterministic(profile, data)
        actions.extend(deterministic["actions"])

    content = deterministic.get("content")

    # 2. Optional LLM rewrite (aggressive strength only)
    if strength == "aggressive" and content is not None and content.strip():
        wants_rewrite = any(a.get("op") == "rewrite" for a in plan.get("actions", []))
        if wants_rewrite and client is not None:
            try:
                rewritten = client.chat(
                    [
                        {"role": "system", "content": REWRITE_PROMPT},
                        {"role": "user", "content": content[:120_000]},
                    ],
                    node="remediate",
                    temperature=0.7,
                )
                if rewritten and rewritten.strip():
                    content = rewritten
                    actions.append(
                        {
                            "op": "rewrite",
                            "description": "LLM rewrite to reduce statistical marks",
                            "verifiable": False,
                            "best_effort": True,
                        }
                    )
            except LLMError as exc:
                errors.append(f"remediate rewrite: {exc}")
                actions.append(
                    {"op": "rewrite_skipped", "reason": f"LLM unavailable: {exc}"}
                )
        elif wants_rewrite:
            actions.append(
                {"op": "rewrite_skipped", "reason": "rewrite requires an LLM client"}
            )

    # 3. Write output
    try:
        if content is not None:
            safe_write_text(out_path, content, deterministic.get("encoding", "utf-8"))
        else:
            # binary/office/image: copy through unchanged (metadata strip pending)
            safe_write_bytes(out_path, data)
            actions.append(
                {
                    "op": "metadata_strip_pending",
                    "note": "binary pass-through — format-aware metadata strip needs exiftool/c2patool",
                }
            )
    except OSError as exc:
        errors.append(f"write: {exc}")
        return RemediateResult(status="error", actions=actions, errors=errors)

    return RemediateResult(
        status="ok",
        output_path=str(out_path),
        backup_path=backup,
        actions=actions,
        errors=errors,
    )