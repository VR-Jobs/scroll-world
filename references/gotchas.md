# 故障表：症状 → 原因 → 修复

先读错误与现有状态，再执行修复；不要原样重复失败请求。

先运行 `scroll-world.py doctor --world "$WORLD"`。旧项目提示 schema 时先运行 `migrate --dry-run`，
确认后再迁移；迁移前版本会备份到 `.work/migrations/`。

## 火山方舟与密钥

- **HTTP 401/403** → `ARK_API_KEY` 未加载、已轮换、区域不匹配或模型未开通。检查
  `.env.local` 权限和当前控制台，不要把密钥打印出来。用空值/长度检查，不用 `echo`。
- **模型不存在或不支持参数** → 时间版本的模型 ID 已变化，或把正式模型参数传给 mini。
  对照官方模型列表，更新 `ARK_IMAGE_MODEL` / `ARK_VIDEO_PREVIEW_MODEL` /
  `ARK_VIDEO_FINAL_MODEL`；不要在多个脚本散落硬编码 ID。
- **HTTP 429 / QuotaExceeded** → 排队任务或账户额度达到限制。先查已有任务；降低并发并等待
  队列释放。查询同一任务不产生新生成，不要重新提交造成重复扣费。
- **HTTP 5xx / 网络中断** → 先检查任务提交响应中是否已有 `.id`。有 ID 就继续轮询；只有确认
  没有创建任务时才重提。把请求状态保存在 `.work/<task>/` 以支持恢复。
- **下载 URL 失效** → 成功后没有及时下载。生成 URL 是临时交付，不是网页 CDN；任务成功后
  立即保存视频与尾帧到本地。
- **密钥出现在 Git/聊天** → 立即在控制台轮换；仅删除文件不够。新密钥通过
  `provider_config.py configure --provider doubao --replace` 的隐藏输入保存到用户配置目录；
  权限必须为 `0600`，不得复制回项目。
- **账本显示 ambiguous** → 提交时网络中断，无法确认 API 是否接受。先在方舟控制台按时间/
  request ID 查任务；确认未创建才将该项人工判为 rejected 并重提，不能直接再发一次。
- **生成前就提示预算用尽** → 账本把 reserved/ambiguous/已接受任务都占一个槽，防止并发超限。
  用 `status` 查明状态；不要删账本。只有明确拒绝的预留会自动释放。

## 供应商与 Higgsfield

- **每个新项目又询问供应商/API** → 没先读取用户级配置。运行 `provider_config.py status --json`；
  `configured=true` 时直接复用，不能再次索取凭据。
- **用户选择 Higgsfield 后要求粘贴 API Key** → 官方 Codex/CLI 路线使用 OAuth，不使用 API Key。
  安装官方 CLI 并运行一次 `higgsfield auth login`，不要伪造 key 字段或直连私有接口。
- **`higgsfield` command not found** → 经用户批准安装官方 CLI，再验证 `higgsfield version`；不要
  下载来历不明的同名二进制。
- **`Session expired` / `Not authenticated`** → 保留现有项目 provider 和任务 ID，请用户重新运行
  `higgsfield auth login`。不要自动回退豆包，这会改变成本、模型风格和连续链。
- **`No workspace selected`** → 运行 `higgsfield workspace list`，让用户明确选择后执行
  `higgsfield workspace set <workspace_id>`；workspace 决定账户和计费范围，不能替用户选择。
- **Higgsfield model/flag unknown** → 运行完整 `higgsfield model list --json`，再用
  `model get <job_set_type> --json` 检查实时 schema；适配器 preflight 失败时不会占预算或继续生成。
- **Higgsfield任务完成但没有尾帧 URL** → 正常；适配器下载实际 MP4 后用 ffmpeg 提取真实末帧。
  不得拿独立场景图替代。
- **Higgsfield金额不可得但 credits 已记录** → credits 不等于稳定人民币价格。只有当前官方
  `cny_per_credit` 快照存在时才换算，否则只报告 credits。

## 图片生成

- **首图未确认就开始批量** → 跳过了 `anchor` 门禁。只展示第一张并等待用户明确确认整体
  风格、配色、材质、构图和产品准确性；不能用 Codex 自评替代用户批准。
- **全部图片刚生成就开始做视频** → 缺少 `images` 门禁。逐张展示整批及 section id，询问
  哪些图需要修改；只有用户明确表示全部通过并执行 `approve images` 后才能提交视频。
- **一张图不合格却整批重生** → 没有使用定向 retry。根据用户反馈只改目标 section 的 prompt/
  references，执行 `retry --stage still --id <scene>`；保留其他图片并在重生后重新确认整批。
- **换图后仍播放旧视频** → 使用了旧版或绕过编排器。先运行 `retry ... --explain`；v5 会按架构归档
  所有依赖视频、编码、QA、旧 dist 和成本报告，再从目标处恢复。不要手工只删除 still。
- **换图后旧批准仍显示 true** → 查看 `status` 的 `anchor_valid/images_valid`，不要只看原始布尔值。
  批准绑定文件指纹；图片改变或 retry 后必须再次批准，编排器不会让旧批准进入视频阶段。
- **锚点风格错误** → 在锚点门禁修正 `STYLE`，不要先批量。锚点失败成本是 1 张，冷批次
  失败成本是 N 张。
- **后续场景复制了锚点内容** → 参考图提示写得过于笼统。明确“只参考材质、角度、灯光、
  比例，不复制建筑或道具”，并把本场主体写具体。
- **批次风格漂移** → 共同 `STYLE` 没有逐字复用，或锚点未放进 `image` 数组。修正后只重试
  漂移项；其余已完成资产保留。
- **OutputImageSensitiveContentDetected / InputTextSensitiveContentDetected** → 读取官方错误，
  删除可能触发误判的词，改为中性建筑描述，如“无人、空置、展示空间、产品模型”；若参考图
  本身触发，重新生成干净锚点。不要通过规避安全审核的变体词攻击过滤器。
- **白色/纯色盒子边缘明显** → 页面背景与场景背景不一致。匹配 CSS 背景，或对浮岛场景运行
  `knockout.py`；全幅写实场景不要抠图。
- **已有图片但脚本退出 12** → prompt、模型、参数或参考图内容与缓存 fingerprint 不同。
  这是陈旧缓存保护；用 `retry --stage still --id ...` 归档旧证据后再生成，不要手改 meta。

## 视频任务

- **任务一直 queued/running** → 队列繁忙。保留任务 ID，降低后续并发并继续轮询；不要提交
  同一段的新任务，除非任务明确 failed/expired/cancelled。
- **OutputVideoSensitiveContentDetected** → 先保存错误信息与 request ID。用更中性、无人、
  产品展示/建筑空间的描述重试一次；仍失败则重新设计该镜头或用 `null` connector 交叉淡化。
  项目已锁定供应商，不因单个失败静默切换后端。
- **成功但没有 last_frame_url** → 提交请求遗漏 `return_last_frame: true`，或模型/Endpoint
  不支持返回尾帧。先检查保存的 `request.json`；缺字段则该段必须重提。不要用肉眼猜一个尾帧。
- **生成音频浪费或报错** → 设置 `generate_audio: false`，编码时 `-an`。滚动网页不需要音轨。
- **首尾帧请求被拒绝** → 两张图比例、格式或尺寸不兼容。把实际帧统一为 PNG/JPEG、相同比例，
  确保不超官方限制；不要把 16:9 与 9:16 混进同一任务。
- **模型生成了错误产品/人物/座椅** → 提示词没有写负约束，或参考图中存在歧义。写明主体
  唯一性和不应出现的对象；视觉审查失败就拒绝该段，SSIM 高并不代表语义正确。
- **预演正确、正式风格有跳变** → 最终链混用了 mini 和正式片/尾帧。正式链必须从第 1 段
  重新生成，整条链使用同一正式模型。
- **连续但进入了错误房间/产品** → 只传上一段尾帧导致语义缺失。架构 A 从第二段起把当前
  Seedream 场景图作为 `reference_image`，prompt 明确图片1管连续、图片2管内容。
- **缓存 task ID 与新 prompt 不一致** → 脚本退出 12；用编排器 retry 归档旧 task。失败 task
  也不会自动重提，避免重复扣费。

## 接缝

- **明显闪跳** → 下一段首帧不是上一段 API 返回的实际尾帧，或 connector 端点使用了源场景图。
  修正帧来源并重新生成；源图只能作为第一段/独立 dive 的首帧。
- **位置连续但像倒带** → 接缝前后速度反向。写实/室内 walkthrough 改用架构 A；每段最后一秒
  恢复向前漂移。架构 B 的拉远只适合微缩地图语法。
- **SSIM 低但肉眼似乎接近** → 编码过重、提取错帧或模型没有贴合端点。先用原始视频测一次：
  原始高、编码低就降低 CRF；两者都低则重生片段。<0.75 不允许只靠淡化掩盖。
- **重试后另一侧接缝坏了** → 一个片段同时参与前后两条边界。替换任何片段后重跑全链 SSIM。
- **只在首屏发生跳变** → 海报用了 Seedream 场景图。海报必须从编码后视频第 0 帧提取。

## 编码与浏览器

- **生产构建提示 browser QA missing/stale** → 运行 `browser-qa.py prepare`，在 file/http × 三种视口
  执行 browser-smoke 并记录 evidence。修改任何公开文件后必须重测，不能复制旧报告。

- **双击 HTML 后视频/图片不显示** → 页面仍在 `file://` 下 fetch MP4，或资源用了 `/assets/...`、
  localhost、用户目录绝对路径、ES module/服务端路由。设置 `directVideo: true`，把所有首方资源
  放进同一交付目录并使用相对路径，再运行 `verify-portable.py`；不要建议用户启动本地服务器。
- **本地双击正常、上传后资源 404** → 只上传了 HTML、破坏了目录层级或服务器区分大小写。
  原样上传完整 `output_dir`，保留文件名大小写和 `assets/` 结构，以 `portable-report.json` 为清单。
- **上传正常、本地双击失败** → 页面依赖模块脚本、Service Worker、API 路由或跨源 fetch；这些
  能力没有 `file://` 等价物。改为普通脚本和目录内静态资源，交付前同时满足 file/HTTP 两种启动方式。

- **视频卡在第 0 帧 / seekable=[0,0]** → 静态服务器不支持 Range。滚动引擎通过 Blob URL
  提供完整可寻址资源；不要移除 Blob 加载逻辑。
- **文件巨大** → 使用了 all-intra。桌面用 GOP 8，移动用 GOP 4；只在极低端手机确有证据时
  才考虑 GOP 2/1。
- **画质软、SSIM 下降** → 不要缩小正式 1080p；桌面 CRF 用 18–20，接缝纹理复杂时用 16。
  `unsharp` 保持轻度，过强会制造光晕。
- **快速滚动时手机冻结** → 确认手机实际加载 `-m.mp4`，720p、GOP 4，并保留引擎的 seek
  coalescing。CPU 4–6× 节流下测试。
- **iOS 黑屏** → iOS 未 prime 的 muted 视频可能不绘制 seek 后画面。保留 poster，第一次触摸时
  `play()` 后立即暂停，并保留 `playsinline`/`muted`。
- **iOS 低电量模式冻结** → `play()` 会拒绝；引擎应在 rejected promise 后切换静态图模式。
- **iPad 加载模糊移动源** → 资源分层不能按 pointer/UA。短边 ≤600 CSS px 才用手机源；iPad
  使用桌面源，但仍采用触摸行为优化。
- **data-saver 仍下载大视频** → 启用时直接进入静态图模式；2g/3g 缩小预取窗口。iOS 无可靠
  网络信号，因此默认策略必须保守：先海报，邻近场景才加载 Blob。
- **移动地址栏收缩导致页面跳动** → 触摸设备忽略仅高度变化的 resize；宽度变化/旋转才重排。
- **文字被刘海或地址栏挡住** → `<meta viewport>` 加 `viewport-fit=cover`，使用 safe-area 和
  `dvh`；检查 390×844 与横屏。
- **竖屏切掉主体** → Seedream 构图未中心安全。重做关键场景 9:16，或选择 full portrait chain；
  CSS 不能恢复已经裁掉的画面。
- **4K 在 Safari/旧浏览器黑屏** → Seedance Full 4K 可能是 HEVC 10-bit。生产流水线必须转
  H.264/yuv420p；不要把供应商原文件直接部署。

## 语义、交付与成本

- **SSIM 通过但产品错了** → SSIM 只测像素接缝。打开 still 与首/中/尾联系表，按
  must_include/must_not_include 逐项审查；资产变化后旧批准会失效。
- **生产包仍有 raw/preview/.work** → 没有从 `delivery.public_files` 构建，或白名单写错。
  只部署 `output_dir`；构建脚本会拒绝这些路径。
- **首屏/总包超预算** → 调低 CRF 质量必须重新跑 SSIM；优先减少预加载、压移动版或拆 CDN，
  不删静态回退。修改 `delivery.budgets` 只能基于真实交付约束，不能为了让检查变绿。
- **成本金额不可得** → 价格快照、视频 completion tokens 或 Codex 宿主 token 缺失。报告缺项
  与原因，不按字数猜 token、不用过期价格。订阅 Codex 可报告单次增量费 0，但不得把月费
  任意分摊成“精确对话成本”。

## 帧序列替代方案

视频 `currentTime` 是实用中档方案。如果客户要求低端设备也完全确定性，可将每段预提取成
WebP/AVIF 帧序列并绘制到 `<canvas>`，或使用 WebCodecs 预解码。代价是构建复杂度、请求数
和缓存策略增加；锚点、连续帧交接、SSIM、场景顺序和滚动映射仍然不变。
