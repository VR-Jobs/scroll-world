---
name: scroll-world
description: >
  使用火山方舟豆包或 Higgsfield 制作沉浸式滚动驱动的预渲染 3D 世界、low-poly/clay diorama
  品牌落地页和 Apple 式 scroll-scrub cinematic website。支持首次供应商安全配置、详细/快速模式、
  多主题、首图与整批图片审批、连续视频、依赖感知返工、Higgsfield 实时模型预检、媒体/语义/浏览器
  QA、双击与静态服务器同包交付、生成预算及图片/视频/Codex 成本报告。用户提到滚动电影、3D 世界、
  产品穿越页、diorama landing page、scroll-scrub video 或滚动进入下一场景时使用。不要用于真正的
  WebXR/VR 交互、自由旋转的 Three.js 配置器或普通视频剪辑，除非同时要求预渲染滚动电影页面。
---

# Scroll World v5

把滚动进度映射为已编码视频时间。供应商负责一致场景与连续镜头，页面负责稳定 seek、文案、无障碍和回退。
以项目内 `world.json` 为单一事实源，以 `.work/usage-ledger.json` 为生成和成本账本。

## 1. 首次配置

首次调用先运行：

```bash
SW=/Users/zhuxianliu/.codex/skills/scroll-world
python3 "$SW/scripts/scroll-world.py" setup --status
```

未配置时让用户选择一次供应商，不得代选：

- 豆包：`setup --provider doubao`，只在终端隐藏输入 `ARK_API_KEY`；不得要求在聊天中粘贴。
- Higgsfield：先安装官方 CLI、运行 `higgsfield auth login` 并让用户选择 workspace，再执行
  `setup --provider higgsfield`；不得替用户选择计费 workspace。

用户级配置以后复用。只有用户明确切换时使用 `--replace`；现有项目始终按自己的
`project.provider` 运行。密钥、OAuth、Base64、`.work` 和用户配置不得进入项目交付或 `.skill`。
认证与模型细节按所选供应商读取 `references/doubao-capabilities.md` 或 `references/higgsfield.md`。

## 2. 选择模式、主题并创建隔离项目

创建任何文件前让用户选择：

- `detailed`（推荐）：首图、整批图片、720p preview、语义 QA 和浏览器 QA 均设门禁。
- `fast`：跳过图片/preview 中间门禁，直接生成 1080p final；最后仍需媒体、浏览器和统一验收。

运行 `scroll-world.py themes` 展示主题；未指定时使用 `low-poly-clay`。关键品牌文字默认用 DOM/SVG/GLB
可控叠加，不让图片或视频模型承担精确拼写。

每个网站必须新建独立目录：

```bash
PROVIDER=$(python3 "$SW/scripts/provider_config.py" show-provider)
python3 "$SW/scripts/init-project.py" \
  --workspace-root /absolute/workspace --name MANIFEST --slug manifest \
  --provider "$PROVIDER" --mode detailed --theme low-poly-clay
WORLD=/absolute/workspace/manifest/world.json
```

HTML、CSS、JS、素材、prompt、账本、QA、报告和 `dist` 全部留在该目录。不得在工作区根目录共享
`world.json`、`assets/`、`prompts/`、`.work` 或 `dist`。只有恢复同一标记项目时才能使用 `--resume`。

旧 schema 项目先预演再迁移：

```bash
python3 "$SW/scripts/scroll-world.py" migrate --world "$WORLD" --dry-run
python3 "$SW/scripts/scroll-world.py" migrate --world "$WORLD"
```

## 3. 计划与付费前检查

填写场景、模型、设计、价格快照、输出与 QA 约束后运行：

```bash
python3 "$SW/scripts/sw_tool.py" validate --world "$WORLD"
python3 "$SW/scripts/scroll-world.py" doctor --world "$WORLD"
python3 "$SW/scripts/scroll-world.py" plan --world "$WORLD"
```

用 `plan` 的图片、preview、final 和重试预留取得一次方案批准。5 场架构 A：详细版 15 次，快速版
10 次。不得超过 `generation_limit`。Higgsfield 必须在预算预留前通过登录、实时 model catalog/schema
和 `generate cost` 校验；失败时停止，不静默删参数或切换供应商。

价格缺失允许制作，但金额必须标为不可得；不得凭记忆硬编码当前价格。

## 4. 可恢复状态机

```bash
python3 "$SW/scripts/scroll-world.py" run --world "$WORLD" --dry-run
python3 "$SW/scripts/scroll-world.py" run --world "$WORLD" --resume
python3 "$SW/scripts/scroll-world.py" status --world "$WORLD"
```

退出码 10 表示异步视频仍在运行，稍后恢复；20 表示人工门禁。不得因这两个状态重复提交任务。

详细版顺序：

1. 生成首图，展示风格、配色、材质、构图和产品准确性，批准 `anchor`。
2. 生成余图，逐张展示；只重做用户点名图片，批准整批 `images` 后才能进入视频。
3. 生成独立 preview 链，检查旅程和运镜，批准 `preview`。
4. 从批准图片重新建立 final 链；下一段始终使用上一段真实尾帧。
5. 编码、自动视觉/运动报告、SSIM、语义 QA、浏览器 QA、生产构建、成本报告。

门禁命令：

```bash
python3 "$SW/scripts/scroll-world.py" approve anchor --world "$WORLD" --note "风格批准"
python3 "$SW/scripts/scroll-world.py" approve images --world "$WORLD" --note "整批批准"
python3 "$SW/scripts/scroll-world.py" approve preview --world "$WORLD" --note "运镜批准"
```

## 5. 依赖感知返工

返工前先查看影响范围：

```bash
python3 "$SW/scripts/scroll-world.py" retry --world "$WORLD" --stage still --id optics --explain
python3 "$SW/scripts/scroll-world.py" retry --world "$WORLD" --stage still --id optics
```

架构 A 会失效目标场景及所有依赖其真实尾帧的下游视频；架构 B 会失效目标 dive 和相邻 connector。
同时归档对应编码、海报、媒体/语义/浏览器 QA、旧 `dist` 和成本报告；账本中的历史请求不得删除。
只重做依赖资产，不重做无关且已通过的图片。

## 6. 强制质量门禁

`media-pipeline.py` 必须验证 H.264/yuv420p、桌面/移动编码、接缝 SSIM，并报告黑帧、冻结、亮度、
运动能量、首图对齐和接缝运动突变。自动指标只负责发现风险，不替代产品语义检查。

详细版使用 `qa-assets.py` 逐场记录 must_include/must_not_include；资产指纹变化会使旧批准失效。

浏览器 QA 必须覆盖 `file://` 与 HTTP 两种启动方式，以及 desktop/mobile/tablet 三种视口。运行
`references/browser-smoke.js`，同时记录控制台和视觉结果，再用：

```bash
python3 "$SW/scripts/browser-qa.py" prepare --world "$WORLD"
python3 "$SW/scripts/browser-qa.py" record --world "$WORLD" --evidence /absolute/browser-evidence.json
python3 "$SW/scripts/browser-qa.py" check --world "$WORLD"
```

报告与全部公开文件哈希绑定；HTML、视频或海报变化后必须重测。缺少或过期报告时不得构建或宣布完成。

## 7. 可移植交付与成本

最终 `dist/index.html` 必须能直接双击，也能把同一目录原样上传到静态服务器。使用普通脚本、
`directVideo: true` 和目录内相对路径；禁止 localhost、根路径 `/assets`、用户绝对路径、Service
Worker、服务器路由和需要 HTTP CORS 的模块加载。

最终运行：

```bash
python3 "$SW/scripts/build-production.py" --world "$WORLD" --dry-run
python3 "$SW/scripts/build-production.py" --world "$WORLD"
python3 "$SW/scripts/sw_tool.py" report --world "$WORLD" \
  --ledger "$(dirname "$WORLD")/.work/usage-ledger.json" \
  --json-output "$(dirname "$WORLD")/.work/cost-report.json" \
  --markdown-output "$(dirname "$WORLD")/COSTS.md"
```

Higgsfield credits 必须分别报告 actual、estimated 和 source；只有 actual 与带日期换算率齐全时才给
实际金额。Codex token/计费模式未暴露时明确写不可得，不按文字长度猜测。

## 按需读取

- 操作命令、状态码、预算、QA 和交付：`references/pipeline.md`
- 豆包/Higgsfield 认证与能力：`references/doubao-capabilities.md`、`references/higgsfield.md`
- 提示词与主题：`references/prompts.md`、`references/themes.md`
- 自动质量与浏览器证据：`references/quality.md`
- 缓存、失败、迁移与恢复：`references/gotchas.md`
- 页面运行时：`references/scrub-engine.js`、`references/index-template.html`、`references/browser-smoke.js`
