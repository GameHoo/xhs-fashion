#!/usr/bin/env bash
# install.sh — One-command installer for xhs-fashion skill.
# Usage: curl -fsSL https://raw.githubusercontent.com/GameHoo/xhs-fashion/main/install.sh | bash
#
# Installs to ~/.openclaw/skills/xhs-fashion-search/ by default.
# Override with: ... | bash -s -- --dir /path/to/skill-dir

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

echo "Installing xhs-fashion skill to: $SKILL_DIR"

mkdir -p "$SKILL_DIR/scripts"

# Download skill files
echo "Downloading skill files..."
curl -fsSL "$BASE_URL/SKILL.md"              -o "$SKILL_DIR/SKILL.md"
curl -fsSL "$BASE_URL/scripts/ensure_env.sh" -o "$SKILL_DIR/scripts/ensure_env.sh"
curl -fsSL "$BASE_URL/scripts/make_collage.py" -o "$SKILL_DIR/scripts/make_collage.py"
chmod +x "$SKILL_DIR/scripts/ensure_env.sh"

echo "Skill files downloaded. Running setup..."

# Run ensure_env.sh (it auto-detects standalone mode)
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
