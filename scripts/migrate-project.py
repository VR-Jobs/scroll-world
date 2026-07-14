#!/usr/bin/env python3
"""Explicit, reversible migration of scroll-world projects to schema v2."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import sw_tool


def migrate(world: dict, theme: str) -> tuple[dict, list[str]]:
    source_version = int(world.get("schema_version", 1))
    if source_version > 2:
        raise SystemExit(f"project schema {source_version} is newer than this skill supports")
    changed: list[str] = []
    value = json.loads(json.dumps(world))
    if source_version < 2:
        value["schema_version"] = 2
        changed.append("schema_version: 1 -> 2")
    project = value.setdefault("project", {})
    if not project.get("theme"):
        project["theme"] = theme
        changed.append(f"project.theme: {theme}")
    if not isinstance(value.get("design"), dict):
        value["design"] = sw_tool.load_theme(project["theme"])
        changed.append("design: copied selected theme template")
    quality = value.setdefault("quality", {})
    defaults = {
        "require_browser_qa": True,
        "still_alignment_warn_below": 0.65,
        "motion_jump_warn_ratio": 2.5,
    }
    for key, default in defaults.items():
        if key not in quality:
            quality[key] = default
            changed.append(f"quality.{key}: {default}")
    return value, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="world.json")
    parser.add_argument("--theme", choices=sorted(sw_tool.theme_catalog()), default="low-poly-clay")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    world_path = Path(args.world).resolve()
    original = sw_tool.read_json(world_path, {})
    migrated, changed = migrate(original, args.theme)
    errors = sw_tool.validate_world(migrated)
    if errors:
        raise SystemExit("migrated world would be invalid:\n- " + "\n- ".join(errors))
    result = {"world": str(world_path), "from": original.get("schema_version", 1), "to": migrated["schema_version"], "changes": changed}
    if args.dry_run or not changed:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = world_path.parent / ".work" / "migrations" / f"world-v{original.get('schema_version', 1)}-{stamp}.json"
    sw_tool.atomic_write(backup, original)
    sw_tool.atomic_write(world_path, migrated)
    marker_path = world_path.parent / ".scroll-world-project.json"
    marker = sw_tool.read_json(marker_path, {})
    if marker:
        marker["schema_version"] = 2
        marker["theme"] = migrated["project"]["theme"]
        marker["migrated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        sw_tool.atomic_write(marker_path, marker)
    result["backup"] = str(backup)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
