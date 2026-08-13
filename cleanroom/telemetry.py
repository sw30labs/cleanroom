"""Cleanroom — LLM Telemetry

Append-only JSONL ledger of every LLM call for cost and usage visibility.

Single choke point (LLMClient) emits one event per attempt; this owns the sink.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TelemetryLogger:
    """Append-only JSONL sink. Thread-safe, never raises on write failure."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, **fields: Any) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            event = {
                "ts": datetime.now(timezone.utc).isoformat(),
                **fields,
            }
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event) + "\n")
        except Exception:  # noqa: BLE001 — telemetry must never break a run
            return

    def load_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return events


def production_telemetry() -> TelemetryLogger:
    from .config import get_settings
    return TelemetryLogger(get_settings().telemetry_path)


def build_cost_summary(path: Path | None = None) -> dict[str, Any] | None:
    """Aggregate the LLM telemetry log for the dashboard Costs view."""
    from .config import get_settings
    p = path or get_settings().telemetry_path
    logger = TelemetryLogger(p)
    events = logger.load_events()
    if not events:
        return None

    by_node: dict[str, dict[str, Any]] = {}
    spend = 0.0
    tok_in = tok_out = 0
    for e in events:
        node = e.get("node") or "unknown"
        b = by_node.setdefault(node, {"n": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "errors": 0})
        b["n"] += 1
        b["input_tokens"] += e.get("input_tokens") or 0
        b["output_tokens"] += e.get("output_tokens") or 0
        b["latency_ms"] += e.get("latency_ms") or 0
        tok_in += e.get("input_tokens") or 0
        tok_out += e.get("output_tokens") or 0
        if e.get("status") == "error":
            b["errors"] += 1
        spend += e.get("cost_usd") or 0
    for b in by_node.values():
        b["latency_avg_ms"] = round(b["latency_ms"] / max(b["n"], 1))
        b.pop("latency_ms", None)
    return {
        "path": str(p),
        "n_events": len(events),
        "spend_usd": round(spend, 6),
        "tokens": {"input": tok_in, "output": tok_out},
        "by_node": dict(sorted(by_node.items())),
        "recent": [
            {
                "ts": e["ts"], "node": e.get("node"), "model_id": e.get("model_id"),
                "status": e.get("status"), "latency_ms": e.get("latency_ms"),
                "input_tokens": e.get("input_tokens"), "output_tokens": e.get("output_tokens"),
            }
            for e in events[-30:][::-1]
        ],
    }