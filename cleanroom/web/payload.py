"""Cleanroom — Dashboard Payload

Aggregate live store/telemetry/config state for the browser.

Display-and-control only; never mutates the running Settings object.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..telemetry import build_cost_summary


def _run_ledger(settings: Settings) -> list[dict[str, Any]]:
    path = settings.run_ledger_path
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records[::-1][:50]


def build_settings_payload(settings: Settings) -> dict[str, Any]:
    from . import runner
    reachable = runner.omlx_reachable(settings)
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "inference": {
            "provider": settings.llm_provider,
            "base_url": settings.llm_base_url,
            "reachable": reachable,
            "model": settings.llm_model,
            "model_judge": settings.llm_model_judge,
        },
        "pipeline": {
            "default_strength": settings.default_strength,
            "max_judge_iterations": settings.max_judge_iterations,
            "judge_score_threshold": settings.judge_score_threshold,
            "auto_apply": bool(settings.auto_apply),
        },
        "stores": {
            "output_dir": str(settings.output_dir),
            "run_ledger": _run_ledger_info(settings.run_ledger_path),
            "telemetry": _run_ledger_info(settings.telemetry_path),
        },
    }


def _run_ledger_info(path: Path) -> dict[str, Any]:
    try:
        return {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else None,
        }
    except OSError:
        return {"path": str(path), "exists": False, "size_bytes": None}


def build_dashboard_payload() -> dict[str, Any]:
    """Aggregate everything the dashboard renders on load."""
    from . import runner
    settings = get_settings()
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "llm_reachable": runner.omlx_reachable(settings),
            "busy": runner.busy(),
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "default_strength": settings.default_strength,
        },
        "jobs": runner.list_jobs(),
        "runs": _run_ledger(settings),
        "cost": build_cost_summary(settings.telemetry_path),
        "settings": build_settings_payload(settings)["inference"],
    }