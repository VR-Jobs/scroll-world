# 提示词模板与访谈字段（豆包 / Higgsfield）

场景一致性来自两层：所有场景逐字复用同一段 `STYLE`，并在锚点批准后把锚点作为
Seedream 的参考图。不要只写“同一风格”；明确需要保持的镜头角度、材质、灯光和比例。

## 访谈记录

- `SUBJECT`：产品/行业与一句话价值。
- `BRAND_NAME`：品牌展示名。
- `PALETTE`：4–6 个命名十六进制色；选一个背景色和一个强调色。
- `TONE`：品牌语气，如克制科技、温暖手作、奢华宁静。
- `STYLE`：美术方向。
- `ARCHITECTURE`：A 连续向前，或 B dive + connector。
- `BUDGET`：图片、mini 预演、正式视频、竖屏重绘和重试分别多少次。
- `SECTIONS[]`：每场 `id/label/subject/focal/next/eyebrow/title/body/tags/cta`。
- `MOBILE`：crop-safe / mobile-encodes / hero-reframe / portrait-chain。

## STYLE 与主题

优先读取 `world.json.design`。将 `design.style_prompt`、palette、material 和 motion 组合成稳定的
STYLE，并逐字复用到所有场景。主题目录与精确文字策略见 `themes.md`。

### 默认 STYLE：low-poly clay diorama

把方括号替换后，将整段逐字复制进每个场景提示词：

```text
等距视角 low-poly 3D 微缩世界，柔和哑光 clay 材质，圆润但结构清晰的低多边形造型，
轻微 tilt-shift 景深，干净的工作室光线，柔和长阴影与克制的体积光。场景像一个完整、
可进入的小型世界，而不是散乱道具。统一使用 [PALETTE]，背景为 [BG_HEX]。主体位于
画面水平中心并保留头部空间，16:9 与竖屏中心裁切都能读清。高细节，无文字、无字母、
无数字、无标识、无水印。
```

替代方向只替换前两句，统一色板和无文字约束保留：

- Papercraft：分层纸艺微缩场景、哑光卡纸、清晰模切边缘、层间柔影。
- Glossy toy：收藏级亮面塑料玩具、柔和轮廓光、圆润工业设计。
- Claymation：手工塑形、细微指纹与塑泥纹理、定格动画布景。
- Neon night：夜间微缩城市、蓝紫霓虹、暖色室内光、潮湿反射地面。
- 写实建筑：电影级建筑摄影、真实材质、克制家具、自然光、无人、全幅场景；跳过浮岛与抠图。

## Seedream 锚点图

```text
[STYLE]
品牌：[BRAND_NAME]。本世界的代表场景：[SECTION.subject]。核心视觉焦点：[FOCAL]。
使用 [PALETTE]，突出 [ACCENT]。这是后续所有场景的美术锚点：镜头角度稳定、尺度关系
清晰、材质统一。单张画面，无分镜，无拼图，无文字，无 logo。
```

请求：`POST /images/generations`，`size: "2K"`，`response_format: "url"`，
`watermark: false`。锚点不附参考图。

## Seedream 后续场景图

请求的 `image` 数组只放批准后的锚点图。提示词：

```text
[STYLE]
图一是整个系列的美术锚点。严格参考图一的低多边形语言、材质、镜头俯角、主光方向、
阴影软硬和物体比例，但不要复制图一的具体建筑或道具。
本场主题：[SECTION.subject]。焦点：[FOCAL]。通往下一场的视觉方向：[NEXT]。
保持同一个连续世界的地面、天空和色彩逻辑。单张画面，无分镜，无拼图，无文字，无 logo。
```

对产品终章：把场景描述改为一个放大的英雄产品，周围只保留少量轨道式道具；主体仍居中。

## Seedance 架构 A：连续向前 leg

`first_frame` = 第一场批准图，或上一段 API 返回的实际 `last_frame_url` 文件。
不要设置 `last_frame`。从第二段起，同时把本场 Seedream 场景图作为
`role: "reference_image"`：输入顺序中图片1是连续首帧，图片2是语义参考。

```text
单一连续电影镜头，全程无剪切、无跳帧、无转场。图片1是上一段的真实尾帧：严格延续
图片1最后一秒缓慢、稳定的向前漂移，不改变镜头位置、运动方向或速度。图片2只用于确定
即将进入的 [SCENE]、[FOCAL]、材质与品牌配色；自然飞入该世界，不要跳切或突然贴合
图片2的宽景构图。[MID_MOVE]。主体与图片2语义一致，不新增第二个产品，不出现人物、
座椅或文字（除非场景明确要求）。
最后一秒回到缓慢、稳定的向前漂移，朝 [NEXT] 前进，为下一段留下平稳接续状态。
[STYLE_TAIL]。真实景深、细腻光影、轻微视差，无文字、无字幕、无 logo。
```

段内运镜库：

- 产品：围绕核心产品缓慢半环绕，随后从产品旁继续向前。
- 室内：穿过门洞或玻璃，挑高处轻微升镜。
- 制造：低机位横向跟随生产线，再转回前进方向。
- 户外：缓慢升起揭示全景，再向下一入口俯冲。
- 工艺：推近细节后缓慢拉回，并恢复向前漂移。

若是第一段且没有图片2，删除图片编号说明，直接从批准图起镜。每段结束后检查 API 尾帧；
若画面仍在剧烈旋转、
横移或运动模糊中，重试当前段，不要让坏尾帧污染后续链条。

## Seedance 架构 B：dive

`first_frame` = 本场 Seedream 图。

```text
单一连续电影镜头，全程无剪切。起始保持参考图构图，从高处看见完整的 [SCENE]。摄影机
缓慢向前并下降，朝 [FOCAL] 飞入微缩世界；建筑上部自然打开或镜头穿过入口，揭示内部。
最后一秒保持缓慢向前移动。[STYLE_TAIL]，柔和视差，无文字、无字幕、无 logo。
```

没有建筑时，把“打开”改为“贴近地面穿过场景”。

## Seedance 架构 B：connector

`first_frame` = dive i 返回的真实尾帧；`last_frame` = dive i+1 视频提取的真实首帧。

```text
单一连续电影镜头，全程无剪切。摄影机从 [SCENE_I] 当前画面平滑升高并向后拉开，进入同一
个连贯的微缩世界上空；随后向前跨越地形，抵达 [SCENE_NEXT]，在结尾开始缓慢下降并精确
贴合目标尾帧构图。运动曲线平滑，没有突然加速、方向跳变或内容变形。[STYLE_TAIL]。
无文字、无字幕、无 logo。
```

## Seedance 请求参数

- mini 预演：模型变量 `ARK_VIDEO_PREVIEW_MODEL`，`resolution: "720p"`。
- 正式片：模型变量 `ARK_VIDEO_FINAL_MODEL`，`resolution: "1080p"`。
- 默认 `ratio: "16:9"`、`duration: 5`、`generate_audio: false`、
  `watermark: false`、`return_last_frame: true`。
- 竖屏链改为 `ratio: "9:16"`；同一条链不能混合比例。

## 每场网页文案

- `eyebrow`：2–4 个词，表达价值类别。
- `title`：3–8 个中文词；首场是页面 h1，末场是价值高潮。
- `body`：一句，从访客收益出发。
- `tags`：0–3 个证据标签。
- `cta`：末场使用真实链接与行动文案。
