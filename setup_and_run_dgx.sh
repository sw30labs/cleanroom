#!/usr/bin/env bash
# DGX bring-up — a thin shim over setup_and_run.sh.
#
# A shim cannot rot: it just forwards to the single source of truth.
#
#   ./setup_and_run_dgx.sh             # qwen27 recipe
#   ./setup_and_run_dgx.sh coder-next  # any recipe from ~/models/recipes
set -euo pipefail

exec "$(dirname "$0")/setup_and_run.sh" --provider dgx "$@"