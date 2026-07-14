#!/usr/bin/env python3
"""Prepare and record semantic QA for generated scenes and videos."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import sw_tool


def resolve(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if root != path and root not in path.parents:
        raise SystemExit(f"path escapes project root: {value}")
    return path


def digest(paths: list[Path], constraints: dict) -> str:
    value = hashlib.sha256(json.dumps(constraints, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for path in paths:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                value.update(chunk)
    return value.hexdigest()


def duration(video: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video),
    ], text=True, capture_output=True, check=False)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return float(result.stdout.strip())


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def make_contact_sheet(video: Path, output: Path) -> None:
    length = duration(video)
    times = [0.0, max(0.0, length / 2), max(0.0, length - 0.08)]
    frames = [output.with_name(f".{output.stem}-{index}.jpg") for index in range(3)]
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for time_value, frame in zip(times, frames):
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{time_value:.3f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=640:-2", str(frame)])
        run([
            "ffmpeg", "-v", "error", "-y",
            "-i", str(frames[0]), "-i", str(frames[1]), "-i", str(frames[2]),
            "-filter_complex", "hstack=inputs=3", "-frames:v", "1", str(output),
        ])
    finally:
        for frame in frames:
            frame.unlink(missing_ok=True)


def load(args):
    world_path = Path(args.world).resolve()
    root = world_path.parent
    world = sw_tool.read_json(world_path, {})
    errors = sw_tool.validate_world(world)
    if errors:
        raise SystemExit("invalid world.json:\n- " + "\n- ".join(errors))
    return world_path, root, world


def cmd_prepare(args) -> int:
    _, root, world = load(args)
    for command in ["ffmpeg", "ffprobe"]:
        if not shutil.which(command):
            raise SystemExit(f"required command not found: {command}")
    report_path = root / ".work" / "qa" / "semantic-report.json"
    previous = sw_tool.read_json(report_path, {"sections": {}})
    report = {"schema_version": 1, "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(), "sections": {}}
    for section in world["sections"]:
        still = resolve(root, section["outputs"]["still"])
        video = resolve(root, section["outputs"]["final_video"])
        if not still.is_file() or not video.is_file():
            raise SystemExit(f"semantic QA input missing for {section['id']}: {still} / {video}")
        constraints = section.get("qa") or {"must_include": [], "must_not_include": []}
        fingerprint = digest([still, video], constraints)
        contact = root / ".work" / "qa" / f"contact-{section['id']}.jpg"
        old = (previous.get("sections") or {}).get(section["id"], {})
        if not contact.is_file() or old.get("fingerprint") != fingerprint:
            make_contact_sheet(video, contact)
        if old.get("fingerprint") == fingerprint:
            status = old.get("status", "pending")
            review = old.get("review")
        else:
            status = "pending"
            review = None
        report["sections"][section["id"]] = {
            "fingerprint": fingerprint,
            "still": str(still.relative_to(root)),
            "video": str(video.relative_to(root)),
            "contact_sheet": str(contact.relative_to(root)),
            "must_include": constraints.get("must_include") or [],
            "must_not_include": constraints.get("must_not_include") or [],
            "status": status,
            "review": review,
        }
    sw_tool.atomic_write(report_path, report)
    print(f"semantic QA prepared: {report_path}")
    for item_id, item in report["sections"].items():
        print(f"{item_id}: {item['status']} -> {item['contact_sheet']}")
    return 0


def cmd_review(args) -> int:
    _, root, _ = load(args)
    report_path = root / ".work" / "qa" / "semantic-report.json"
    report = sw_tool.read_json(report_path, {})
    sections = report.get("sections") or {}
    if args.section not in sections:
        raise SystemExit(f"unknown or unprepared section: {args.section}")
    if args.status == "fail" and not args.notes:
        raise SystemExit("a failed review requires --notes")
    sections[args.section]["status"] = args.status
    sections[args.section]["review"] = {
        "reviewer": args.reviewer,
        "notes": args.notes,
        "reviewed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    sw_tool.atomic_write(report_path, report)
    print(f"semantic review recorded: {args.section}={args.status}")
    return 0


def cmd_check(args) -> int:
    _, root, _ = load(args)
    report = sw_tool.read_json(root / ".work" / "qa" / "semantic-report.json", {})
    sections = report.get("sections") or {}
    if not sections:
        print("semantic QA has not been prepared", file=sys.stderr)
        return 2
    failed = {key: item.get("status") for key, item in sections.items() if item.get("status") != "pass"}
    if failed:
        print(json.dumps(failed, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3
    print(f"semantic QA passed: {len(sections)} sections")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--world", default="world.json")
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("review")
    p.add_argument("--world", default="world.json")
    p.add_argument("--section", required=True)
    p.add_argument("--status", choices=["pass", "fail"], required=True)
    p.add_argument("--notes")
    p.add_argument("--reviewer", default="codex-vision")
    p.set_defaults(func=cmd_review)
    p = sub.add_parser("check")
    p.add_argument("--world", default="world.json")
    p.set_defaults(func=cmd_check)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
