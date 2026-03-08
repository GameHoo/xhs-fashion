---
name: xhs-fashion-search
description: AI 辅助生成小红书穿搭搜索关键词。当用户提到穿搭、不知道穿什么、想搜穿搭灵感、找穿搭参考、搜索穿搭、outfit ideas、想看小红书穿搭、或者任何关于"穿什么"的问题时，使用此 skill。即使用户没有明确说"搜索"，只要意图是找穿搭方案或穿搭灵感，都应该触发。
---

# 小红书穿搭搜索 — AI 辅助生成搜索关键词

当用户「不知道穿什么」时，通过几个简短问题收集需求，自动拼出小红书搜索关键词（如 `小个子 春季 通勤 显瘦 穿搭`），然后用 `xhs` CLI 搜索小红书并下载图片，拼成带编号的预览图给用户挑选，最后用 `fashn-tryon` CLI 生成虚拟试穿效果。

## 路径约定

以下变量在本 skill 中反复使用，统一定义：

| 变量 | 值 | 说明 |
|------|---|------|
| `<skill-dir>` | 本 SKILL.md 所在目录（如果是符号链接，取真实路径） | 包含 `scripts/`、`.venv/` |
| `<venv>` | `<skill-dir>/.venv` | Python 虚拟环境（由初始化脚本自动创建） |
| `<project-root>` | 若 `<skill-dir>` 往上三级目录存在 `pyproject.toml`，则取该目录；否则留空 | 开发模式项目根目录 |
| `<env-file>` | 开发模式用 `<project-root>/.env`；独立安装用 `<skill-dir>/.env` | `FASHN_API_KEY` 配置文件 |

**路径解析**：skill 可能通过符号链接加载（如 OpenClaw 从 `~/.openclaw/skills/` 加载），必须先解析真实路径：

```bash
SKILL_DIR=$(cd "$(dirname "$(readlink -f "<SKILL.md 的路径>")")" && pwd)
PROJECT_ROOT_CANDIDATE=$(cd "$SKILL_DIR/../../.." 2>/dev/null && pwd)
if [[ -f "$PROJECT_ROOT_CANDIDATE/pyproject.toml" ]]; then
  PROJECT_ROOT="$PROJECT_ROOT_CANDIDATE"
  ENV_FILE="$PROJECT_ROOT/.env"
else
  PROJECT_ROOT=""
  ENV_FILE="$SKILL_DIR/.env"
fi
```

CLI 可执行文件：
- `<venv>/bin/xhs` — 小红书搜索
- `<venv>/bin/fashn-tryon` — 虚拟试穿
- `<venv>/bin/python3` — 运行脚本

## 环境自动初始化

**在调用任何 CLI 命令之前**，必须先运行初始化脚本确保环境就绪：

```bash
VENV=$(<skill-dir>/scripts/ensure_env.sh)
```

脚本会：
1. 检查 `<skill-dir>/.venv` 是否已存在且完整 — 如果是，秒返回 venv 路径
2. 如果不存在，自动用 `uv` 创建 Python 3.11+ 虚拟环境并安装所有依赖
3. 最后一行输出 venv 路径，可直接赋值给变量使用

后续命令用 `$VENV/bin/xhs`、`$VENV/bin/fashn-tryon`、`$VENV/bin/python3` 调用即可。

### 环境就绪后：自动完成登录

初始化完成后，**必须主动检查小红书登录状态**，不要让用户自己去跑命令：

1. 运行 `$VENV/bin/xhs login status --json` 检查登录态
2. 如果返回 `logged_out`，直接运行 `$VENV/bin/xhs login start --wait --json` 生成二维码
3. 将二维码图片（返回 JSON 的 `qr_image_path` 字段）展示给用户，提示「用小红书 App 扫一下就行」
   - Claude Code：用 `open` 命令打开二维码图片
   - OpenClaw：用 `message` + `media` 发送二维码图片
4. 命令带 `--wait` 会阻塞等待扫码完成，成功后继续后续流程

> **重要**：永远不要把 CLI 命令甩给用户让他们自己执行。所有命令都应该由 agent 自己运行，用户只需要做「扫码」这一个动作。

## 运行环境适配

本 skill 同时支持 **Claude Code（终端）** 和 **OpenClaw（聊天平台）** 两种环境。OpenClaw 可通过 Telegram、微信等多种渠道与用户交互。

| 能力 | Claude Code | OpenClaw |
|------|------------|----------|
| 展示图片 | 用 `Read` 工具读取图片文件 | 用 `message` 工具发送，加 `media` 字段指向本地文件路径 |
| 接收用户照片 | 用户给出文件路径 | 用户直接在聊天中发照片，系统保存到 `~/.openclaw/media/inbound/`，消息中以 `[media attached: /path/to/file.jpg ...]` 格式给出路径 |
| 运行命令 | 用 `Bash` 工具 | 用 `exec` 工具 |

**环境检测方法**：如果可用工具列表中有 `message` 和 `exec` 工具，说明是 OpenClaw 环境；如果有 `Bash` 和 `Read` 工具，说明是 Claude Code 环境。

## 流程

### 第 1 步：判断意图清晰度

根据用户消息分两条路：

- **需求明确** — 用户已经给出具体搜索方向（如「帮我搜春季碎花裙」「搜索 2026 冬季穿搭」）
  → 先确认：「直接帮你搜「XXX」？还是想让我帮你做更精准的智能推荐？」
  - 用户说直接搜 → 跳到第 3 步
  - 用户想要推荐 → 进入第 2 步

- **需求模糊** — 用户说「帮我搜适合我的穿搭」「不知道穿什么」等
  → 回复：「没问题～我问你几个小问题，帮你找到最适合的穿搭」，进入第 2 步

### 第 2 步：引导提问（仅需求模糊时）

逐个问以下问题，每个问题给出选项降低回答门槛。用户回答后继续下一个问题，不要一次全问完。

**Q1 — 性别**
你想看男装还是女装？
- 都看看 / 女装 / 男装

**Q2 — 场景**
穿去哪？干什么？
- 都看看 / 通勤上班 / 约会 / 面试 / 旅行出游 / 朋友聚会 / 日常休闲 / 婚礼宴会 / 运动户外……

**Q3 — 身材关键词**
简单描述一下自己的身材就行，比如：「160 微胖」「170 偏瘦」「小个子梨形」「宽肩溜肩」都可以，怎么说都行～

**Q4 — 想要的效果**
最想达到什么效果？
- 都看看 / 显瘦 / 显白 / 显高 / 百搭 / 遮肉 / 有气场 / 减龄……

**Q5 — MBTI（可选）**
你的 MBTI 是什么？（不知道可以跳过）
- 直接输入如「INFP」「ESTJ」
- 不知道 / 跳过

MBTI 不直接拼入搜索词，但用于辅助下一步的风格推荐排序（如 INTJ 偏向极简黑白灰，ENFP 偏向明亮活泼）。

**Q6 — 风格偏好（AI 智能推荐）**
根据前面所有回答（场景 + 身材 + 效果 + MBTI），挑出 3-5 个最匹配的风格推荐给用户。把最推荐的排在前面，并加一句「为什么适合你」的理由。每个风格附带一句话描述。

**不局限于下方风格库**——可以根据当下流行趋势、用户具体需求自由推荐任何风格，只要能在小红书上搜到相关穿搭内容即可。风格库仅作参考：

| 风格 | 一句话描述 |
|------|-----------|
| 韩系温柔 | 柔和配色、修身剪裁，干净清爽，约会感满分 |
| 简约通勤 | 干净利落基础款，不出错的上班日常，黑白灰驼为主 |
| 法式慵懒 | 不费力的优雅，碎花裙、针织衫、贝雷帽，随性有女人味 |
| 新中式 | 盘扣、立领、水墨印花，古典又现代 |
| 老钱风 | 低调高级感，polo 衫、西裤、大衣，看起来从小有钱 |
| 美拉德风 | 棕色系叠穿，咖啡焦糖奶茶色，秋冬氛围感拉满 |
| 格雷系 | 高级灰调为主，冷静克制又高级 |
| 多巴胺/彩色系 | 大胆撞色、高饱和度，穿上心情就好 |
| 辣妹风 | 短上衣、紧身裤、大胆露肤，性感有活力 |
| 美式休闲 | 卫衣、牛仔裤、棒球帽，舒适随性街头感 |
| 日系文艺 | 棉麻大地色、叠穿，安静温柔文青氛围 |
| 运动休闲 | 运动单品混搭日常，松弛又时髦 |
| 暗黑酷帅 | 全黑皮革机车靴，冷酷有态度 |
| 甜酷混搭 | 甜美 + 帅气，蝴蝶结配皮衣，反差感拉满 |
| 学院风 | 衬衫、针织背心、百褶裙，乖巧书卷气 |
| 极简北欧 | 黑白灰+大廓形，冷淡高级，不挑身材 |
| 复古港风 | 高腰阔腿裤、大垫肩、红唇，浓郁年代感 |
| 田园碎花 | 碎花长裙、草编包、宽檐帽，春夏郊游感 |
| 工装机能 | 多口袋、尼龙面料、束脚裤，硬朗实用 |
| 盐系少年 | 宽松基础款、低饱和色、干净清爽，不费力的帅 |
| 千金小姐 | 珍珠、蕾丝、A 字裙，精致优雅不张扬 |
| 松弛感 | 大一号衬衫、宽裤、拖鞋，慵懒但时髦 |
| 职场精英 | 西装套装、结构感单品，气场全开 |
| 街头嘻哈 | oversize、棒球帽、球鞋，自由不羁 |
| Y2K 千禧 | 低腰裤、金属感、亮片、蝴蝶元素，未来复古 |
| Blokette | 芭蕾舞元素+运动单品混搭，蝴蝶结配球鞋 |
| Cottagecore 田园核 | 碎花、蕾丝、泡泡袖，乡村浪漫感 |
| Gorpcore 山系户外 | 冲锋衣、徒步鞋混搭日常，实用美学 |
| Clean Fit 干净fit | 基础款极致搭配，重剪裁不重装饰 |
| Quiet Luxury 静奢 | 极简无 logo，看不出品牌但质感拉满 |
| Mob Wife 黑帮夫人 | 皮草、豹纹、大金链，张扬富贵 |
| Coquette 蝴蝶结甜心 | 蝴蝶结、蕾丝、粉色系，极致少女 |
| Normcore | 反时尚，普通到极致就是时髦 |

用户也可以自己说一个风格关键词。

推荐示例：
> 根据你的情况（小个子 + 通勤 + 显瘦），推荐这几个风格：
> 1. **韩系温柔** — 修身高腰线设计特别适合小个子拉长比例，通勤也不会太夸张
> 2. **简约通勤** — 基础款剪裁利落显瘦，上班直接穿不用纠结
> 3. **法式慵懒** — 碎花裙、针织衫，不费力的优雅感
> 4. **老钱风** — 低调的高级感，polo 衫、西裤、大衣
>
> 你喜欢哪个方向？也可以告诉我其他你想试的风格～

**季节自动推断**：根据当前日期自动填入，不需要问用户。
- 1-2 月 → 冬季
- 3-4 月 → 早春/春季
- 5-6 月 → 初夏/夏季
- 7-8 月 → 夏季
- 9-10 月 → 早秋/秋季
- 11-12 月 → 秋冬/冬季

### 第 3 步：拼接搜索词

将收集到的信息按以下公式组合：

```
搜索词 = 性别词 + 身材词 + 季节词 + 场景词 + 功能词 + 风格词 + "穿搭"
```

规则：
- 用户选「都看看」的维度不拼入搜索词
- MBTI 不拼入搜索词（仅用于风格推荐排序）
- 每个维度取最核心的 1-2 个词，避免搜索词过长
- 季节词根据当前日期自动填入

示例输出：`女生 小个子 早春 通勤 显瘦 韩系 穿搭`

拼接完成后，告诉用户即将搜索的关键词，然后进入第 4 步搜索。

### 第 4 步：搜索小红书 & 展示结果

#### 4.0 前置检查：小红书登录态

在搜索之前，先检查登录状态：

```bash
<venv>/bin/xhs login status --json
```

根据返回的 `status` 字段处理：

| status | 处理 |
|--------|------|
| `logged_in` | 正常继续搜索 |
| `logged_out` | 运行 `<venv>/bin/xhs login start --wait --json`，提示用户：「需要先登录小红书，我帮你生成了二维码，用小红书 App 扫一下就行～」。Claude Code 环境可用 `Read` 工具展示 QR 图片（路径在返回 JSON 的 `qr_image_path` 字段）；OpenClaw 环境用 `message` + `media` 发送 QR 图片。登录成功后继续搜索。 |
| `pending_login` | 说明之前生成过二维码还没过期，展示 QR 图片（同上），等待用户扫码 |

#### 4.1 搜索并下载图片

用 `xhs` CLI 搜索小红书，下载封面图：

```bash
<venv>/bin/xhs search images \
  --keyword "<第 3 步拼好的搜索词>" \
  --image-dir /tmp/xhs-search \
  --page-size 12 \
  --image-mode cover \
  --login-policy wait \
  --json
```

参数说明：
- `--page-size 12`：一次拉 12 张，不做预过滤，拉完直接展示
- `--image-mode cover`：只下载封面图，速度快，预览够用
- `--login-policy wait`：未登录时自动弹出二维码等用户扫码
- `--json`：输出结构化 JSON，方便解析

#### 4.2 解析搜索结果

从返回的 JSON 中提取关键字段：
- `status`：检查是否成功（`ok` 或 `partial`）
- `download_dir`：图片实际下载目录
- `items[]`：每个搜索结果，包含：
  - `feed_id`：帖子 ID，用于构造链接 `https://www.xiaohongshu.com/explore/<feed_id>`
  - `title`：帖子标题
  - `image_paths`：下载好的图片本地路径列表

如果 `status` 是 `requires_login`，提示用户扫码登录。

#### 4.3 生成带编号的拼图

用 skill 自带的拼图脚本，把 12 张封面图拼成 3 张 2x2 拼图（每张 4 图），每张图片右上角标注编号 1~12：

```bash
<venv>/bin/python3 <skill-dir>/scripts/make_collage.py \
  --images img1.jpg img2.jpg img3.jpg img4.jpg \
  --start-number 1 \
  --output /tmp/xhs_collage_1.jpg
```

对编号 1~4、5~8、9~12 分别生成 3 张拼图。

#### 4.4 展示给用户

根据当前运行环境选择展示方式：

**Claude Code 环境**：用 `Bash` 工具执行 `open` 命令打开拼图，让用户在系统图片查看器中预览：

```bash
open /tmp/xhs_collage_1.jpg /tmp/xhs_collage_2.jpg /tmp/xhs_collage_3.jpg
```

**OpenClaw 环境**：用 `message` 工具发送图片，每张拼图一条消息：

```json
{
  "action": "send",
  "target": "<当前对话的 chat ID>",
  "message": "帮你搜到了这些穿搭参考（编号 1~4）\n喜欢哪几套？告诉我编号就行",
  "media": { "filePath": "/tmp/xhs_collage_1.jpg" }
}
```

对编号 5~8 和 9~12 的拼图分别再发一条。最后一条附加提示：

> 都不喜欢的话可以说「下一批」，我再帮你找！

#### 4.5 用户反馈处理

| 用户反馈 | 处理方式 |
|---------|---------|
| 选择编号（如「3、7」） | 进入第 5 步试穿流程 |
| 「下一批」「再看看」 | 用相同关键词加 `--page N` 翻页，重新拉取（重复 4.1~4.2），拼图展示 |
| 「换个风格」「重新搜」 | 回到第 2 步或第 3 步，调整关键词后重新搜索 |


### 第 5 步：虚拟试穿

#### 5.0 前置检查：FASHN API Key

在进入试穿流程前，检查 `<env-file>` 是否存在且包含 `FASHN_API_KEY`：

```bash
grep -q 'FASHN_API_KEY=fa-' "<env-file>" 2>/dev/null && echo "ok" || echo "missing"
```

如果缺失，提示用户：

> 虚拟试穿需要 FASHN API Key 才能用。
> 去 https://fashn.ai 注册一个账号就能拿到，免费额度够用的。
> 拿到之后把 Key 发给我就行（格式是 `fa-` 开头的一串）

用户提供 Key 后，写入 `<env-file>`：

```bash
echo 'export FASHN_API_KEY=<用户提供的key>' > "<env-file>"
```

如果 `.env` 已有其他内容，用追加模式或编辑工具更新，不要覆盖其他配置。

#### 5.1 获取用户照片

如果用户在本次对话中还没有提供过自己的照片，先问一下：

**Claude Code 环境**：
> 想帮你试穿看看效果！发一下你的照片路径就行（全身照效果最好）

**OpenClaw 环境**：
> 想帮你试穿看看效果！直接发一张你的照片就行（全身照效果最好）

OpenClaw 环境下，用户发送照片后，系统会将图片保存到 `~/.openclaw/media/inbound/` 目录，并在消息中以 `[media attached: /path/to/file.jpg ...]` 格式给出路径。从该消息中提取路径即可。

用户提供照片后，在本次会话内记住路径，后续试穿不需要重复提供。

#### 5.2 判断图片类型并处理

用 `Read` 工具查看用户选中的每张穿搭图，判断类型并分别处理：

| 图片类型 | 判断依据 | 处理方式 |
|---------|---------|---------|
| **单人穿搭照** | 一张图只展示一个人、一套搭配 | 直接进入 5.3 试穿 |
| **规则拼接图** | 2x2 / 3x3 / 2x3 / 1xN 等网格排列的多套穿搭 | 用 `split_collage.py` 切割后逐片试穿，再拼回来（见下方） |
| **不适合试穿** | 不规则拼接、多人合照、纯文字、产品平铺图等 | 跳过，在展示结果时顺带说明「#N 这张不太适合试穿，跳过了」 |

**拼接图处理流程**：

1. 判断网格布局（如 2x2、3x3），调用切割脚本：

```bash
<venv>/bin/python3 <skill-dir>/scripts/split_collage.py split \
  --image <选中的穿搭图路径> \
  --grid 2x2 \
  --output-dir /tmp/xhs-splits
```

参数说明：
- `--grid`：由模型判断后指定（如 `2x2`、`3x3`）；也可省略让脚本自动检测

2. 检查返回的 JSON：
   - `status: "ok"` → 对每个切片调用 `fashn-tryon`（见 5.3）
   - `status: "not_a_collage"` → 按单张图处理

3. 所有切片试穿完成后，将试穿结果按原网格拼回来：

```bash
<venv>/bin/python3 <skill-dir>/scripts/split_collage.py reassemble \
  --pieces result_r1c1.png result_r1c2.png result_r2c1.png result_r2c2.png \
  --grid 2x2 \
  --output /tmp/xhs-tryon/reassembled_<编号>.jpg
```

拼回来的图展示给用户，效果是「同一张拼接图，但每套穿搭都换成了用户自己」。

#### 5.3 调用虚拟试穿

根据 5.2 的判断，对每张需要试穿的图片（单张照片或拼接图的切片）调用 `fashn-tryon` CLI。

`<env-file>` 中包含 `FASHN_API_KEY`，必须在调用前 source 加载。

```bash
source "<env-file>" && <venv>/bin/fashn-tryon run \
  --user-image <用户照片路径> \
  --model-image <穿搭图路径> \
  --output-dir /tmp/xhs-tryon \
  --category auto \
  --garment-photo-type model \
  --mode balanced \
  --json
```

参数说明：
- `--user-image`：用户自己的照片
- `--model-image`：可重复多次，每个是一张穿搭图（单张照片或切片）
- `--garment-photo-type model`：因为穿搭图是真人穿着的（不是平铺图）
- `--category auto`：自动识别上装/下装/连体
- `--mode balanced`：速度和质量的平衡

#### 5.4 展示试穿结果

从 `fashn-tryon` 的 JSON 输出中，读取每个 job 的结果图片路径（在 `results/<job_id>/` 下）。

- **单张照片的试穿结果**：直接展示
- **拼接图的试穿结果**：展示 5.2 中 `reassemble` 拼回来的图

**Claude Code 环境**：用 `Bash` 工具执行 `open` 命令打开试穿结果图，让用户在系统图片查看器中预览：

```bash
open /tmp/xhs-tryon/tryon_.../results/<job_id>/output_001.png
```

**OpenClaw 环境**：用 `message` 工具发送图片，每张试穿结果一条消息：

```json
{
  "action": "send",
  "target": "<chat ID>",
  "message": "#3 试穿效果\n查看原帖：https://www.xiaohongshu.com/explore/<feed_id>",
  "media": { "filePath": "/tmp/xhs-tryon/tryon_.../results/<job_id>/output_001.png" }
}
```

最后附提示：
> 觉得哪套最好看？想换其他的也可以告诉我

#### 5.5 后续交互

用户看完试穿效果后可以：
- 「再选几套试试」 → 回到第 4 步的搜索结果，重新选编号
- 「换个风格重新搜」 → 回到第 2/3 步
- 「下一批」 → 翻页搜索新结果
- 结束对话

## 注意事项

- 语气亲切自然，像朋友聊天一样，不要太正式
- 问题逐个问，不要一次列出所有问题轰炸用户
- 用户随时可以跳过问题或直接给出搜索词
- 如果用户中途改主意，灵活调整，不要死板地走流程

### OpenClaw 环境消息顺序

在 OpenClaw 环境中，assistant 的 `text` 输出和 `message` 工具调用走不同的投递路径，可能导致消息乱序（比如拼图先到、状态文字后到）。

**规则：当后续需要通过 `message` 工具发送图片/媒体时，状态文字也必须通过 `message` 工具发送，不要用纯文本输出。**

错误示例（会乱序）：
```
# 文本输出 — 走 block reply 管道
"筛完了，帮你拼图看看～"
# 然后调用 message 工具发图 — 走 message 工具管道
message({ message: "编号 1~4", filePath: "collage_1.jpg" })
```

正确示例（顺序保证）：
```
# 先用 message 工具发状态文字
message({ message: "筛完了，帮你拼图看看～" })
# 再用 message 工具发图
message({ message: "编号 1~4", filePath: "collage_1.jpg" })
```

简单来说：**OpenClaw 环境下，如果同一轮要发多条消息，全部用 `message` 工具按顺序发，不要混用文本输出和 `message` 工具。**
