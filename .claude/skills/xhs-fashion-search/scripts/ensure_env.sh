#!/usr/bin/env bash
# ensure_env.sh — Check and initialize the skill's Python venv.
# Prints the venv path on success. Exit code 0 = ready, non-zero = failed.
# Requires: uv (https://docs.astral.sh/uv/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
VENV_DIR="$SKILL_DIR/.venv"

# --- 1. Check if venv already exists and is functional ---
if [[ -x "$VENV_DIR/bin/python3" ]] && \
   [[ -x "$VENV_DIR/bin/xhs" ]] && \
   [[ -x "$VENV_DIR/bin/fashn-tryon" ]]; then
    echo "$VENV_DIR"
    exit 0
fi

# --- 2. Find uv ---
UV="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV" ]]; then
    # Common install locations
    for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv /opt/homebrew/bin/uv; do
        [[ -x "$p" ]] && UV="$p" && break
    done
fi
if [[ -z "$UV" ]]; then
    echo "ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

echo "Initializing skill environment at $VENV_DIR ..." >&2

# --- 3. Create venv with Python 3.11+ ---
if [[ ! -x "$VENV_DIR/bin/python3" ]]; then
    "$UV" venv --python ">=3.11" "$VENV_DIR" >&2
fi

# --- 4. Install project packages ---
"$UV" pip install --python "$VENV_DIR/bin/python3" -e "$PROJECT_ROOT" >&2
"$UV" pip install --python "$VENV_DIR/bin/python3" -e "$PROJECT_ROOT/xhs-tryon" >&2

# --- 5. Verify ---
missing=()
[[ -x "$VENV_DIR/bin/xhs" ]] || missing+=(xhs)
[[ -x "$VENV_DIR/bin/fashn-tryon" ]] || missing+=(fashn-tryon)

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: Failed to install: ${missing[*]}" >&2
    exit 1
fi

echo "Environment ready." >&2
echo "$VENV_DIR"
