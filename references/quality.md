# 自动质量与浏览器证据

## 内容

1. 媒体自动指标
2. 人工语义检查
3. 浏览器证据格式
4. 完成门禁

## 1. 媒体自动指标

`media-pipeline.py` 写入 `.work/qa/media-report.json`：

- `seams[].ssim`：相邻编码片段的像素连续性；低于 `seam_fail_below` 阻止构建。
- `automatic_quality[].black_frame_ratio`：采样黑帧比例；超过 0.5 判 FAIL。
- `freeze_ratio`：相邻采样帧变化极小的比例；高值判 WARN，避免把克制慢镜头误杀。
- `mean_luma`：平均亮度，用于发现异常曝光。
- `motion_energy_start/end/mean`：灰度帧差近似运动能量。
- `still_to_first_ssim`：批准图片与正式视频首帧的一致性；低于阈值判 WARN。
- `seams[].motion_jump_ratio`：接缝两侧运动能量突变；超过阈值判 WARN。

自动指标不能识别品牌语义、产品结构或文字正确性。详细版继续执行 `qa-assets.py` 的逐场
must_include/must_not_include 审批；快速版在最终统一验收中查看联系表。

## 2. 浏览器证据

浏览器 QA 固定覆盖六个组合：

```text
file  × desktop/mobile/tablet
http  × desktop/mobile/tablet
```

每个组合在页面上下文执行 `references/browser-smoke.js`，并由浏览器控制层额外记录控制台错误和
视觉检查。保存一个 JSON：

```json
{
  "runs": [
    {
      "launch_mode": "file",
      "viewport": "desktop",
      "console_errors": [],
      "visual_pass": true,
      "smoke": {
        "pass": true,
        "videoCount": 2,
        "videos": [{"seekableEnd": 5, "currentTimeChanged": true}]
      }
    }
  ]
}
```

`record` 要求六个组合齐全、至少一个视频可 seek、滚动确实改变视频时间、无控制台错误且视觉通过。
报告绑定 `delivery.public_files` 的文件名和 SHA-256；任何页面、海报、脚本或视频变化都会使报告过期。

```bash
python3 "$SW/scripts/browser-qa.py" prepare --world "$WORLD"
python3 "$SW/scripts/browser-qa.py" record --world "$WORLD" --evidence /absolute/evidence.json
python3 "$SW/scripts/browser-qa.py" check --world "$WORLD"
```

## 3. 完成门禁

`scroll-world.py run` 和 `build-production.py` 都会执行 browser `check`。缺失、失败或过期时停止，不能
以“代码看起来正确”替代真实浏览器证据。生产完成仍需 `verify-portable.py` 检查相对路径、资源闭包、
H.264/yuv420p 和双击入口。
