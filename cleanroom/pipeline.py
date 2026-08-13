"""Cleanroom — LangGraph Pipeline

Orchestrate the four-agent workflow: Analyze → Recommend → Remediate → QA.

Deterministic phases need no LLM; semantic steps degrade when the endpoint is offline.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .config import Settings, get_settings
from .llm import LLMClient
from .telemetry import production_telemetry

logger = logging.getLogger(__name__)


class CleanerState(TypedDict, total=False):
    """Shared state for the LangGraph pipeline."""
    run_id: str
    input_path: str
    output_dir: str
    settings: dict[str, Any]
    # phase results
    analyze: dict[str, Any]
    recommend: dict[str, Any]
    remediate: dict[str, Any]
    qa: dict[str, Any]
    errors: list[str]
    current_phase: str
    # controls
    strength: str
    apply: bool
    skip_llm: bool
    max_judge_iterations: int
    judge_score_threshold: float
    final: dict[str, Any]


@dataclass
class CleanerResult:
    """Final pipeline outcome."""
    run_id: str
    state: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "phases": {
                k: self.state.get(k)
                for k in ("analyze", "recommend", "remediate", "qa")
            },
            "final": self.state.get("final"),
            "errors": self.errors,
        }


def _settings_dict(settings: Settings) -> dict[str, Any]:
    return settings.to_safe_dict()


def _client_from_state(state: CleanerState) -> LLMClient | None:
    """Build an LLM client from state + environment, or None if unreachable.

    API key is never in state. Returns None when the configured endpoint is
    not reachable, so offline/deterministic runs skip LLM work entirely
    instead of burning retry backoff on a dead connection.
    """
    s = state.get("settings") or {}
    if state.get("skip_llm"):
        return None
    settings = Settings(
        omlx_base_url=s.get("omlx_base_url", "http://127.0.0.1:8000/v1"),
        omlx_model=s.get("omlx_model", "DeepSeek-V4-Flash-0731-MXFP4-MLX"),
        omlx_model_judge=s.get("omlx_model_judge", "DeepSeek-V4-Flash-0731-MXFP4-MLX"),
        omlx_model_final_judge=s.get("omlx_model_final_judge", ""),
        llm_provider=s.get("llm_provider", "omlx"),
    )
    try:
        from .web.runner import omlx_reachable
        if not omlx_reachable(settings):
            return None
    except Exception:  # noqa: BLE001
        return None
    return LLMClient(settings, telemetry=production_telemetry(), node=str(state.get("current_phase") or "pipeline"))


def _close_client(client: LLMClient | None) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not close LLM client: %s", exc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def node_analyze(state: CleanerState) -> CleanerState:
    from .phases.phase1_analyze import analyze_file

    state = dict(state)
    state["current_phase"] = "analyze"
    errors = list(state.get("errors") or [])
    client = None
    try:
        client = _client_from_state(state)
        result = analyze_file(Path(state["input_path"]), client=client)
        state["analyze"] = {
            "status": result.status,
            "profile": result.profile,
            "deterministic": result.deterministic,
            "semantic": result.semantic,
        }
        errors.extend(f"analyze: {e}" for e in result.errors)
    except Exception as e:
        errors.append(f"analyze: {e}")
        state["analyze"] = {"status": "error", "error": str(e)}
    finally:
        _close_client(client)
    state["errors"] = errors
    return state  # type: ignore[return-value]


def node_recommend(state: CleanerState) -> CleanerState:
    from .phases.phase2_recommend import recommend

    state = dict(state)
    state["current_phase"] = "recommend"
    errors = list(state.get("errors") or [])
    client = None
    try:
        analysis = state.get("analyze") or {}
        client = _client_from_state(state)
        result = recommend(analysis, client=client)
        state["recommend"] = {"status": result.status, "plan": result.plan}
        errors.extend(f"recommend: {e}" for e in result.errors)
    except Exception as e:
        errors.append(f"recommend: {e}")
        state["recommend"] = {"status": "error", "error": str(e)}
    finally:
        _close_client(client)
    state["errors"] = errors
    return state  # type: ignore[return-value]


def node_remediate(state: CleanerState) -> CleanerState:
    from .phases.phase3_remediate import remediate_file

    state = dict(state)
    state["current_phase"] = "remediate"
    errors = list(state.get("errors") or [])
    client = None
    try:
        client = _client_from_state(state)
        plan = (state.get("recommend") or {}).get("plan") or {}
        result = remediate_file(
            Path(state["input_path"]),
            plan,
            client=client,
            output_dir=Path(state.get("output_dir") or state["settings"].get("output_dir") or "."),
            strength=state.get("strength"),
            apply=bool(state.get("apply", True)),
        )
        state["remediate"] = {
            "status": result.status,
            "output_path": result.output_path,
            "backup_path": result.backup_path,
            "actions": result.actions,
        }
        errors.extend(f"remediate: {e}" for e in result.errors)
    except Exception as e:
        errors.append(f"remediate: {e}")
        state["remediate"] = {"status": "error", "error": str(e)}
    finally:
        _close_client(client)
    state["errors"] = errors
    return state  # type: ignore[return-value]


def node_qa(state: CleanerState) -> CleanerState:
    from .phases.phase3_remediate import remediate_file
    from .phases.phase4_qa import qa_file

    state = dict(state)
    state["current_phase"] = "qa"
    errors = list(state.get("errors") or [])
    client = None
    try:
        client = _client_from_state(state)
        input_path = Path(state["input_path"])
        rem = state.get("remediate") or {}
        output_path = Path(rem.get("output_path") or input_path)

        # Re-enter remediation through QA's revise loop using the same plan.
        plan = (state.get("recommend") or {}).get("plan") or {}

        def _revise(text: str) -> str:
            if client is None:
                return ""
            from .llm import LLMError
            try:
                return client.chat(
                    [
                        {"role": "system", "content": "Revise this content to reduce watermarking while preserving all facts. Output only the revised content."},
                        {"role": "user", "content": text[:120_000]},
                    ],
                    node="qa_revise",
                    temperature=0.7,
                )
            except LLMError as exc:
                errors.append(f"qa revise: {exc}")
                return ""

        result = qa_file(
            input_path,
            output_path,
            client=client,
            max_iterations=state.get("max_judge_iterations", get_settings().max_judge_iterations),
            threshold=state.get("judge_score_threshold", get_settings().judge_score_threshold),
            remediator=_revise,
        )
        state["qa"] = {
            "status": result.status,
            "verdict": result.verdict,
            "score": result.score,
            "iterations": result.iterations,
            "deterministic": result.deterministic,
            "findings": result.findings,
        }
        errors.extend(f"qa: {e}" for e in result.errors)
        # final verdict
        state["final"] = {
            "run_id": state.get("run_id"),
            "input_path": state.get("input_path"),
            "output_path": str(output_path),
            "phase": "qa",
            "verdict": result.verdict,
            "qa_status": result.status,
            "completed_at": _now(),
        }
    except Exception as e:
        errors.append(f"qa: {e}")
        state["qa"] = {"status": "error", "error": str(e)}
        state["final"] = {"run_id": state.get("run_id"), "verdict": "FAIL", "error": str(e)}
    finally:
        _close_client(client)
    state["errors"] = errors
    return state  # type: ignore[return-value]


def build_graph() -> StateGraph:
    """Build the compiled LangGraph StateGraph."""
    g = StateGraph(CleanerState)
    g.add_node("analyze", node_analyze)
    g.add_node("recommend", node_recommend)
    g.add_node("remediate", node_remediate)
    g.add_node("qa", node_qa)
    g.set_entry_point("analyze")
    g.add_edge("analyze", "recommend")
    g.add_edge("recommend", "remediate")
    g.add_edge("remediate", "qa")
    g.add_edge("qa", END)
    return g


def run_cleanroom(
    input_path: str | Path,
    *,
    strength: str | None = None,
    apply: bool = True,
    output_dir: str | Path | None = None,
    settings: Settings | None = None,
    skip_llm: bool = False,
    compile_graph: bool = True,
) -> CleanerResult:
    """Run the full 4-phase workflow on a single file.

    Deterministic phases run without an LLM; semantic/rewrite/judge steps are
    attempted and degrade gracefully when the endpoint is unreachable. Set
    ``skip_llm=True`` to force the deterministic path even when an endpoint
    is online (used by offline tests and ``--preview``-style hygiene).
    """
    settings = settings or get_settings()
    input_path = Path(input_path).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"input file not found: {input_path}")
    output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"cleanroom-{uuid.uuid4().hex[:8]}"
    initial: CleanerState = {
        "run_id": run_id,
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "settings": _settings_dict(settings),
        "strength": strength or settings.default_strength,
        "apply": apply,
        "skip_llm": skip_llm,
        "max_judge_iterations": settings.max_judge_iterations,
        "judge_score_threshold": settings.judge_score_threshold,
        "errors": [],
        "current_phase": "analyze",
    }

    if compile_graph:
        app = build_graph().compile()
        result = app.invoke(initial)
    else:
        # sequential fallback (no compile needed for tests without langgraph)
        result = dict(initial)
        for node in (node_analyze, node_recommend, node_remediate, node_qa):
            result = node(result)

    _log_run(result, settings)
    return CleanerResult(run_id=run_id, state=result, errors=list(result.get("errors") or []))


def _log_run(state: dict[str, Any], settings: Settings) -> None:
    """Append a run record to the run ledger (JSONL)."""
    try:
        ledger = settings.run_ledger_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(state.get("final") or state.get("qa") or {"run_id": state.get("run_id")}) + "\n")
    except Exception:  # noqa: BLE001
        logger.warning("run ledger write failed", exc_info=True)