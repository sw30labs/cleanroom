"""Cleanroom — Dashboard Runner

Background job runner so the HTTP layer can launch and poll cleaner runs.

One job at a time (SQLite is single-writer); jobs run in a daemon thread.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import Settings, get_settings

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_BUSY = threading.Lock()

MAX_INPUT_BYTES = 100 * 1024 * 1024  # 100 MiB
MAX_RECENT_JOBS = 25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def omlx_reachable(settings: Settings | None = None, timeout: float = 2.0) -> bool:
    """TCP-level reachability check for the active LLM endpoint."""
    settings = settings or get_settings()
    url = urlparse(settings.llm_base_url)
    host = url.hostname or "127.0.0.1"
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def busy() -> bool:
    return _BUSY.locked()


def get_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs() -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        return [
            {k: v for k, v in job.items()}
            for job in sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)
        ]


def start_clean_job(
    filename: str,
    b64_content: str,
    *,
    strength: str = "safe",
    apply: bool = True,
) -> tuple[str | None, str | None]:
    """Launch a cleanroom run. Returns (job_id, None) or (None, error)."""
    if not _BUSY.acquire(blocking=False):
        return None, "another job is still running — wait for it to finish"
    job_id = f"web-{uuid.uuid4().hex[:8]}"
    try:
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "id": job_id,
                "filename": filename,
                "strength": strength,
                "apply": apply,
                "status": "running",
                "started_at": _now(),
                "finished_at": None,
                "result": None,
                "error": None,
            }
            if len(_JOBS) > MAX_RECENT_JOBS:
                for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started_at"])[: len(_JOBS) - MAX_RECENT_JOBS]:
                    if _JOBS[old]["status"] != "running":
                        del _JOBS[old]
        threading.Thread(
            target=_run, args=(job_id, filename, b64_content, strength, apply),
            daemon=True,
        ).start()
    except BaseException:
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id].update(status="error", error="failed to start job thread", finished_at=_now())
        _BUSY.release()
        raise
    return job_id, None


def _run(job_id: str, filename: str, b64_content: str, strength: str, apply: bool) -> None:
    from ..pipeline import run_cleanroom

    try:
        # decode + write to a temp input under generated/inbox
        data = base64.b64decode(b64_content)
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
        settings = get_settings()
        inbox = settings.output_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name
        input_path = inbox / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        input_path.write_bytes(data)

        result = run_cleanroom(
            input_path,
            strength=strength,
            apply=apply,
            settings=settings,
        )
        status = "done"
        error = None
        payload = result.summary() if result else None
    except Exception as exc:  # surfaced to the dashboard console
        payload, status, error = None, "error", str(exc)
    finally:
        with _JOBS_LOCK:
            _JOBS[job_id].update(status=status, result=payload, error=error, finished_at=_now())
        _BUSY.release()