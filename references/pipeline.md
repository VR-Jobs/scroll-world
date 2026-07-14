# Scroll World v5 操作手册（豆包 / Higgsfield）

本流水线为每个网站创建独立的 `SITE_ROOT`，以其中的 `world.json` 为单一事实源，以
`SITE_ROOT/.work/usage-ledger.json` 保存原子预算和实际用量。
脚本兼容 POSIX shell/macOS bash 3.2；编排、验证和构建只依赖 Python 标准库及
`curl/jq/ffmpeg/ffprobe`；Higgsfield 路线另需官方 `higgsfield` CLI。

## 1. 首次供应商配置、选择模式并初始化

先运行 `python3 "$SW/scripts/scroll-world.py" setup --status`。未配置时只问一次供应商：

- 豆包：`python3 "$SW/scripts/provider_config.py" configure --provider doubao`，在隐藏终端输入中
  粘贴一次 API Key。它保存到用户配置目录的权限 `0600` 文件，不进入项目。
- Higgsfield：官方 CLI 不粘贴 API Key；安装后运行一次 `higgsfield auth login`，再执行
  `python3 "$SW/scripts/provider_config.py" configure --provider higgsfield`。详见 `higgsfield.md`。

配置存在时直接复用，不再询问。只有用户明确切换时使用 `--replace`；现有项目仍锁定原 provider。

用户给出网站需求后，先让用户二选一，未选择前不创建项目或调用生成 API：

| 模式 | 中间审批 | 视频链 | 适用情况 |
|---|---|---|---|
| `detailed` 详细版本（推荐） | 首图、整批图片、mini 预演、逐场语义 QA | 720p mini → 独立 1080p final | 正式品牌站、复杂视觉、返工昂贵 |
| `fast` 快速版本 | 仅方案批准；生成中不暂停，最后统一验收 | 跳过 mini，直接 1080p final | 快速原型、方向已明确、减少请求与往返 |

快速版仍保留预算锁、指纹缓存、H.264 编码、SSIM、浏览器与可移植验证。当前模型注册表没有
`1024p` 视频模式；快速版用一次直出受支持的 1080p 正式视频实现“一步到位”，不得伪造参数。

```bash
SW=/Users/zhuxianliu/.codex/skills/scroll-world
WORKSPACE_ROOT=/absolute/path/to/workspace
PROVIDER=$(python3 "$SW/scripts/provider_config.py" show-provider)
MODE=detailed # 或 fast，来自用户选择
python3 "$SW/scripts/init-project.py" --workspace-root "$WORKSPACE_ROOT" \
  --name "MANIFEST" --slug "manifest" --provider "$PROVIDER" --mode "$MODE" --theme low-poly-clay
SITE_ROOT="$WORKSPACE_ROOT/manifest"
WORLD="$SITE_ROOT/world.json"
touch "$SITE_ROOT/.env.local"
chmod 600 "$SITE_ROOT/.env.local"
```

新网站始终使用新 slug；目录冲突时停止，不得复用另一个网站。只有继续同一网站并核对
`.scroll-world-project.json` 后才能给初始化器加 `--resume`。所有 HTML、CSS、JS、素材、prompt、
项目专用脚本、`.work`、`dist`、QA 和成本报告必须位于 `SITE_ROOT`；工作区根目录不得出现这些
共享目录或散落文件。下文始终用绝对的 `WORLD`/`SITE_ROOT` 路径执行。

豆包旧项目仍可用 `.env.local` 覆盖用户级密钥/账户 Endpoint；新项目不需要重复粘贴：

```bash
ARK_API_KEY=在火山方舟控制台创建并已轮换的密钥
# 可选：账户 Endpoint 或新时间版本覆盖
# ARK_IMAGE_MODEL=ep-...
# ARK_VIDEO_PREVIEW_MODEL=ep-...
# ARK_VIDEO_FINAL_MODEL=ep-...
```

```bash
set -a
. "$SITE_ROOT/.env.local"
set +a
: "${ARK_API_KEY:?ARK_API_KEY 未配置}"
```

模型默认值集中在 `references/models.json` 的 provider 分区；能力摘要见
`doubao-capabilities.md` / `higgsfield.md`。

## 2. world.json

必须填写：

- `project.brand/provider/architecture/generation_limit/workflow_mode/theme` 与 `design`；
- `models` 三个别名；
- `pricing` 的官方 URL、抓取时间、币种和当时单价；
- `generation.ratio/duration_seconds/preview_enabled`；
- 每场 prompt、参考图、所有输出路径、语义正负约束；
- 架构 B 的 N-1 个 transitions；
- `delivery.public_files/initial_files/budgets` 与 `delivery.portable`。

价格字段为 `null` 时允许继续，但最终报告会把对应金额标为不可得。不要在技能内固化会变化
的价格。生成前运行：

```bash
python3 "$SW/scripts/sw_tool.py" validate --world "$WORLD"
python3 "$SW/scripts/scroll-world.py" doctor --world "$WORLD"
python3 "$SW/scripts/scroll-world.py" plan --world "$WORLD"
```

`plan` 会输出供应商和模式，是预算批准依据；`retry_reserve` 不够时先减少场景/预演，不靠超限后解释。
5 场架构 A 的详细版为 15 次（5 图 + 5 mini + 5 final），快速版为 10 次（5 图 + 5 final）。

## 3. 双模式编排与门禁

```bash
python3 "$SW/scripts/scroll-world.py" run --world "$WORLD" --dry-run
python3 "$SW/scripts/scroll-world.py" run --world "$WORLD" --resume
```

详细版状态机按以下顺序推进：

1. 只生成首场锚点图；展示给用户确认风格、配色、材质、构图与产品准确性，等待 `approve anchor`。
2. 生成剩余场景图；均引用锚点和可选产品图。
3. 展示全部场景图及 section id，等待 `approve images`。用户点名不合格图时仅 retry 该 still，
   重生后重新展示整批；未批准整批前不提交视频。
4. preview 视频链；任务异步时返回，稍后 `--resume`；整链完成后等待 `approve preview`。
5. final 视频链；使用独立正式链，绝不接 mini 尾帧。
6. 编码/海报/SSIM；任一 SSIM <0.75 阻止继续。
7. 联系表与语义 QA；逐场 pass 后才能生产构建。
8. 双击/云端同包验证、白名单构建与成本报告。

快速版状态机按以下顺序推进：

1. 生成首场锚点后不暂停，以锚点为风格参考连续生成全部场景图。
2. 跳过 `preview`，直接从图片建立独立的 1080p `final` 链。
3. 编码/海报/SSIM、联系表、浏览器与可移植验证自动执行。
4. 交付完整网站和联系表供用户一次性验收；用户点名场景后 retry 会自动归档对应 still 和依赖后链。
5. 输出带 `workflow_mode=fast` 的成本报告。快速版不接受任何 `approve` 命令。

```bash
python3 "$SW/scripts/scroll-world.py" approve anchor --world "$WORLD" --note "批准原因"
python3 "$SW/scripts/scroll-world.py" approve images --world "$WORLD" --note "全部图片批准"
python3 "$SW/scripts/scroll-world.py" approve preview --world "$WORLD" --note "批准原因"
python3 "$SW/scripts/scroll-world.py" status --world "$WORLD"
```

`run` 返回 10 表示视频 queued/running；详细版返回 20 表示人工门禁。两者都不是失败，也不应重提。

## 4. 原子脚本

编排器之外需要诊断时，可直接调用原子脚本。

### 豆包 Seedream（最多 10 张参考图）

```bash
"$SW/scripts/ark-image.sh" "$SITE_ROOT/prompts/still-hero.txt" "$SITE_ROOT/assets/anchor-hero.jpg" \
  --reference "$SITE_ROOT/assets/product-front.png" \
  --reference "$SITE_ROOT/assets/product-side.png" --label still:hero
```

旧接口的第三个位置参数仍可作为一张锚点参考。脚本指纹包含模型、完整 prompt、参数及每张
参考图内容；输出存在但指纹不符时退出 12。明确重试用编排器 `retry`，或直接 `--force`。

详细版整批检查时逐张展示 `status`/`GATE images` 输出的 section id 与 still 路径。用户例如反馈
“optics 镜片形状不对”时，只改 `prompts/still-optics.txt` 或该场参考图，然后执行：

```bash
python3 "$SW/scripts/scroll-world.py" retry --world "$WORLD" --stage still --id optics
python3 "$SW/scripts/scroll-world.py" run --world "$WORLD" --resume
```

旧图和请求证据会归档，其他通过图片不变；新图完成后再次展示整批并等待 `approve images`。
批准记录绑定图片内容指纹，直接替换文件也会使旧批准失效。
快速版不执行批准命令；在最终统一验收中收到同样反馈时仍使用目标 retry，并重跑依赖该图片的
正式视频与验证。

### 豆包 Seedance 提交（首/尾帧 + reference images）

```bash
"$SW/scripts/ark-video-submit.sh" final "$SITE_ROOT/prompts/video-optics.txt" \
  "$SITE_ROOT/assets/raw-final-hero-last.png" "$SITE_ROOT/.work/video-final-optics" "" 16:9 5 \
  --reference-image "$SITE_ROOT/assets/still-optics.jpg" --label section:final:optics
```

架构 A 中，第一张输入是上一段真实尾帧（连续性），`reference_image` 是当前场景图（语义）。
内容总计最多 9 张图片。connector 的第五个位置参数传实际目标首帧：

```bash
"$SW/scripts/ark-video-submit.sh" final "$SITE_ROOT/prompts/connector-hero-optics.txt" \
  "$SITE_ROOT/assets/raw-final-hero-last.png" "$SITE_ROOT/.work/connector-final-hero-optics" \
  "$SITE_ROOT/.work/first-frames/final-optics.png" 16:9 5 --label transition:final:hero-optics
```

查询不消耗新生成：

```bash
"$SW/scripts/ark-video-poll.sh" "$SITE_ROOT/.work/video-final-optics" \
  "$SITE_ROOT/assets/raw-final-optics.mp4" "$SITE_ROOT/assets/raw-final-optics-last.png"
```

- 0：成功/缓存；10：queued/running；其他：读取状态响应诊断。
- 成功立即下载临时 URL；轮询结果的 `usage.completion_tokens` 写入账本。
- API 失败不会自动删除 task ID；传输结果未知记为 `ambiguous` 并占预算。

### Higgsfield 官方 CLI 适配

编排器会自动调用以下适配器；诊断时可独立执行：

```bash
python3 "$SW/scripts/higgsfield_adapter.py" image \
  "$SITE_ROOT/prompts/still-hero.txt" "$SITE_ROOT/assets/anchor-hero.jpg" \
  --reference "$SITE_ROOT/assets/product-front.png" --label still:hero
python3 "$SW/scripts/higgsfield_adapter.py" video-submit final \
  "$SITE_ROOT/prompts/video-optics.txt" "$SITE_ROOT/assets/raw-final-hero-last.png" \
  "$SITE_ROOT/.work/video-final-optics" "" 16:9 5 \
  --reference-image "$SITE_ROOT/assets/still-optics.jpg" --label section:final:optics
python3 "$SW/scripts/higgsfield_adapter.py" video-poll \
  "$SITE_ROOT/.work/video-final-optics" "$SITE_ROOT/assets/raw-final-optics.mp4" \
  "$SITE_ROOT/assets/raw-final-optics-last.png"
```

默认 still=`nano_banana_2`/2K、preview=`seedance_2_0_mini`/720p、final=
`seedance_2_0`/1080p。CLI 自动上传本地参考图；视频下载后由 ffmpeg 提取真实尾帧。每个项目首笔
请求前运行 `higgsfield model list/get --json` 验证实时参数，不直连私有 API。

## 5. 缓存与重试

```bash
python3 "$SW/scripts/scroll-world.py" retry --world "$WORLD" --stage still --id optics
python3 "$SW/scripts/scroll-world.py" retry --world "$WORLD" --stage final --id optics
```

旧资产/任务移到 `.work/rejected/<timestamp>-.../`；账本记录不删除，因为已接受请求仍是实际
用量。替换一个正式片段后，`media-pipeline.py` 会全链重算 SSIM；语义资产 fingerprint 变化
后旧 pass 自动变 pending。

## 6. 媒体与语义 QA

```bash
python3 "$SW/scripts/media-pipeline.py" --world "$WORLD"
python3 "$SW/scripts/qa-assets.py" prepare --world "$WORLD"
```

详细版打开每场源 still 与 `.work/qa/contact-<id>.jpg`，对照正负约束检查首/中/尾。只有真正
看过资产后记录：

```bash
python3 "$SW/scripts/qa-assets.py" review --world "$WORLD" --section hero \
  --status pass --reviewer codex-vision --notes "主体、材质、配色、运动均通过"
python3 "$SW/scripts/qa-assets.py" check --world "$WORLD"
```

媒体报告位于 `.work/qa/media-report.json`，语义报告位于
`.work/qa/semantic-report.json`。SSIM WARN 必须人工查看短交叉淡化，FAIL 不可用淡化掩盖。
快速版仍生成联系表、媒体报告并阻止 SSIM FAIL，但不在构建前等待逐场语义审批；最终统一验收
必须查看联系表，任何用户指出的语义错误都要目标返工。

## 7. 页面与生产构建

网页海报必须来自编码视频首帧。复制 `scrub-engine.js` 并配置 desktop/mobile clip + poster。
最终目录无条件采用“本地/云端同包”：配置 `directVideo: true`，双击 `index.html` 可运行，整个
目录原样上传到静态服务器也可运行。HTML、CSS、普通 JS 与 `assets/` 保持相对位置；禁止
`/assets/...`、`file://`/localhost/用户目录绝对路径、ES module、Service Worker 和服务端路由。
不要用 `fetch()` 读取本地 MP4，也不要引用生成服务返回的临时 URL。
在桌面、390×844、834×1194 运行 `references/browser-smoke.js`，并分别检查控制台、视觉接缝、
reduced-motion/data-saver/播放拒绝回退。烟雾脚本返回值必须 `pass: true`；它不会捕获运行前的
历史控制台错误，也不能判断产品语义。必须完成 file/http × 三种视口并记录哈希绑定证据：

```bash
python3 "$SW/scripts/browser-qa.py" prepare --world "$WORLD"
python3 "$SW/scripts/browser-qa.py" record --world "$WORLD" --evidence /absolute/browser-evidence.json
python3 "$SW/scripts/browser-qa.py" check --world "$WORLD"
```

浏览器 QA 后执行：

```bash
python3 "$SW/scripts/build-production.py" --world "$WORLD" --dry-run
python3 "$SW/scripts/build-production.py" --world "$WORLD"
python3 "$SW/scripts/verify-portable.py" --root "$SITE_ROOT/dist" --entry index.html \
  --json-output "$SITE_ROOT/dist/portable-report.json"
```

构建只复制 `public_files`，拒绝 `.work`、密钥、prompts、raw/preview，校验本地引用也在白名单
中，并执行 `max_total_mb/max_single_video_mb/max_initial_mb`。发布前验证器还会解析入口和资源
闭包、检查文件存在，并用 ffprobe 确认 MP4 为 H.264/yuv420p。部署或拷贝完整 `output_dir`，
不能只拿走 `index.html`；`portable-report.json` 必须为 `pass`。

## 8. 成本与用量

豆包图片接受响应后记录输出数量与 output tokens；价格快照明确按张时用 `cny_per_output`，明确
按 token 时用 `cny_per_million_output_tokens`，不能从响应里出现 token 就自行推断计费单位。
视频完成后记录 completion tokens。Higgsfield分别记录每次请求的 `credits_actual`、
`credits_estimated` 与来源；缺少 actual 时不能把预估伪装成实际扣费。仅有当前官方
`pricing.higgsfield.cny_per_credit` 快照时换算人民币。Codex 用量只能从宿主/用户
提供的真实遥测登记：

```bash
python3 "$SW/scripts/sw_tool.py" record-codex --ledger "$SITE_ROOT/.work/usage-ledger.json" \
  --billing-mode api --model gpt-5-codex \
  --input-tokens 123456 --cached-input-tokens 100000 --output-tokens 12345 \
  --source codex-host-usage
```

订阅模式用 `--billing-mode subscription`，其单次增量费为 0，但不把月费虚构分摊到对话。
宿主不提供数据时保持 unknown。

```bash
python3 "$SW/scripts/sw_tool.py" report --world "$WORLD" \
  --ledger "$SITE_ROOT/.work/usage-ledger.json" \
  --json-output "$SITE_ROOT/.work/cost-report.json" --markdown-output "$SITE_ROOT/COSTS.md"
```

最终回复逐项采用 `COSTS.md` 数据，并报告 provider 和 detailed/fast 模式。金额缺少实际 token、
credits 换算或价格快照
时必须写不可得和原因。

## 9. 收尾验证

```bash
sh -n "$SW"/scripts/*.sh
python3 -m py_compile "$SW"/scripts/*.py
python3 "$SW/scripts/init-project.py" --workspace-root "$WORKSPACE_ROOT" \
  --name "MANIFEST" --slug "manifest" --provider "$PROVIDER" --mode "$MODE" --resume
python3 "$SW/scripts/sw_tool.py" validate --world "$WORLD"
python3 "$SW/scripts/qa-assets.py" check --world "$WORLD"
python3 "$SW/scripts/build-production.py" --world "$WORLD" --dry-run
python3 "$SW/scripts/verify-portable.py" --root "$SITE_ROOT/dist" --entry index.html
rg -n 'ARK_API_KEY=' -g '!.env.local' -g '!.work/**' "$SITE_ROOT"
rg -n 'HF_API_KEY|HF_API_SECRET|HF_KEY|oauth|access.token' "$SITE_ROOT"
```

密钥扫描只允许空模板。真实失败必须报告；未做浏览器 QA、可移植目录验证或生产预算检查时，
不称完成。详细版还要求语义 QA；快速版要求最终统一验收。
