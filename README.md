# xhs-fashion

小红书穿搭搜索 + AI 虚拟试穿。通过 AI Skill 引导用户找到合适的穿搭，搜索小红书获取灵感图，再用 FASHN 虚拟试穿看效果。支持 Claude Code 和 OpenClaw。

## 前置依赖

- **Python >= 3.11**
- **[uv](https://docs.astral.sh/uv/)** — Python 包管理器，用于自动创建 venv
- **Node.js >= 18** — xhs-tryon 签名脚本需要
- **mcporter** — MCP 服务管理器，用于启动和调用 xiaohongshu-mcp 服务

```bash
# 安装 uv（如果还没有）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装 mcporter（如果还没有）
npm install -g mcporter
```

## 安装

环境初始化由 `ensure_env.sh` 脚本自动完成，会在 `.claude/skills/xhs-fashion-search/.venv` 下创建 venv 并安装所有依赖：

```bash
# 1. 一键初始化环境（创建 venv + 安装依赖）
VENV=$(.claude/skills/xhs-fashion-search/scripts/ensure_env.sh)

# 2. 配置 FASHN API Key（虚拟试穿需要）
echo 'export FASHN_API_KEY=fa-xxxxxxxxxxxx' > .env

# 3. 启动 xiaohongshu-mcp 服务
#    mcporter 会根据 config/mcporter.json 在 localhost:18060 启动服务。
mcporter start

# 4. 登录小红书（首次使用前需要扫码登录）
#    返回 JSON 中 qr_image_path 字段是二维码图片路径。
#    AI agent 应直接运行此命令并将二维码展示给用户，不要让用户自己执行。
$VENV/bin/xhs login start --wait --json
```

> **给 AI agent 的提示**：安装完成后应主动执行登录命令并将二维码图片发送给用户，用户只需要扫码，不需要手动运行任何命令。

## OpenClaw 安装 Skill

详见 [OpenClaw 安装指南](docs/openclaw-setup.md)。

快速安装：

```bash
git clone https://github.com/GameHoo/xhs-fashion.git ~/xhs-fashion
ln -sf ~/xhs-fashion/.claude/skills/xhs-fashion-search ~/.openclaw/skills/xhs-fashion-search
```

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
xhs CLI  ──→  mcporter call  ──→  xiaohongshu-mcp (localhost:18060)
                                         │
                                    小红书 API
```

- `xhs_cli/` — 搜索 CLI，通过 mcporter 调用 xiaohongshu-mcp 服务
- `xhs-tryon/fashn_tryon/` — 虚拟试穿 CLI，调用 FASHN API
- `config/mcporter.json` — mcporter 服务配置
- `.claude/skills/xhs-fashion-search/` — Claude Code / OpenClaw skill 定义

## 故障排查

**mcporter 启动失败**：检查 `config/mcporter.json` 配置是否正确，确保端口 18060 未被占用。

**搜索返回 `requires_login`**：运行 `xhs login start --wait --json` 重新扫码登录。

**虚拟试穿报错 `FASHN_API_KEY is not set`**：确保运行前执行了 `source .env`。

**Playwright 报错**：运行 `$VENV/bin/playwright install chromium` 安装浏览器。
