#!/usr/bin/env bash
# ensure_env.sh — One-command setup: Python venv + xiaohongshu-mcp service.
# Prints the venv bin path on success. Exit code 0 = ready, non-zero = failed.
# Requires: uv (https://docs.astral.sh/uv/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$SKILL_DIR/.venv"

# Detect project root (may not exist in standalone install)
_candidate="$(cd "$SKILL_DIR/../../.." 2>/dev/null && pwd)"
if [[ -f "$_candidate/pyproject.toml" ]]; then
    PROJECT_ROOT="$_candidate"
    STANDALONE=false
else
    PROJECT_ROOT=""
    STANDALONE=true
fi

GITHUB_REPO="GameHoo/xhs-fashion"

# --- xiaohongshu-mcp settings ---
XHS_MCP_BASE="$HOME/.agent-reach/xiaohongshu-mcp"
XHS_MCP_BIN="$HOME/.local/bin/xiaohongshu-mcp"
XHS_MCP_PORT="${XHS_MCP_PORT:-18060}"
XHS_MCP_URL="http://localhost:${XHS_MCP_PORT}"
XHS_MCP_REPO="xpzouying/xiaohongshu-mcp"
LAUNCHD_LABEL="com.codex.xiaohongshu-mcp"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

# =====================================================================
#  1. Python venv
# =====================================================================
setup_venv() {
    if [[ -x "$VENV_DIR/bin/python3" ]] && \
       [[ -x "$VENV_DIR/bin/xhs" ]] && \
       [[ -x "$VENV_DIR/bin/fashn-tryon" ]]; then
        return 0
    fi

    # Find uv
    local UV
    UV="$(command -v uv 2>/dev/null || true)"
    if [[ -z "$UV" ]]; then
        for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv /opt/homebrew/bin/uv; do
            [[ -x "$p" ]] && UV="$p" && break
        done
    fi
    if [[ -z "$UV" ]]; then
        echo "ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
        return 1
    fi

    echo "Initializing Python environment at $VENV_DIR ..." >&2

    if [[ ! -x "$VENV_DIR/bin/python3" ]]; then
        "$UV" venv --python ">=3.11" "$VENV_DIR" >&2
    fi

    if [[ "$STANDALONE" == true ]]; then
        # Standalone mode: install from GitHub (no local clone needed)
        echo "Installing from GitHub (${GITHUB_REPO})..." >&2
        "$UV" pip install --python "$VENV_DIR/bin/python3" \
            "xhs-fashion @ git+https://github.com/${GITHUB_REPO}.git" >&2
        "$UV" pip install --python "$VENV_DIR/bin/python3" \
            "fashn-tryon @ git+https://github.com/${GITHUB_REPO}.git#subdirectory=xhs-tryon" >&2
    else
        # Dev mode: editable install from local source
        "$UV" pip install --python "$VENV_DIR/bin/python3" -e "$PROJECT_ROOT" >&2
        "$UV" pip install --python "$VENV_DIR/bin/python3" -e "$PROJECT_ROOT/xhs-tryon" >&2
    fi

    local missing=()
    [[ -x "$VENV_DIR/bin/xhs" ]] || missing+=(xhs)
    [[ -x "$VENV_DIR/bin/fashn-tryon" ]] || missing+=(fashn-tryon)
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR: Failed to install: ${missing[*]}" >&2
        return 1
    fi
    echo "Python environment ready." >&2
}

# =====================================================================
#  2. xiaohongshu-mcp service
# =====================================================================

# Detect OS + arch → asset name
detect_asset_name() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) arch="amd64" ;;
        arm64|aarch64) arch="arm64" ;;
        *) echo "ERROR: Unsupported architecture: $arch" >&2; return 1 ;;
    esac
    case "$os" in
        darwin|linux) ;;
        *) echo "ERROR: Unsupported OS: $os" >&2; return 1 ;;
    esac
    echo "xiaohongshu-mcp-${os}-${arch}"
}

# Check if service is reachable (any HTTP response = alive, including 404/405)
service_alive() {
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 "${XHS_MCP_URL}/" 2>/dev/null)" || return 1
    [[ "$code" =~ ^[2-5][0-9][0-9]$ ]]
}

# Download latest binary from GitHub Releases
download_binary() {
    local asset_name="$1"
    local tag url tmp_tar

    echo "Fetching latest release from ${XHS_MCP_REPO}..." >&2
    tag="$(curl -sf "https://api.github.com/repos/${XHS_MCP_REPO}/releases/latest" \
        | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null)" || {
        echo "ERROR: Failed to fetch latest release tag" >&2
        return 1
    }

    local version_dir="${XHS_MCP_BASE}/${tag}"
    if [[ -x "${version_dir}/${asset_name}" ]]; then
        echo "Binary already exists: ${version_dir}/${asset_name}" >&2
        echo "$version_dir"
        return 0
    fi

    url="https://github.com/${XHS_MCP_REPO}/releases/download/${tag}/${asset_name}.tar.gz"
    echo "Downloading ${url}..." >&2

    mkdir -p "$version_dir"
    tmp_tar="$(mktemp)"
    trap "rm -f '$tmp_tar'" RETURN

    curl -fSL --progress-bar -o "$tmp_tar" "$url" >&2 || {
        echo "ERROR: Download failed: $url" >&2
        rm -rf "$version_dir"
        return 1
    }

    tar xzf "$tmp_tar" -C "$version_dir" >&2
    chmod +x "$version_dir"/* 2>/dev/null || true

    # Update current symlink
    ln -sfn "$version_dir" "${XHS_MCP_BASE}/current"

    echo "Downloaded ${tag} to ${version_dir}" >&2
    echo "$version_dir"
}

# Create symlink in ~/.local/bin
ensure_symlink() {
    local current="${XHS_MCP_BASE}/current"
    local asset_name="$1"
    local bin_path="${current}/${asset_name}"

    if [[ ! -x "$bin_path" ]]; then
        echo "ERROR: Binary not found at $bin_path" >&2
        return 1
    fi

    mkdir -p "$(dirname "$XHS_MCP_BIN")"
    ln -sf "$bin_path" "$XHS_MCP_BIN"
}

# macOS: install and load launchd plist
install_launchd() {
    if [[ "$(uname -s)" != "Darwin" ]]; then
        return 0
    fi

    mkdir -p "${XHS_MCP_BASE}/data" "${XHS_MCP_BASE}/logs"
    mkdir -p "$(dirname "$LAUNCHD_PLIST")"

    # Write plist (overwrite if binary path changed)
    cat > "$LAUNCHD_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${XHS_MCP_BIN}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${XHS_MCP_BASE}/data</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${HOME}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${XHS_MCP_BASE}/logs/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${XHS_MCP_BASE}/logs/stderr.log</string>
</dict>
</plist>
PLIST

    local domain="gui/$(id -u)"
    # Remove old service if loaded, then re-register
    launchctl bootout "${domain}/${LAUNCHD_LABEL}" 2>/dev/null || true
    launchctl bootstrap "$domain" "$LAUNCHD_PLIST" 2>/dev/null || true
    # Force (re)start
    launchctl kickstart -k "${domain}/${LAUNCHD_LABEL}" 2>/dev/null || true
    echo "launchd service started: ${LAUNCHD_LABEL}" >&2
}

# Linux: start as background process
start_background() {
    if pgrep -f "xiaohongshu-mcp" >/dev/null 2>&1; then
        echo "xiaohongshu-mcp already running" >&2
        return 0
    fi
    mkdir -p "${XHS_MCP_BASE}/data" "${XHS_MCP_BASE}/logs"
    nohup "$XHS_MCP_BIN" \
        > "${XHS_MCP_BASE}/logs/stdout.log" \
        2> "${XHS_MCP_BASE}/logs/stderr.log" &
    echo "Started xiaohongshu-mcp in background (PID: $!)" >&2
}

# Register with mcporter
ensure_mcporter_config() {
    local MCPORTER
    MCPORTER="$(command -v mcporter 2>/dev/null || true)"
    [[ -z "$MCPORTER" ]] && return 0

    # Check if already configured
    if "$MCPORTER" config list 2>/dev/null | grep -q "xiaohongshu"; then
        return 0
    fi

    "$MCPORTER" config add xiaohongshu "${XHS_MCP_URL}/mcp" >/dev/null 2>&1 || true
    echo "Registered xiaohongshu with mcporter" >&2
}

# Main: ensure xiaohongshu-mcp is running
setup_xhs_mcp() {
    # Already running? Done.
    if service_alive; then
        ensure_mcporter_config
        return 0
    fi

    # Already have binary? Just start it.
    if [[ -x "$XHS_MCP_BIN" ]]; then
        echo "xiaohongshu-mcp binary found, starting service..." >&2
    else
        # Download binary
        local asset_name
        asset_name="$(detect_asset_name)" || return 1

        download_binary "$asset_name" >/dev/null || return 1
        ensure_symlink "$asset_name" || return 1
        echo "Installed xiaohongshu-mcp to ${XHS_MCP_BIN}" >&2
    fi

    # Start service
    if [[ "$(uname -s)" == "Darwin" ]]; then
        install_launchd
    else
        start_background
    fi

    # Wait for service to come up
    local retries=10
    while (( retries-- > 0 )); do
        if service_alive; then
            echo "xiaohongshu-mcp is running on port ${XHS_MCP_PORT}" >&2
            ensure_mcporter_config
            return 0
        fi
        sleep 1
    done

    echo "WARNING: xiaohongshu-mcp started but not yet reachable on port ${XHS_MCP_PORT}." >&2
    echo "  It may need a few more seconds. The xhs CLI will auto-retry on first use." >&2
    ensure_mcporter_config
}

# =====================================================================
#  Main
# =====================================================================
setup_venv || exit 1
setup_xhs_mcp || echo "WARNING: xiaohongshu-mcp setup failed. Search will not work until the service is running." >&2

echo "$VENV_DIR"
