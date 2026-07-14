#!/usr/bin/env python3
"""Prepare, record, and verify browser evidence for scroll-world delivery."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

import sw_tool


REQUIRED_VIEWPORTS = {"desktop", "mobile", "tablet"}
REQUIRED_LAUNCH_MODES = {"file", "http"}


def load_world(value: str) -> tuple[Path, Path, dict]:
    world_path = Path(value).resolve()
    world = sw_tool.read_json(world_path, {})
    errors = sw_tool.validate_world(world)
    if errors:
        raise SystemExit("invalid world.json:\n- " + "\n- ".join(errors))
    return world_path, world_path.parent, world


def resolve(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if root != path and root not in path.parents:
        raise SystemExit(f"path escapes project root: {value}")
    return path


def delivery_fingerprint(root: Path, world: dict) -> str:
    digest = hashlib.sha256()
    for value in sorted(set((world.get("delivery") or {}).get("public_files") or [])):
        path = resolve(root, value)
        if not path.is_file():
            raise SystemExit(f"browser QA input missing: {value}")
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sw_tool.sha_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def evidence_error(item: dict) -> str | None:
    mode = item.get("launch_mode")
    viewport = item.get("viewport")
    smoke = item.get("smoke") or {}
    if mode not in REQUIRED_LAUNCH_MODES:
        return f"invalid launch_mode: {mode}"
    if viewport not in REQUIRED_VIEWPORTS:
        return f"invalid viewport: {viewport}"
    if smoke.get("pass") is not True:
        return f"{mode}/{viewport}: browser smoke failed"
    if int(smoke.get("videoCount") or 0) < 1:
        return f"{mode}/{viewport}: no videos found"
    videos = smoke.get("videos") or []
    if not any(float(row.get("seekableEnd") or 0) > 0 for row in videos):
        return f"{mode}/{viewport}: no seekable video"
    if not any(row.get("currentTimeChanged") is True for row in videos):
        return f"{mode}/{viewport}: scroll did not change video time"
    if item.get("console_errors"):
        return f"{mode}/{viewport}: console errors were recorded"
    if item.get("visual_pass") is not True:
        return f"{mode}/{viewport}: visual review did not pass"
    return None


def cmd_prepare(args) -> int:
    _world_path, root, world = load_world(args.world)
    plan = {
        "schema_version": 1,
        "delivery_fingerprint": delivery_fingerprint(root, world),
        "entry": (world.get("delivery") or {}).get("portable", {}).get("entry", "index.html"),
        "required_launch_modes": sorted(REQUIRED_LAUNCH_MODES),
        "required_viewports": {
            "desktop": [1440, 900],
            "mobile": [390, 844],
            "tablet": [834, 1194],
        },
        "smoke_script": str((Path(__file__).resolve().parents[1] / "references" / "browser-smoke.js")),
        "instructions": "Run browser-smoke.js for every launch-mode/viewport pair, capture console errors, and record visual_pass.",
    }
    output = root / ".work" / "qa" / "browser-plan.json"
    sw_tool.atomic_write(output, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_record(args) -> int:
    _world_path, root, world = load_world(args.world)
    try:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read browser evidence: {exc}") from exc
    items = evidence.get("runs") if isinstance(evidence, dict) else evidence
    if not isinstance(items, list):
        raise SystemExit("browser evidence must be an array or an object with runs[]")
    errors = [error for item in items if isinstance(item, dict) for error in [evidence_error(item)] if error]
    pairs = {(item.get("launch_mode"), item.get("viewport")) for item in items if isinstance(item, dict)}
    required_pairs = {(mode, viewport) for mode in REQUIRED_LAUNCH_MODES for viewport in REQUIRED_VIEWPORTS}
    missing = sorted(required_pairs - pairs)
    if missing:
        errors.append("missing browser runs: " + ", ".join(f"{mode}/{viewport}" for mode, viewport in missing))
    report = {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "delivery_fingerprint": delivery_fingerprint(root, world),
        "runs": items,
        "errors": errors,
    }
    output = root / ".work" / "qa" / "browser-report.json"
    sw_tool.atomic_write(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 4


def cmd_check(args) -> int:
    _world_path, root, world = load_world(args.world)
    report_path = root / ".work" / "qa" / "browser-report.json"
    report = sw_tool.read_json(report_path, {})
    errors = list(report.get("errors") or [])
    if report.get("status") != "pass":
        errors.append("browser QA report is missing or not passed")
    try:
        current = delivery_fingerprint(root, world)
    except SystemExit as exc:
        errors.append(str(exc))
        current = None
    if current and report.get("delivery_fingerprint") != current:
        errors.append("browser QA evidence is stale for the current delivery files")
    if errors:
        print("\n".join(f"ERROR: {error}" for error in dict.fromkeys(errors)), file=sys.stderr)
        return 4
    print(f"browser QA passed: {report_path}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    for name, function in [("prepare", cmd_prepare), ("check", cmd_check)]:
        item = sub.add_parser(name)
        item.add_argument("--world", default="world.json")
        item.set_defaults(func=function)
    record = sub.add_parser("record")
    record.add_argument("--world", default="world.json")
    record.add_argument("--evidence", required=True)
    record.set_defaults(func=cmd_record)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
