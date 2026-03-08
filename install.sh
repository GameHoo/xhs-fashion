#!/usr/bin/env bash
# install.sh — One-command installer for xhs-fashion skill.
# Usage: curl -fsSL https://raw.githubusercontent.com/GameHoo/xhs-fashion/main/install.sh | bash
#
# Installs to ~/.openclaw/skills/xhs-fashion-search/ by default.
# Override with: ... | bash -s -- --dir /path/to/skill-dir
#
# Auto-installs: uv, mcporter. Requires: Node.js (for mcporter).

set -euo pipefail

REPO="GameHoo/xhs-fashion"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/.claude/skills/xhs-fashion-search"
SKILL_DIR=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) SKILL_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# Auto-detect target directory
if [[ -z "$SKILL_DIR" ]]; then
    SKILL_DIR="$HOME/.openclaw/skills/xhs-fashion-search"
fi

# =====================================================================
#  Auto-install dependencies
# =====================================================================

# uv — Python package manager
ensure_uv() {
    if command -v uv &>/dev/null; then
        return 0
    fi
    # Check common paths
    for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv /opt/homebrew/bin/uv; do
        [[ -x "$p" ]] && return 0
    done
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in this session
    export PATH="$HOME/.local/bin:$PATH"
}

# mcporter — MCP service client (requires Node.js/npm)
ensure_mcporter() {
    if command -v mcporter &>/dev/null; then
        return 0
    fi
    if ! command -v npm &>/dev/null; then
        echo ""
        echo "ERROR: Node.js is required for mcporter but not found."
        echo "Install Node.js first:"
        echo "  macOS:  brew install node"
        echo "  Linux:  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt install -y nodejs"
        echo ""
        echo "Then re-run this installer."
        exit 1
    fi
    echo "Installing mcporter..."
    npm install -g mcporter
}

echo "=== xhs-fashion installer ==="
echo ""

ensure_uv
ensure_mcporter

# =====================================================================
#  Download skill files
# =====================================================================

echo ""
echo "Installing skill to: $SKILL_DIR"

mkdir -p "$SKILL_DIR/scripts"

echo "Downloading skill files..."
curl -fsSL "$BASE_URL/SKILL.md"                -o "$SKILL_DIR/SKILL.md"
curl -fsSL "$BASE_URL/scripts/ensure_env.sh"   -o "$SKILL_DIR/scripts/ensure_env.sh"
curl -fsSL "$BASE_URL/scripts/make_collage.py"  -o "$SKILL_DIR/scripts/make_collage.py"
curl -fsSL "$BASE_URL/scripts/split_collage.py" -o "$SKILL_DIR/scripts/split_collage.py"
chmod +x "$SKILL_DIR/scripts/ensure_env.sh"

# =====================================================================
#  Setup Python env + xiaohongshu-mcp service
# =====================================================================

echo "Setting up environment..."

VENV=$("$SKILL_DIR/scripts/ensure_env.sh")

echo ""
echo "=== Installation complete ==="
echo "Skill directory: $SKILL_DIR"
echo "Python venv:     $VENV"
echo ""
echo "Next steps:"
echo "  1. Start a new OpenClaw conversation"
echo "  2. Say \"不知道穿什么\" or \"帮我搜穿搭\""
echo "  3. The skill will guide you through the rest!"
