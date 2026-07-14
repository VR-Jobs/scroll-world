#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import provider_config
import sw_tool
from providers import get_provider


MARKER = ".scroll-world-project.json"
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PROJECT_DIRS = ("prompts", "assets", "assets/vid", ".work", "dist")


def fail(message: str, code: int = 2) -> None:
    print(f"init-project: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path}: {exc}", 4)
    if not isinstance(value, dict):
        fail(f"expected a JSON object in {path}", 4)
    return value


def initialize(workspace_root: Path, name: str, slug: str, mode: str, provider: str, theme: str, resume: bool) -> Path:
    if not workspace_root.is_dir():
        fail(f"workspace root does not exist or is not a directory: {workspace_root}")
    if not name.strip() or name != name.strip():
        fail("name must be non-empty and have no leading or trailing whitespace")
    if not SLUG_PATTERN.fullmatch(slug) or slug in {".", ".."}:
        fail("slug must be 1-64 ASCII letters, digits, dots, underscores, or hyphens")

    project_root = workspace_root / slug
    marker_path = project_root / MARKER

    if project_root.exists() and any(project_root.iterdir()):
        if not resume:
            fail(
                f"project directory already exists: {project_root}; "
                "use --resume only for the same marked project, or choose a new slug",
                3,
            )
        if not marker_path.is_file():
            fail(f"refusing to resume unmarked directory: {project_root}", 4)
        marker = load_json(marker_path)
        marker_mode = marker.get("workflow_mode", "detailed")
        marker_provider = marker.get("provider", "doubao")
        marker_theme = marker.get("theme", "low-poly-clay")
        if marker.get("project_name") != name or marker.get("project_slug") != slug or marker_mode != mode or marker_provider != provider or marker_theme != theme:
            fail(f"project marker does not match name/slug/mode/provider/theme: {marker_path}", 4)
        print(json.dumps({
            "status": "resumed",
            "project_root": str(project_root),
            "workflow_mode": mode,
            "provider": provider,
            "theme": theme,
        }, ensure_ascii=False))
        return project_root

    project_root.mkdir(parents=False, exist_ok=True)
    for relative in PROJECT_DIRS:
        (project_root / relative).mkdir(parents=True, exist_ok=True)

    skill_root = Path(__file__).resolve().parents[1]
    world = load_json(skill_root / "assets" / "world.example.json")
    world.pop("$schema", None)
    project = world.setdefault("project", {})
    project["brand"] = name
    project["workflow_mode"] = mode
    project["provider"] = provider
    project["theme"] = theme
    world["design"] = sw_tool.load_theme(theme)
    world["models"] = get_provider(provider, Path(__file__).resolve().parent).default_model_aliases()
    world.setdefault("generation", {})["preview_enabled"] = mode == "detailed"
    world.setdefault("quality", {})["require_semantic_approval"] = mode == "detailed"
    (project_root / "world.json").write_text(
        json.dumps(world, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (project_root / ".gitignore").write_text(
        ".env.local\n.env.*.local\n.work/\n*.log\n",
        encoding="utf-8",
    )
    marker = {
        "schema_version": 2,
        "project_name": name,
        "project_slug": slug,
        "workflow_mode": mode,
        "provider": provider,
        "theme": theme,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "created",
        "project_root": str(project_root),
        "workflow_mode": mode,
        "provider": provider,
        "theme": theme,
    }, ensure_ascii=False))
    return project_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create one isolated scroll-world project directory without touching sibling sites."
    )
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--name", required=True, help="Human-facing site or brand name")
    parser.add_argument("--slug", required=True, help="Unique portable directory name")
    parser.add_argument(
        "--mode",
        choices=["detailed", "fast"],
        default="detailed",
        help="Detailed approval/preview workflow or one-pass fast workflow",
    )
    parser.add_argument(
        "--provider",
        choices=["doubao", "higgsfield"],
        help="Generation provider; defaults to the one-time user configuration",
    )
    parser.add_argument(
        "--theme",
        choices=sorted(sw_tool.theme_catalog()),
        default="low-poly-clay",
        help="Visual system copied into world.design",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Allow an existing directory only when its project marker matches",
    )
    args = parser.parse_args()
    provider = args.provider or provider_config.selected_provider()
    if not provider:
        fail("provider is not configured; run provider_config.py configure first", 5)
    initialize(args.workspace_root.expanduser().resolve(), args.name, args.slug, args.mode, provider, args.theme, args.resume)


if __name__ == "__main__":
    main()
