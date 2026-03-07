# OpenClaw 安装指南

在 OpenClaw 上安装「小红书穿搭搜索 + 虚拟试穿」skill。

## 前置依赖

安装前确保系统上有以下工具：

| 工具 | 用途 | 安装方式 |
|------|------|---------|
| Python >= 3.11 | 运行 CLI | `brew install python@3.11` |
| [uv](https://docs.astral.sh/uv/) | 自动创建 venv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js >= 18 | 签名脚本 | `brew install node` |
| mcporter | MCP 服务管理 | `npm install -g mcporter` |

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/GameHoo/xhs-fashion.git ~/xhs-fashion
cd ~/xhs-fashion
```

### 2. 链接 Skill 到 OpenClaw

```bash
mkdir -p ~/.openclaw/skills
ln -sf ~/xhs-fashion/.claude/skills/xhs-fashion-search ~/.openclaw/skills/xhs-fashion-search
```

新会话自动生效，无需重启 OpenClaw。

### 3. 初始化环境

Skill 首次触发时会自动调用 `ensure_env.sh` 创建 venv 并安装依赖，也可以手动提前执行：

```bash
~/xhs-fashion/.claude/skills/xhs-fashion-search/scripts/ensure_env.sh
```

### 4. 启动 MCP 服务

```bash
cd ~/xhs-fashion && mcporter start
```

mcporter 根据 `config/mcporter.json` 在 `localhost:18060` 启动 xiaohongshu-mcp 服务。

### 5. 配置 FASHN API Key（虚拟试穿用）

如果你已经有 FASHN API Key，直接写入 `.env`：

```bash
echo 'export FASHN_API_KEY=你的key' > ~/xhs-fashion/.env
```

如果还没有，去 https://fashn.ai 注册账号获取（免费额度够用）。

也可以跳过这步 — skill 在需要试穿时会自动提示你提供 key。

### 6. 小红书登录

不需要手动操作。用户首次使用穿搭搜索时，skill 会自动生成二维码并发送给用户扫码登录。

## 验证安装

在 OpenClaw 聊天中发送以下任意一条消息，skill 应被触发：

- 「不知道穿什么」
- 「帮我搜穿搭」
- 「找点穿搭灵感」

## 故障排查

| 问题 | 解决 |
|------|------|
| skill 没有触发 | 检查符号链接：`ls -la ~/.openclaw/skills/xhs-fashion-search/SKILL.md` |
| ensure_env.sh 报错 `uv not found` | 安装 uv：`curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| 搜索返回 `requires_login` | 发送「重新登录小红书」让 skill 重新生成二维码 |
| mcporter 启动失败 | 检查端口 18060 是否被占用：`lsof -i :18060` |
| 试穿报错 `FASHN_API_KEY is not set` | 确认 `~/xhs-fashion/.env` 文件存在且包含 key |
