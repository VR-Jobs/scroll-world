#!/usr/bin/env python3
"""Build an allowlisted production directory and enforce delivery budgets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import sw_tool


BLOCKED_PARTS = {".work", ".git", "prompts", "node_modules"}
BLOCKED_NAMES = {".env", ".env.local"}
BLOCKED_MARKERS = ("raw-preview", "raw-final", "create-response", "status-response", "request.json")
SCRIPT_DIR = Path(__file__).resolve().parent


def resolve(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if root != path and root not in path.parents:
        raise SystemExit(f"path escapes project root: {value}")
    return path


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate_public_path(value: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"unsafe public path: {value}")
    if any(part in BLOCKED_PARTS for part in path.parts):
        raise SystemExit(f"blocked private directory in public_files: {value}")
    if path.name in BLOCKED_NAMES or path.name.startswith(".env."):
        raise SystemExit(f"blocked secret file in public_files: {value}")
    if any(marker in value for marker in BLOCKED_MARKERS):
        raise SystemExit(f"blocked source/work asset in public_files: {value}")


def referenced_assets(root: Path, public_files: set[str]) -> set[str]:
    references = set()
    patterns = [
        re.compile(r"(?:src|href)=[\"']([^\"'#?]+)"),
        re.compile(r"url\([\"']?([^\"')?#]+)"),
        re.compile(r"[\"'](assets/[^\"'?]+)[\"']"),
    ]
    for value in public_files:
        path = resolve(root, value)
        if path.suffix.lower() not in {".html", ".css", ".js", ".json"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            for match in pattern.findall(text):
                if match.startswith(("http://", "https://", "data:", "#", "/")):
                    continue
                candidate = str((Path(value).parent / match).as_posix())
                candidate = str(Path(candidate))
                if candidate.startswith("./"):
                    candidate = candidate[2:]
                if (root / candidate).is_file():
                    references.add(candidate)
    return references


def verify_portable(root: Path, entry: str, json_output: Path | None = None) -> None:
    command = [sys.executable, str(SCRIPT_DIR / "verify-portable.py"), "--root", str(root), "--entry", entry]
    if json_output is not None:
        command.extend(["--json-output", str(json_output)])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "portable delivery verification failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="world.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    world_path = Path(args.world).resolve()
    root = world_path.parent
    world = sw_tool.read_json(world_path, {})
    errors = sw_tool.validate_world(world)
    if errors:
        raise SystemExit("invalid world.json:\n- " + "\n- ".join(errors))
    if (world.get("quality") or {}).get("require_browser_qa", True):
        browser_check = subprocess.run([
            sys.executable, str(SCRIPT_DIR / "browser-qa.py"), "check", "--world", str(world_path),
        ], text=True, capture_output=True, check=False)
        if browser_check.returncode:
            raise SystemExit(browser_check.stderr.strip() or "browser QA gate failed")
    delivery = world["delivery"]
    portable = delivery.get("portable") or {}
    entry = portable.get("entry", "index.html")
    if portable.get("direct_video", True) is not True:
        raise SystemExit("delivery.portable.direct_video must be true")
    public_values = delivery.get("public_files") or []
    if len(set(public_values)) != len(public_values):
        raise SystemExit("delivery.public_files contains duplicates")
    public = set(public_values)
    initial = set(delivery.get("initial_files") or [])
    if not initial.issubset(public):
        raise SystemExit("delivery.initial_files must be a subset of public_files")

    entries = []
    for value in public_values:
        validate_public_path(value)
        source = resolve(root, value)
        if not source.is_file():
            raise SystemExit(f"missing public file: {value}")
        entries.append({"path": value, "bytes": source.stat().st_size, "sha256": sha256(source)})
    missing_allowlist = referenced_assets(root, public) - public
    if missing_allowlist:
        raise SystemExit("locally referenced files are not allowlisted: " + ", ".join(sorted(missing_allowlist)))

    total_bytes = sum(item["bytes"] for item in entries)
    initial_bytes = sum(item["bytes"] for item in entries if item["path"] in initial)
    videos = [item for item in entries if Path(item["path"]).suffix.lower() in {".mp4", ".webm", ".mov"}]
    largest_video = max((item["bytes"] for item in videos), default=0)
    budgets = delivery.get("budgets") or {}
    checks = [
        ("total", total_bytes, float(budgets.get("max_total_mb", 10**9)) * 1024 * 1024),
        ("initial", initial_bytes, float(budgets.get("max_initial_mb", 10**9)) * 1024 * 1024),
        ("single video", largest_video, float(budgets.get("max_single_video_mb", 10**9)) * 1024 * 1024),
    ]
    failures = [f"{name} {actual / 1024 / 1024:.2f}MB > {limit / 1024 / 1024:.2f}MB" for name, actual, limit in checks if actual > limit]
    manifest = {
        "schema_version": 1,
        "files": entries,
        "totals": {
            "files": len(entries),
            "total_bytes": total_bytes,
            "initial_bytes": initial_bytes,
            "largest_video_bytes": largest_video,
        },
        "budgets": budgets,
        "budget_failures": failures,
    }
    print(json.dumps(manifest["totals"], indent=2))
    if failures:
        raise SystemExit("delivery budget failed:\n- " + "\n- ".join(failures))
    if args.dry_run:
        verify_portable(root, entry)
        print("production build dry-run passed")
        return 0

    output = resolve(root, delivery["output_dir"])
    if output == root:
        raise SystemExit("delivery.output_dir cannot be the project root")
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        for value in public_values:
            source = resolve(root, value)
            target = staging / value
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        sw_tool.atomic_write(staging / "asset-manifest.json", manifest)
        verify_portable(staging, entry, staging / "portable-report.json")
        backup = output.with_name(f".{output.name}.previous")
        if backup.exists():
            shutil.rmtree(backup)
        if output.exists():
            os.replace(output, backup)
        os.replace(staging, output)
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"production build ready: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
