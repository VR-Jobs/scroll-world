# 主题系统

运行 `scroll-world.py themes` 查看全部模板。初始化时用 `--theme <id>`，模板会被复制到
`world.json.design`，后续可按品牌修改；不要在生成后静默换主题。

| ID | 视觉方向 | 推荐场景 |
|---|---|---|
| `low-poly-clay` | 柔和微缩 clay | 产品叙事、亲和科技 |
| `chrome-futurism` | 铬金属、深蓝反射 | 硬件、汽车、未来品牌 |
| `editorial-brutalism` | 大字、硬裁切、图形平面 | 文化节、媒体、时尚 |
| `glass-laboratory` | 透明光学、白色实验室 | 医疗、镜片、科研 |
| `cosmic-ritual` | 黑曜石、蓝色符号、宇宙雾 | 艺术、音乐、文化活动 |
| `architectural-white` | 白色建筑、博物馆尺度 | 高端产品、地产、设计机构 |

每个主题提供 `style_prompt/palette/material/typography/motion/text_strategy`。把
`design.style_prompt` 逐字复用到所有图片和视频 prompt；具体场景只追加 subject、focal、next 和
正负约束。

关键品牌名、数字、CTA 和法律信息使用 HTML/CSS、SVG 或预制 GLB。只有非语言纹理和装饰符号可由
生成模型烘焙。需要透视内的 3D 文字时，优先使用预制 GLB/透明视频叠加；不得接受拼写错误的 AI 文字。
