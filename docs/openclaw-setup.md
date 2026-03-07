# OpenClaw 安装指南

在 OpenClaw 上安装「小红书穿搭搜索 + 虚拟试穿」skill。

## 前置依赖

只需 **Node.js >= 18**（`brew install node`）。其余依赖由安装脚本自动安装。

## 安装步骤

### 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/GameHoo/xhs-fashion/main/install.sh | bash
```

脚本会自动完成：
- 下载 skill 文件到 `~/.openclaw/skills/xhs-fashion-search/`
- 创建 Python venv 并安装 CLI 工具（从 GitHub 安装，无需 clone 项目）
- 下载 xiaohongshu-mcp 二进制并启动服务
- 注册 mcporter 服务端点

### 手动安装（开发者）

如果想修改源码，可以 clone 项目后 symlink：

```bash
git clone https://github.com/GameHoo/xhs-fashion.git ~/xhs-fashion
ln -sf ~/xhs-fashion/.claude/skills/xhs-fashion-search ~/.openclaw/skills/xhs-fashion-search
~/xhs-fashion/.claude/skills/xhs-fashion-search/scripts/ensure_env.sh
```

### 配置 FASHN API Key（虚拟试穿用，可选）

```bash
# 一键安装用户
echo 'export FASHN_API_KEY=fa-xxx' >> ~/.openclaw/skills/xhs-fashion-search/.env

# 手动安装用户
echo 'export FASHN_API_KEY=fa-xxx' >> ~/xhs-fashion/.env
```

去 https://fashn.ai 注册账号获取（免费额度够用）。也可以跳过 — skill 在需要试穿时会自动提示。

### 小红书登录

不需要手动操作。用户首次使用穿搭搜索时，skill 会自动生成二维码并发送给用户扫码登录。

## 验证安装

在 OpenClaw 聊天中发送以下任意一条消息，skill 应被触发：

- 「不知道穿什么」
- 「帮我搜穿搭」
- 「找点穿搭灵感」

## 故障排查

| 问题 | 解决 |
|------|------|
| skill 没有触发 | 检查文件存在：`ls ~/.openclaw/skills/xhs-fashion-search/SKILL.md` |
| ensure_env.sh 报错 `uv not found` | 重跑 `install.sh`（会自动安装 uv） |
| 搜索报 `service_unavailable` | 检查服务：`launchctl list \| grep xiaohongshu`，若无则重跑 `ensure_env.sh` |
| 搜索返回 `requires_login` | 发送「重新登录小红书」让 skill 重新生成二维码 |
| 端口 18060 被占用 | `lsof -i :18060` 查看占用进程 |
| `mcporter` 找不到 | 重跑 `install.sh`（会自动安装），或手动 `npm install -g mcporter` |
| 试穿报错 `FASHN_API_KEY is not set` | 确认 `.env` 文件存在且包含 `export FASHN_API_KEY=fa-xxx`（一键安装在 `~/.openclaw/skills/xhs-fashion-search/`，手动安装在项目根目录） |
