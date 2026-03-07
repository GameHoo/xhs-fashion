# xhs-fashion

小红书穿搭搜索 + AI 虚拟试穿。通过 AI Skill 引导用户找到合适的穿搭，搜索小红书获取灵感图，再用 FASHN 虚拟试穿看效果。支持 Claude Code 和 OpenClaw。

## 前置依赖

- **Python >= 3.11**
- **Node.js >= 18** — xhs-tryon 签名脚本需要
- **mcporter** — MCP 服务管理器，用于启动和调用 xiaohongshu-mcp 服务

```bash
# 安装 mcporter（如果还没有）
npm install -g mcporter
```

## 安装

```bash
# 1. 创建虚拟环境
#    iCloud 路径含空格会破坏 venv shebang，所以物理目录放在 ~/.xhs-fashion/venv，
#    项目里通过 .venv 符号链接引用。
python3.11 -m venv ~/.xhs-fashion/venv
ln -sf ~/.xhs-fashion/venv .venv

# 2. 安装两个包：xhs（搜索）+ fashn-tryon（虚拟试穿）
.venv/bin/pip install . ./xhs-tryon/

# 3. 安装 Playwright 浏览器（xhs-tryon 依赖）
.venv/bin/playwright install chromium

# 4. 配置 FASHN API Key（虚拟试穿需要）
cp .env.example .env
# 编辑 .env，填入你的 key：
#   export FASHN_API_KEY=fa-xxxxxxxxxxxx

# 5. 启动 xiaohongshu-mcp 服务
#    mcporter 会根据 config/mcporter.json 在 localhost:18060 启动服务。
mcporter start

# 6. 登录小红书（首次使用前需要扫码登录）
.venv/bin/xhs login start --wait --json
# 终端会显示二维码，用小红书 App 扫码即可
```

## CLI 命令

```bash
# 搜索小红书穿搭
.venv/bin/xhs search images --keyword "男生 早春 简约 穿搭" --image-dir /tmp/xhs-search --json

# 虚拟试穿（需要先 source .env）
source .env && .venv/bin/fashn-tryon run \
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

**Playwright 报错**：运行 `.venv/bin/playwright install chromium` 安装浏览器。
