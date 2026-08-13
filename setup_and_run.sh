#!/usr/bin/env bash
# Set up Cleanroom from a source checkout and start the local dashboard.
#
# Usage:
#   ./setup_and_run.sh                        # auto-detect LLM backend
#   ./setup_and_run.sh --provider omlx        # Mac MLX server
#   ./setup_and_run.sh --provider dgx         # DGX vLLM backend, qwen27 recipe
#   ./setup_and_run.sh --provider dgx coder-next   # any ~/models recipe
#
# The script is a single source of truth: venv creation, package install,
# test run, backend detection, stale-dashboard guard, and server start.
# A provider flag only picks WHICH backend to talk to; the checkout travels
# between the Mac and the DGX, so the venv is chosen by PLATFORM, never by
# provider.
set -euo pipefail

cd "$(dirname "$0")"

PROVIDER="${CLEANROOM_LLM_PROVIDER:-auto}"
if [ "${1:-}" = "--provider" ]; then
    PROVIDER="${2:?--provider needs a value (auto|omlx|dgx)}"
    shift 2
fi
RECIPE="${1:-qwen27}"
SERVE="${CLEANROOM_DGX_SERVE_SCRIPT:-${HOME}/Desktop/serve-local-llm.sh}"

if [ "$(uname -s)" = "Darwin" ]; then
    VENV=.venv
    PLATFORM_DEFAULT=omlx
else
    VENV=.venv-dgx
    PLATFORM_DEFAULT=dgx
fi

case "$PROVIDER" in
    auto)     ;;
    omlx|mlx) PROVIDER=omlx ;;
    dgx|vllm) PROVIDER=dgx ;;
    *) echo "ERROR: unknown provider '$PROVIDER' (expected auto, omlx or dgx)" >&2
       exit 1 ;;
esac

PORT="${CLEANROOM_PORT:-8080}"

# Dashboards from earlier runs survive the terminal that started them and keep
# holding the port, which makes a fresh start look like a hang. Clear them
# first — but only processes whose executable is python, so a shell/editor
# that merely mentions the module is never a kill target.
dashboard_pids() {
    local pid comm
    for pid in $(pgrep -f ' -m cleanroom\\.web' 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        comm="$(ps -o comm= -p "$pid" 2>/dev/null || true)"
        case "${comm##*/}" in
            python | python[0-9]*) printf '%s\n' "$pid" ;;
        esac
    done
}

stop_stale_dashboards() {
    local pids
    pids="$(dashboard_pids)"
    if [ -n "$pids" ]; then
        echo "==> Stopping stale dashboard process(es): $(echo "$pids" | tr '\n' ' ')"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 2
        pids="$(dashboard_pids)"
        if [ -n "$pids" ]; then
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
            sleep 1
        fi
    fi
    if command -v lsof >/dev/null 2>&1 &&
       lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "ERROR: port $PORT is held by a process that is not a Cleanroom dashboard:" >&2
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
        echo "Stop it, or set CLEANROOM_PORT to a free port." >&2
        exit 1
    fi
}

# Pick a Python 3.12 or 3.13 interpreter (project requires >=3.12,<3.14).
# Prefer the existing venv's interpreter so an already-built venv is reused
# verbatim; otherwise look for a system python3.12/3.13. python3.11 is
# rejected (too old) and conda's base (3.14) is rejected (too new).
pick_python() {
    if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import sys; exit(0 if (3,12)<=sys.version_info<(3,14) else 1)' 2>/dev/null; then
        echo "$VENV/bin/python"
        return
    fi
    for cand in python3.12 python3.13; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; exit(0 if (3,12)<=sys.version_info<(3,14) else 1)' 2>/dev/null; then
            echo "$cand"
            return
        fi
    done
    # last resort: any python, but only if it satisfies the constraint
    if python3 -c 'import sys; exit(0 if (3,12)<=sys.version_info<(3,14) else 1)' 2>/dev/null; then
        echo "python3"
        return
    fi
    echo ""
}

# conda's shell hook can auto-activate a base env and shadow an activate
# script. Always call the venv's interpreter by absolute path instead of
# relying on `source activate`, so a conda-backed checkout can't drift.
# PY is resolved up front; if a venv exists we ignore the system default.
VENV_PY="$(pick_python)"
if [ -z "$VENV_PY" ]; then
    echo "ERROR: no Python in [3.12, 3.14) found. Need python3.12 or python3.13." >&2
    exit 1
fi
echo "==> Using $VENV_PY ($($VENV_PY --version 2>&1))"

if [ ! -d "$VENV" ]; then
    echo "==> Creating virtual environment in $VENV"
    "$VENV_PY" -m venv "$VENV"
    VENV_PY="$VENV/bin/python"
fi
PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV_PY"

echo "==> Installing package with dev and pdf dependencies"
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -e '.[dev,pdf]'

# Which backend? Ask the server what it serves rather than inspecting the
# machine: both providers default to the same endpoint and differ only in
# model id, so reachability proves nothing — an OMLX-configured run on a DGX
# connects happily and then fails every call on a model vLLM does not serve.
bring_up_vllm() {
    echo "==> Bringing up vLLM (recipe: $RECIPE) on 127.0.0.1:8000"
    if curl -sS --max-time 3 -H "Authorization: Bearer ***" \
            http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
        echo "    already serving — leaving the current model in place"
    elif [ -x "$SERVE" ]; then
        "$SERVE" up "$RECIPE"
    else
        echo "ERROR: no vLLM server on 127.0.0.1:8000 and no bring-up script" >&2
        echo "       at $SERVE (override with CLEANROOM_DGX_SERVE_SCRIPT)." >&2
        exit 1
    fi
}

if [ "$PROVIDER" = "auto" ]; then
    echo "==> Detecting LLM backend"
    # Ask the endpoint which provider serves it by probing /v1/models: OMLX
    # and vLLM both answer, but only vLLM serves the DGX recipe ids. We detect
    # by model id shape rather than by hostname so a tunneled DGX still works.
    if curl -sS --max-time 3 -H "Authorization: Bearer ***" \
            http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
        SERVED_MODEL="$(curl -sS --max-time 3 -H "Authorization: Bearer ***" \
            http://127.0.0.1:8000/v1/models \
            | python -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || true)"
        if case "$SERVED_MODEL" in Qwen/*|qwen*|*FP8|*f8) true;; *) false;; esac; then
            PROVIDER=dgx
        else
            PROVIDER=omlx
        fi
        echo "    detected: $PROVIDER (serving $SERVED_MODEL)"
    elif [ "$PLATFORM_DEFAULT" = "dgx" ] && [ -x "$SERVE" ]; then
        echo "    nothing served yet — starting the local backend, then retrying"
        bring_up_vllm
        PROVIDER="$PLATFORM_DEFAULT"
        echo "    using: $PROVIDER"
    else
        PROVIDER="$PLATFORM_DEFAULT"
        echo "    undecided — falling back to the $(uname -s) default: $PROVIDER"
    fi
fi

if [ "$PROVIDER" = "dgx" ]; then
    bring_up_vllm
    # Pin the model from the server itself: dgx.model must match GET /v1/models,
    # and deriving it here keeps recipe switches from drifting the config.
    MODEL="$(curl -sS --max-time 5 -H "Authorization: Bearer ***" \
        http://127.0.0.1:8000/v1/models \
        | python -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')"
    echo "==> Served model: $MODEL"
fi

echo "==> Running test suite"
"$PY" -m pytest

export CLEANROOM_LLM_PROVIDER="$PROVIDER"
if [ "$PROVIDER" = "dgx" ]; then
    export VLLM_MODEL="$MODEL"
    export VLLM_API_KEY="${VLLM_API_KEY:-test}"
    echo "    headless? tunnel with: ssh -L $PORT:127.0.0.1:$PORT <this-host>"
fi

stop_stale_dashboards

echo "==> Starting local dashboard at http://127.0.0.1:$PORT (provider=$PROVIDER, Ctrl+C to stop)"
"$PY" -m cleanroom.web --port "$PORT"