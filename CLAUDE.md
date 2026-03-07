# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

小红书穿搭搜索 + AI 虚拟试穿。Two CLI tools: `xhs` (search XiaoHongShu for outfit inspiration) and `fashn-tryon` (virtual try-on via FASHN API). Designed as an AI skill for Claude Code and OpenClaw.

## Architecture

```
xhs CLI (typer) ──subprocess──→ mcporter call ──HTTP──→ xiaohongshu-mcp (Go, localhost:18060) ──→ XHS API
fashn-tryon CLI (argparse) ──HTTP──→ FASHN API (api.fashn.ai/v1)
```

- **`xhs_cli/`** — Search CLI. Entry: `xhs_cli.app:app`. Uses subprocess to call `mcporter` for MCP tool invocations, and stdlib `urllib` for health checks and image downloads (zero external HTTP deps).
- **`xhs-tryon/fashn_tryon/`** — Virtual try-on CLI. Entry: `fashn_tryon.cli:main`. Uses `requests` + `FashnClient` to call FASHN API. Concurrent job processing via `ThreadPoolExecutor`.
- **`.claude/skills/xhs-fashion-search/`** — Skill definition (`SKILL.md`) + bootstrap scripts. `ensure_env.sh` validates local `uv`/`mcporter` prerequisites, then sets up the Python venv, xiaohongshu-mcp binary/service, and mcporter registration. Standalone OpenClaw installs use `install.sh` to auto-install `uv` and `mcporter` first.
- **`xiaohongshu-mcp`** — Go binary from [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp), runs on `localhost:18060`. Auto-installed by `ensure_env.sh`. Managed by launchd (`com.codex.xiaohongshu-mcp`) on macOS.

This repository contains two Python packages: the root package (`xhs-fashion`) produces the `xhs` binary, and `xhs-tryon/pyproject.toml` defines the `fashn-tryon` package.

## Common Commands

```bash
# Environment setup (requires local `uv` + `mcporter` in PATH)
VENV=$(.claude/skills/xhs-fashion-search/scripts/ensure_env.sh)

# Run CLI tools
$VENV/bin/xhs search images --keyword "男生 早春 简约 穿搭" --image-dir /tmp/xhs-search --json
source .env && $VENV/bin/fashn-tryon run --user-image user.jpg --model-image look.jpg --output-dir /tmp/tryon --json

# Tests (all tests live in tests/)
uv run pytest tests/ -v                                           # all tests
uv run pytest tests/test_runtime.py::TestClassName::test_name -v  # single test
```

## Key Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `FASHN_API_KEY` | FASHN virtual try-on API key (stored in `.env` as `export FASHN_API_KEY=...`) | — |
| `XHS_CLI_SERVICE_URL` | MCP service endpoint | `http://localhost:18060/mcp` |
| `XHS_CLI_COOKIE_FILE` | XHS cookie storage path | `~/.agent-reach/xiaohongshu-mcp/data/cookies.json` |
| `XHS_CLI_STATE_DIR` | CLI persistent state (QR codes, login state) | `~/.xhs-cli` |
| `XHS_CLI_LAUNCHD_LABEL` | launchd label for auto-restarting MCP service | `com.codex.xiaohongshu-mcp` |

## Code Conventions

- Python 3.11+. Uses `uv` for dependency management, never pip.
- `xhs_cli` has zero external HTTP dependencies — uses only stdlib `urllib` for health checks and image downloads. MCP tool calls go through `mcporter` subprocess.
- `fashn_tryon` uses `requests` for FASHN API communication.
- Both CLIs output structured JSON (via `--json`) for AI agent consumption. Status field conventions: `ok`, `partial`, `requires_login`, `error`, `service_unavailable`.
- Exit codes carry meaning: 0=success, 10=auth required, 20=service unavailable, 21=MCP/API error, 30=bad input.
- Auth state is file-based: cookie file existence = logged in, QR state in `~/.xhs-cli/state.json`.

## Documentation Maintenance

When dependencies, installation steps, or architecture change, **always update all of these**:
- `README.md` — main setup guide
- `docs/openclaw-setup.md` — OpenClaw-specific setup
- `CLAUDE.md` (this file) — AI agent guidance

## Known Fragilities

- `xiaohongshu-mcp` is a third-party Go project ([xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)) — may need upstream updates when XHS changes their signing logic.
