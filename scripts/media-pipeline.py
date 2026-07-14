#!/usr/bin/env python3
"""Encode web delivery assets and enforce codec, size and seam gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import sw_tool


def resolve(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if root != path and root not in path.parents:
        raise SystemExit(f"path escapes project root: {value}")
    return path


def run(command: list[str], capture=False):
    result = subprocess.run(command, text=True, capture_output=capture, check=False)
    if result.returncode:
        if capture:
            sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result


def digest(path: Path, settings: str) -> str:
    value = hashlib.sha256()
    value.update(settings.encode("utf-8"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def encode(raw: Path, output: Path, mobile: bool, state: dict) -> None:
    settings = "mobile:720:crf22:g4:v1" if mobile else "desktop:source:crf18:g8:v1"
    fingerprint = digest(raw, settings)
    key = str(output)
    if output.is_file() and state.get(key) == fingerprint:
        print(f"encode cached: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-an"]
    if mobile:
        command.extend(["-vf", "scale=-2:720,unsharp=5:5:0.5:5:5:0.0", "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-pix_fmt", "yuv420p", "-g", "4", "-keyint_min", "4"])
    else:
        command.extend(["-vf", "unsharp=5:5:0.6:5:5:0.0", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-g", "8", "-keyint_min", "8"])
    command.extend(["-sc_threshold", "0", "-movflags", "+faststart", str(output)])
    run(command)
    state[key] = fingerprint


def poster(video: Path, output: Path, quality: int, state: dict) -> None:
    settings = f"poster:webp:q{quality}:v1"
    fingerprint = digest(video, settings)
    key = str(output)
    if output.is_file() and state.get(key) == fingerprint:
        print(f"poster cached: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-i", str(video), "-frames:v", "1", "-c:v", "libwebp", "-quality", str(quality), str(output)])
    state[key] = fingerprint


def probe(path: Path) -> dict:
    result = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate",
        "-of", "json", str(path),
    ], capture=True)
    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        raise SystemExit(f"no video stream: {path}")
    return streams[0]


def seam_score(left: Path, right: Path, work: Path) -> float:
    work.mkdir(parents=True, exist_ok=True)
    a = work / "left.png"
    b = work / "right.png"
    run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.05", "-i", str(left), "-frames:v", "1", str(a)])
    run(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-i", str(right), "-frames:v", "1", str(b)])
    result = subprocess.run(["ffmpeg", "-v", "info", "-i", str(a), "-i", str(b), "-lavfi", "ssim", "-f", "null", "-"], text=True, capture_output=True, check=False)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    matches = re.findall(r"All:([0-9.]+)", result.stderr)
    if not matches:
        raise SystemExit(f"unable to parse SSIM for {left} -> {right}")
    return float(matches[-1])


def sampled_gray_frames(video: Path, width: int = 64, height: int = 36, fps: int = 4) -> list[bytes]:
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={width}:{height}",
        "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise SystemExit(result.stderr.decode("utf-8", errors="replace"))
    frame_size = width * height
    return [result.stdout[index:index + frame_size] for index in range(0, len(result.stdout), frame_size) if len(result.stdout[index:index + frame_size]) == frame_size]


def mean_abs_difference(left: bytes, right: bytes) -> float:
    return sum(abs(a - b) for a, b in zip(left, right)) / max(1, len(left))


def automatic_video_metrics(video: Path) -> dict:
    frames = sampled_gray_frames(video)
    if not frames:
        raise SystemExit(f"unable to sample video quality frames: {video}")
    luma = [sum(frame) / len(frame) for frame in frames]
    motion = [mean_abs_difference(frames[index - 1], frames[index]) for index in range(1, len(frames))]
    black_ratio = sum(value < 8 for value in luma) / len(luma)
    freeze_ratio = sum(value < 0.75 for value in motion) / len(motion) if motion else 1.0
    verdict = "FAIL" if black_ratio > 0.5 else "WARN" if black_ratio > 0.05 or freeze_ratio > 0.95 else "PASS"
    return {
        "sample_count": len(frames),
        "mean_luma": round(sum(luma) / len(luma), 4),
        "black_frame_ratio": round(black_ratio, 4),
        "freeze_ratio": round(freeze_ratio, 4),
        "motion_energy_mean": round(sum(motion) / len(motion), 4) if motion else 0.0,
        "motion_energy_start": round(motion[0], 4) if motion else 0.0,
        "motion_energy_end": round(motion[-1], 4) if motion else 0.0,
        "verdict": verdict,
    }


def still_to_video_score(still: Path, video: Path, work: Path) -> float:
    work.mkdir(parents=True, exist_ok=True)
    first = work / "video-first.png"
    run(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-i", str(video), "-frames:v", "1", str(first)])
    result = subprocess.run([
        "ffmpeg", "-v", "info", "-i", str(still), "-i", str(first), "-lavfi",
        "[0:v]scale=160:90[a];[1:v]scale=160:90[b];[a][b]ssim", "-f", "null", "-",
    ], text=True, capture_output=True, check=False)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    matches = re.findall(r"All:([0-9.]+)", result.stderr)
    if not matches:
        raise SystemExit(f"unable to parse still/video SSIM for {still} -> {video}")
    return float(matches[-1])


def delivery_nodes(world: dict) -> list[dict]:
    if world["project"]["architecture"] == "A":
        return world["sections"]
    nodes = []
    transitions = world.get("transitions") or []
    for index, section in enumerate(world["sections"]):
        nodes.append(section)
        if index < len(transitions):
            nodes.append(transitions[index])
    return nodes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="world.json")
    args = parser.parse_args()
    world_path = Path(args.world).resolve()
    root = world_path.parent
    world = sw_tool.read_json(world_path, {})
    errors = sw_tool.validate_world(world)
    if errors:
        raise SystemExit("invalid world.json:\n- " + "\n- ".join(errors))
    for command in ["ffmpeg", "ffprobe"]:
        if not shutil.which(command):
            raise SystemExit(f"required command not found: {command}")

    cache_path = root / ".work" / "media-cache.json"
    cache = sw_tool.read_json(cache_path, {})
    nodes = delivery_nodes(world)
    assets = []
    automatic_quality = []
    for node in nodes:
        outputs = node.get("outputs") or {}
        required = ["raw_final", "final_video", "mobile_video"]
        if any(key not in outputs for key in required):
            raise SystemExit(f"delivery node {node.get('id')} missing media output fields")
        raw = resolve(root, outputs["raw_final"])
        if not raw.is_file():
            raise SystemExit(f"missing raw final video: {raw}")
        final_video = resolve(root, outputs["final_video"])
        mobile_video = resolve(root, outputs["mobile_video"])
        encode(raw, final_video, False, cache)
        encode(raw, mobile_video, True, cache)
        if outputs.get("poster"):
            poster(final_video, resolve(root, outputs["poster"]), 86, cache)
        if outputs.get("mobile_poster"):
            poster(mobile_video, resolve(root, outputs["mobile_poster"]), 84, cache)
        for variant, video in [("desktop", final_video), ("mobile", mobile_video)]:
            info = probe(video)
            if info.get("codec_name") != "h264" or info.get("pix_fmt") != "yuv420p":
                raise SystemExit(f"web codec gate failed: {video}: {info}")
            if variant == "mobile" and int(info.get("height") or 0) > 720:
                raise SystemExit(f"mobile height exceeds 720: {video}")
            assets.append({"id": node["id"], "variant": variant, "path": str(video.relative_to(root)), "bytes": video.stat().st_size, "probe": info})
        metrics = automatic_video_metrics(final_video)
        item = {"id": node["id"], "video": str(final_video.relative_to(root)), **metrics}
        still_value = (node.get("outputs") or {}).get("still")
        if still_value:
            alignment = still_to_video_score(resolve(root, still_value), final_video, root / ".work" / "qa" / "alignment" / node["id"])
            item["still_to_first_ssim"] = alignment
            if alignment < float((world.get("quality") or {}).get("still_alignment_warn_below", 0.65)) and item["verdict"] == "PASS":
                item["verdict"] = "WARN"
        automatic_quality.append(item)
    sw_tool.atomic_write(cache_path, cache)

    quality = world.get("quality") or {}
    fail_below = float(quality.get("seam_fail_below", 0.75))
    warn_below = float(quality.get("seam_warn_below", 0.90))
    seams = []
    failed = False
    for index in range(len(nodes) - 1):
        left = resolve(root, nodes[index]["outputs"]["final_video"])
        right = resolve(root, nodes[index + 1]["outputs"]["final_video"])
        label = f"{nodes[index]['id']}>{nodes[index + 1]['id']}"
        score = seam_score(left, right, root / ".work" / "qa" / "seams" / label)
        verdict = "PASS" if score >= warn_below else "WARN" if score >= fail_below else "FAIL"
        left_motion = automatic_quality[index]["motion_energy_end"]
        right_motion = automatic_quality[index + 1]["motion_energy_start"]
        motion_jump = abs(left_motion - right_motion) / max(1.0, min(left_motion, right_motion) or 1.0)
        motion_verdict = "WARN" if motion_jump > float(quality.get("motion_jump_warn_ratio", 2.5)) else "PASS"
        failed = failed or verdict == "FAIL"
        seams.append({"label": label, "ssim": score, "verdict": verdict, "motion_jump_ratio": round(motion_jump, 4), "motion_verdict": motion_verdict})
        print(f"{verdict} {label} ssim={score:.6f}")

    failed = failed or any(item["verdict"] == "FAIL" for item in automatic_quality)
    report = {"schema_version": 2, "assets": assets, "automatic_quality": automatic_quality, "seams": seams, "thresholds": {"warn_below": warn_below, "fail_below": fail_below, "motion_jump_warn_ratio": float(quality.get("motion_jump_warn_ratio", 2.5)), "still_alignment_warn_below": float(quality.get("still_alignment_warn_below", 0.65))}}
    sw_tool.atomic_write(root / ".work" / "qa" / "media-report.json", report)
    if failed:
        print("one or more seams failed; production build is blocked", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
