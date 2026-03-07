# xhs-fashion

小红书穿搭搜索 + AI 虚拟试穿。通过 AI Skill 引导用户找到合适的穿搭，搜索小红书获取灵感图，再用 FASHN 虚拟试穿看效果。支持 Claude Code 和 OpenClaw。

## 前置依赖

只需 **Node.js >= 18**（`brew install node`）。其余依赖（uv、mcporter、xiaohongshu-mcp）由安装脚本自动安装。

## 安装

```bash
# 1. 一键初始化（Python venv + xiaohongshu-mcp 服务 + mcporter 注册）
VENV=$(.claude/skills/xhs-fashion-search/scripts/ensure_env.sh)

# 2. 配置 FASHN API Key（虚拟试穿用，可跳过）
echo 'export FASHN_API_KEY=你的key' > .env

# 3. 登录小红书
$VENV/bin/xhs login start --wait --json
```

`ensure_env.sh` 会自动完成：
- 创建 Python venv 并安装 xhs CLI 和 fashn-tryon CLI
- 从 GitHub Releases 下载 xiaohongshu-mcp 二进制（按平台自动选择）
- macOS 上配置 launchd 保活服务，Linux 上后台启动
- 向 mcporter 注册服务端点

已安装过的环境重复运行会秒返回（幂等）。

> **给 AI agent 的提示**：安装完成后应主动执行登录命令并将二维码图片发送给用户，用户只需要扫码，不需要手动运行任何命令。

## OpenClaw 安装 Skill

一行命令，不需要 clone 整个项目：

```bash
curl -fsSL https://raw.githubusercontent.com/GameHoo/xhs-fashion/main/install.sh | bash
```

脚本会自动下载 skill 文件、创建 Python 环境、安装 CLI 工具和 xiaohongshu-mcp 服务。

详见 [OpenClaw 安装指南](docs/openclaw-setup.md)。

## CLI 命令

```bash
VENV=.claude/skills/xhs-fashion-search/.venv

# 搜索小红书穿搭
$VENV/bin/xhs search images --keyword "男生 早春 简约 穿搭" --image-dir /tmp/xhs-search --json

# 虚拟试穿（需要先 source .env）
source .env && $VENV/bin/fashn-tryon run \
  --user-image user.jpg \
  --model-image look.jpg \
  --output-dir /tmp/xhs-tryon \
  --json
```

Key commands:

- `xhs login start` — 登录小红书（生成二维码）
- `xhs login status` — 检查登录状态
- `xhs login reset` — 重置登录
- `xhs search images` — 搜索并下载穿搭图片
- `fashn-tryon run` — 虚拟试穿
- `fashn-tryon resume` — 恢复中断的试穿任务

## 架构

```
xhs CLI ──subprocess──→ mcporter call ──HTTP──→ xiaohongshu-mcp (Go, localhost:18060)
                                                       │
                                                  小红书 API

fashn-tryon CLI ──HTTP──→ FASHN API (api.fashn.ai/v1)
```

- `xhs_cli/` — 搜索 CLI (typer)，通过 mcporter 调用 xiaohongshu-mcp 服务
- `xhs-tryon/fashn_tryon/` — 虚拟试穿 CLI (argparse)，调用 FASHN API
- [`xiaohongshu-mcp`](https://github.com/xpzouying/xiaohongshu-mcp) — Go 服务，Docker 或原生二进制运行在 localhost:18060
- `config/mcporter.json` — mcporter 服务端点配置（指向 localhost:18060）
- `.claude/skills/xhs-fashion-search/` — Claude Code / OpenClaw skill 定义

## 故障排查

| 问题 | 解决 |
|------|------|
| 搜索报 `service_unavailable` | 检查 launchd 服务：`launchctl list \| grep xiaohongshu`，若无则重新 load plist |
| 端口 18060 被占用 | `lsof -i :18060` 查看占用进程 |
| 搜索返回 `requires_login` | 运行 `$VENV/bin/xhs login start --wait --json` 重新扫码登录 |
| `mcporter` 找不到 | `npm install -g mcporter` |
| 虚拟试穿报 `FASHN_API_KEY is not set` | 确保 `.env` 文件存在且包含 key，运行前 `source .env` |
