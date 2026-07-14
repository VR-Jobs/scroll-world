#!/usr/bin/env python3
"""Resumable orchestrator for Seedream/Seedance scroll-world projects."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import sw_tool
import provider_config
from providers import get_provider


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_project(world_path: str):
    path = Path(world_path).resolve()
    world = sw_tool.read_json(path, {})
    errors = sw_tool.validate_world(world)
    if errors:
        raise SystemExit("invalid world.json:\n- " + "\n- ".join(errors))
    return path, path.parent, world


def resolve(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if root != path and root not in path.parents:
        raise SystemExit(f"path escapes project root: {value}")
    return path


def approvals(root: Path) -> dict:
    return sw_tool.read_json(root / ".work" / "approvals.json", {"anchor": False, "images": False, "preview": False})


def write_approval(root: Path, gate: str, value: bool, note: str | None, fingerprint: str | None = None) -> None:
    path = root / ".work" / "approvals.json"
    data = approvals(root)
    data[gate] = value
    data[f"{gate}_updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if value and fingerprint:
        data[f"{gate}_fingerprint"] = fingerprint
    elif not value:
        data.pop(f"{gate}_fingerprint", None)
    if note:
        data[f"{gate}_note"] = note
    sw_tool.atomic_write(path, data)


def stills_fingerprint(root: Path, world: dict, anchor_only: bool = False) -> str | None:
    sections = world["sections"][:1] if anchor_only else world["sections"]
    digest = hashlib.sha256()
    for section in sections:
        value = section["outputs"]["still"]
        path = resolve(root, value)
        if not path.is_file():
            return None
        for item in (section["id"], value, sw_tool.sha_file(path)):
            digest.update(item.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def approval_valid(root: Path, world: dict, gate: str) -> bool:
    data = approvals(root)
    if not data.get(gate):
        return False
    if gate == "anchor":
        current = stills_fingerprint(root, world, anchor_only=True)
        return current is not None and data.get("anchor_fingerprint") == current
    if gate == "images":
        current = stills_fingerprint(root, world)
        return current is not None and data.get("images_fingerprint") == current
    return True


def invoke(command: list[str], root: Path, world_path: Path, dry_run=False, allowed=(0,)) -> int:
    printable = " ".join(json.dumps(part, ensure_ascii=False) for part in command)
    if dry_run:
        print(f"DRY-RUN {printable}")
        return 0
    env = os.environ.copy()
    env["SW_WORLD_FILE"] = str(world_path)
    env.setdefault("SW_LEDGER_FILE", str(root / ".work" / "usage-ledger.json"))
    world = sw_tool.read_json(world_path, {})
    env.update(provider_config.runtime_env(sw_tool.provider_name(world)))
    models = model_plan(world)
    env.setdefault("ARK_IMAGE_MODEL", models["image"])
    env.setdefault("ARK_VIDEO_PREVIEW_MODEL", models["preview"])
    env.setdefault("ARK_VIDEO_FINAL_MODEL", models["final"])
    result = subprocess.run(command, cwd=root, env=env, check=False)
    if result.returncode not in allowed:
        raise SystemExit(result.returncode)
    return result.returncode


def model_plan(world: dict) -> dict:
    return get_provider(sw_tool.provider_name(world), SCRIPT_DIR).model_plan(world)


def cmd_init(args) -> int:
    root = Path(args.project).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "world.json"
    if target.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite {target}; pass --force")
    template = sw_tool.read_json(SKILL_DIR / "assets" / "world.example.json", {})
    provider = args.provider or provider_config.selected_provider()
    if not provider:
        raise SystemExit("configure a provider first with provider_config.py configure --provider doubao|higgsfield")
    template["project"]["brand"] = args.brand
    template["project"]["provider"] = provider
    template["project"]["workflow_mode"] = args.mode
    template["project"]["theme"] = args.theme
    template["design"] = sw_tool.load_theme(args.theme)
    template["generation"]["preview_enabled"] = args.mode == "detailed"
    template["quality"]["require_semantic_approval"] = args.mode == "detailed"
    template["models"] = get_provider(provider, SCRIPT_DIR).default_model_aliases()
    sw_tool.atomic_write(target, template)
    for name in [".work", "prompts", "assets", "assets/vid"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    print(f"initialized: {target}")
    return 0


def planned_counts(world: dict) -> dict:
    sections = len(world["sections"])
    transitions = len(world.get("transitions") or []) if world["project"]["architecture"] == "B" else 0
    video_per_mode = sections + transitions
    preview = video_per_mode if world["generation"].get("preview_enabled", True) else 0
    return {"images": sections, "preview_videos": preview, "final_videos": video_per_mode, "total": sections + preview + video_per_mode}


def cmd_plan(args) -> int:
    _, _, world = load_project(args.world)
    counts = planned_counts(world)
    models = model_plan(world)
    print(json.dumps({
        "brand": world["project"]["brand"],
        "provider": sw_tool.provider_name(world),
        "workflow_mode": sw_tool.workflow_mode(world),
        "architecture": world["project"]["architecture"],
        "models": models,
        "planned_requests_without_retries": counts,
        "generation_limit": world["project"]["generation_limit"],
        "retry_reserve": world["project"]["generation_limit"] - counts["total"],
    }, ensure_ascii=False, indent=2))
    if counts["total"] > world["project"]["generation_limit"]:
        print("ERROR: planned requests exceed generation_limit", file=sys.stderr)
        return 2
    if counts["total"] == world["project"]["generation_limit"]:
        print("WARNING: no retry budget remains", file=sys.stderr)
    return 0


def doctor_payload(world_path_value: str | None = None, refresh: bool = False) -> tuple[dict, bool]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"name": name, "status": "pass" if ok else "fail" if required else "warn", "detail": detail})

    for command in ["python3", "curl", "jq", "ffmpeg", "ffprobe"]:
        found = shutil.which(command)
        add(f"dependency:{command}", found is not None, found or "not found")

    selected = provider_config.selected_provider()
    world = None
    world_path = None
    if world_path_value:
        world_path, _root, world = load_project(world_path_value)
        selected = sw_tool.provider_name(world)
        counts = planned_counts(world)
        add("world:budget", counts["total"] <= world["project"]["generation_limit"], f"planned={counts['total']} limit={world['project']['generation_limit']}")
        captured = (world.get("pricing") or {}).get("captured_at")
        add("pricing:snapshot", bool(captured), captured or "missing; monetary totals will be unavailable", required=False)
    add("provider:selected", selected in {"doubao", "higgsfield"}, selected or "not configured")

    if selected == "doubao":
        add("doubao:credential", provider_config.doubao_api_key() is not None, "stored/environment key present" if provider_config.doubao_api_key() else "missing")
    elif selected == "higgsfield":
        installed = shutil.which("higgsfield")
        add("higgsfield:cli", installed is not None, installed or "not installed")
        authenticated, reason = provider_config.higgsfield_auth_status()
        add("higgsfield:auth", authenticated, "authenticated" if authenticated else reason or "not authenticated")
        if authenticated and world_path and world:
            provider = get_provider("higgsfield", SCRIPT_DIR)
            command = provider.preflight_command(world_path) or []
            if refresh:
                command.append("--refresh")
            env = os.environ.copy()
            env["SW_WORLD_FILE"] = str(world_path)
            env.setdefault("SW_LEDGER_FILE", str(world_path.parent / ".work" / "usage-ledger.json"))
            result = subprocess.run(command, cwd=world_path.parent, env=env, text=True, capture_output=True, check=False)
            detail = (result.stdout or result.stderr or "no output").strip().splitlines()[-1]
            add("higgsfield:capabilities", result.returncode == 0, detail)

    if world_path:
        browser_report = world_path.parent / ".work" / "qa" / "browser-report.json"
        add("browser:qa-evidence", browser_report.is_file(), str(browser_report) if browser_report.is_file() else "not recorded yet", required=False)
    ok = all(item["status"] != "fail" for item in checks)
    return {"schema_version": 1, "status": "pass" if ok else "fail", "provider": selected, "checks": checks}, ok


def cmd_doctor(args) -> int:
    payload, ok = doctor_payload(args.world, args.refresh)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 2


def cmd_setup(args) -> int:
    if args.status:
        return provider_config.cmd_status(argparse.Namespace(json=True))
    if not args.provider:
        raise SystemExit("setup requires --provider doubao|higgsfield; ask the user before choosing")
    provider_config.cmd_configure(argparse.Namespace(
        provider=args.provider,
        replace=args.replace,
        api_key_env=args.api_key_env,
    ))
    payload, ok = doctor_payload(None, False)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 2


def cmd_themes(_args) -> int:
    print(json.dumps(sw_tool.theme_catalog(), ensure_ascii=False, indent=2))
    return 0


def cmd_migrate(args) -> int:
    command = [sys.executable, str(SCRIPT_DIR / "migrate-project.py"), "--world", args.world, "--theme", args.theme]
    if args.dry_run:
        command.append("--dry-run")
    return subprocess.run(command, check=False).returncode


def references_for_still(root: Path, section: dict, anchor: Path | None) -> list[Path]:
    refs = []
    if anchor is not None:
        refs.append(anchor)
    for value in section.get("reference_images") or []:
        path = resolve(root, value)
        if path not in refs:
            refs.append(path)
    return refs


def ensure_stills(world_path: Path, root: Path, world: dict, dry_run: bool) -> str:
    provider = get_provider(sw_tool.provider_name(world), SCRIPT_DIR)
    image_command = provider.image_command()
    sections = world["sections"]
    anchor = resolve(root, sections[0]["outputs"]["still"])
    first = sections[0]
    if not anchor.is_file():
        command = [*image_command, str(resolve(root, first["still_prompt"])), str(anchor), "--label", f"still:{first['id']}"]
        for ref in references_for_still(root, first, None):
            command.extend(["--reference", str(ref)])
        invoke(command, root, world_path, dry_run)
        if dry_run:
            return "would-generate-anchor"
    detailed = sw_tool.workflow_mode(world) == "detailed"
    if detailed and not approval_valid(root, world, "anchor"):
        print(f"GATE anchor: show {anchor} to the user; confirm overall style, palette, composition and product fidelity, then run approve anchor")
        return "gate-anchor"
    for section in sections[1:]:
        output = resolve(root, section["outputs"]["still"])
        if output.is_file():
            continue
        command = [*image_command, str(resolve(root, section["still_prompt"])), str(output), "--label", f"still:{section['id']}"]
        for ref in references_for_still(root, section, anchor):
            command.extend(["--reference", str(ref)])
        invoke(command, root, world_path, dry_run)
        if dry_run:
            return f"would-generate-still:{section['id']}"
    if detailed and not approval_valid(root, world, "images"):
        review = [{"id": section["id"], "still": section["outputs"]["still"]} for section in sections]
        print("GATE images: show every generated image to the user and collect one batch decision")
        print(json.dumps(review, ensure_ascii=False, indent=2))
        print("If any image is rejected, revise only its prompt/references, run retry --stage still --id <scene>, regenerate it, and review the full batch again. When all pass, run approve images.")
        return "gate-images"
    return "done"


def video_outputs(node: dict, mode: str):
    outputs = node["outputs"]
    if mode == "preview":
        return outputs.get("raw_preview"), outputs.get("preview_last_frame")
    return outputs.get("raw_final"), outputs.get("final_last_frame")


def video_mode_complete(root: Path, world: dict, mode: str) -> bool:
    nodes = list(world["sections"])
    if world["project"]["architecture"] == "B":
        nodes.extend(world.get("transitions") or [])
    for node in nodes:
        raw_value, last_value = video_outputs(node, mode)
        if not raw_value or not last_value:
            return False
        if not resolve(root, raw_value).is_file() or not resolve(root, last_value).is_file():
            return False
    return True


def task_dir(root: Path, mode: str, kind: str, item_id: str) -> Path:
    prefix = "video" if kind == "section" else "connector"
    return root / ".work" / f"{prefix}-{mode}-{item_id}"


def progress_video(
    world_path: Path,
    root: Path,
    world: dict,
    node: dict,
    mode: str,
    kind: str,
    first_frame: Path,
    last_frame: Path | None,
    reference_images: list[Path],
    dry_run: bool,
) -> str:
    raw_value, last_value = video_outputs(node, mode)
    if not raw_value or not last_value:
        raise SystemExit(f"{kind} {node['id']} missing {mode} output paths")
    raw = resolve(root, raw_value)
    returned_last = resolve(root, last_value)
    task = task_dir(root, mode, kind, node["id"])
    if raw.is_file() and returned_last.is_file():
        return "done"
    if (task / "task-id.txt").is_file():
        provider = get_provider(sw_tool.provider_name(world), SCRIPT_DIR)
        command = [*provider.video_poll_command(), str(task), str(raw), str(returned_last)]
        code = invoke(command, root, world_path, dry_run, allowed=(0, 10))
        return "pending" if dry_run or code == 10 else "done"
    submitter = get_provider(sw_tool.provider_name(world), SCRIPT_DIR).video_submit_command()
    command = [
        *submitter, mode,
        str(resolve(root, node["video_prompt"])), str(first_frame), str(task),
        str(last_frame) if last_frame else "", world["generation"]["ratio"], str(world["generation"]["duration_seconds"]),
        "--label", f"{kind}:{mode}:{node['id']}",
    ]
    for reference in reference_images:
        if reference != first_frame and reference != last_frame:
            command.extend(["--reference-image", str(reference)])
    invoke(command, root, world_path, dry_run)
    return "pending"


def extract_first_frame(world_path: Path, root: Path, source: Path, target: Path, dry_run: bool) -> None:
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    invoke(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(target)], root, world_path, dry_run)


def run_video_mode(world_path: Path, root: Path, world: dict, mode: str, dry_run: bool) -> str:
    architecture = world["project"]["architecture"]
    sections = world["sections"]
    if architecture == "A":
        previous_last = None
        for index, section in enumerate(sections):
            still = resolve(root, section["outputs"]["still"])
            first = still if index == 0 else previous_last
            if first is None or (not dry_run and not first.is_file()):
                raise SystemExit(f"missing chain first frame for {section['id']}: {first}")
            state = progress_video(world_path, root, world, section, mode, "section", first, None, [still], dry_run)
            if state != "done":
                return state
            _, last_value = video_outputs(section, mode)
            previous_last = resolve(root, last_value)
        return "done"

    # Architecture B: independent dives, followed by endpoint-constrained connectors.
    for section in sections:
        still = resolve(root, section["outputs"]["still"])
        state = progress_video(world_path, root, world, section, mode, "section", still, None, [], dry_run)
        if state != "done":
            return state
    transitions = world.get("transitions") or []
    for index, transition in enumerate(transitions):
        current = sections[index]
        following = sections[index + 1]
        _, current_last_value = video_outputs(current, mode)
        following_raw_value, _ = video_outputs(following, mode)
        current_last = resolve(root, current_last_value)
        following_raw = resolve(root, following_raw_value)
        following_first = root / ".work" / "first-frames" / f"{mode}-{following['id']}.png"
        extract_first_frame(world_path, root, following_raw, following_first, dry_run)
        state = progress_video(world_path, root, world, transition, mode, "transition", current_last, following_first, [], dry_run)
        if state != "done":
            return state
    return "done"


def qa_passed(root: Path) -> bool:
    report = sw_tool.read_json(root / ".work" / "qa" / "semantic-report.json", {})
    items = report.get("sections") or {}
    return bool(items) and all(item.get("status") == "pass" for item in items.values())


def cmd_run(args) -> int:
    world_path, root, world = load_project(args.world)
    mode = sw_tool.workflow_mode(world)
    print(f"provider: {sw_tool.provider_name(world)}")
    print(f"workflow mode: {mode}")
    counts = planned_counts(world)
    if counts["total"] > world["project"]["generation_limit"]:
        raise SystemExit("planned generation requests exceed budget; edit world.json first")
    preflight = get_provider(sw_tool.provider_name(world), SCRIPT_DIR).preflight_command(world_path)
    if preflight:
        invoke(preflight, root, world_path, args.dry_run)
    state = ensure_stills(world_path, root, world, args.dry_run)
    if state != "done":
        return 20 if state.startswith("gate") else 0
    if world["generation"].get("preview_enabled", True):
        state = run_video_mode(world_path, root, world, "preview", args.dry_run)
        if state != "done":
            print("preview task submitted/running; rerun with --resume later")
            return 10 if not args.dry_run else 0
        if not approvals(root).get("preview"):
            print("GATE preview: inspect the full preview chain then run approve preview")
            return 20
    state = run_video_mode(world_path, root, world, "final", args.dry_run)
    if state != "done":
        print("final task submitted/running; rerun with --resume later")
        return 10 if not args.dry_run else 0
    if args.dry_run:
        print("DRY-RUN finalization: media encode -> semantic QA -> production build -> cost report")
        return 0
    invoke([sys.executable, str(SCRIPT_DIR / "media-pipeline.py"), "--world", str(world_path)], root, world_path)
    invoke([sys.executable, str(SCRIPT_DIR / "qa-assets.py"), "prepare", "--world", str(world_path)], root, world_path)
    if world.get("quality", {}).get("require_semantic_approval", True) and not qa_passed(root):
        print("GATE semantic QA: inspect .work/qa contact sheets and record every section review")
        return 20
    if world.get("quality", {}).get("require_browser_qa", True):
        browser = [sys.executable, str(SCRIPT_DIR / "browser-qa.py")]
        code = invoke([*browser, "check", "--world", str(world_path)], root, world_path, allowed=(0, 4))
        if code == 4:
            invoke([*browser, "prepare", "--world", str(world_path)], root, world_path)
            print("GATE browser QA: run every file/http and desktop/mobile/tablet check, then record browser evidence")
            return 20
    invoke([sys.executable, str(SCRIPT_DIR / "build-production.py"), "--world", str(world_path)], root, world_path)
    invoke([sys.executable, str(SCRIPT_DIR / "sw_tool.py"), "report", "--world", str(world_path),
            "--ledger", str(root / ".work" / "usage-ledger.json"),
            "--json-output", str(root / ".work" / "cost-report.json"),
            "--markdown-output", str(root / "COSTS.md")], root, world_path)
    print("scroll world complete: production build and COSTS.md are ready")
    return 0


def cmd_approve(args) -> int:
    _, root, world = load_project(args.world)
    if sw_tool.workflow_mode(world) != "detailed":
        raise SystemExit("approval gates are disabled in fast mode; use targeted retry after final review")
    current = approvals(root)
    fingerprint = None
    if args.gate == "anchor":
        fingerprint = stills_fingerprint(root, world, anchor_only=True)
        if fingerprint is None:
            raise SystemExit("cannot approve anchor before the first generated image exists")
        if current.get("anchor_fingerprint") != fingerprint:
            write_approval(root, "images", False, "invalidated by changed anchor approval")
            write_approval(root, "preview", False, "invalidated by changed anchor approval")
    elif args.gate == "images":
        if not approval_valid(root, world, "anchor"):
            raise SystemExit("cannot approve the image batch before the current anchor is approved")
        fingerprint = stills_fingerprint(root, world)
        if fingerprint is None:
            raise SystemExit("cannot approve images before every section still exists")
        if current.get("images_fingerprint") != fingerprint:
            write_approval(root, "preview", False, "invalidated by changed image batch approval")
    elif args.gate == "preview":
        if not approval_valid(root, world, "images"):
            raise SystemExit("cannot approve preview before the current image batch is approved")
        if not video_mode_complete(root, world, "preview"):
            raise SystemExit("cannot approve preview before every preview video and returned last frame exists")
    write_approval(root, args.gate, True, args.note, fingerprint)
    print(f"approved: {args.gate}")
    return 0


def find_node(world: dict, item_id: str):
    for kind, nodes in [("section", world["sections"]), ("transition", world.get("transitions") or [])]:
        for node in nodes:
            if node.get("id") == item_id:
                return kind, node
    raise SystemExit(f"unknown section/transition id: {item_id}")


def move_if_exists(source: Path, archive: Path) -> None:
    if source.exists():
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / source.name
        suffix = 1
        while target.exists():
            target = archive / f"{source.name}.{suffix}"
            suffix += 1
        shutil.move(str(source), str(target))


def node_kind(world: dict, node: dict) -> str:
    return "section" if any(item is node for item in world["sections"]) else "transition"


def affected_video_nodes(world: dict, kind: str, node: dict) -> list[tuple[str, dict]]:
    if world["project"]["architecture"] == "A":
        if kind != "section":
            return [(kind, node)]
        index = next(index for index, item in enumerate(world["sections"]) if item is node)
        return [("section", item) for item in world["sections"][index:]]
    if kind == "transition":
        return [(kind, node)]
    sections = world["sections"]
    transitions = world.get("transitions") or []
    index = next(index for index, item in enumerate(sections) if item is node)
    affected: list[tuple[str, dict]] = [("section", node)]
    if index > 0:
        affected.append(("transition", transitions[index - 1]))
    if index < len(transitions):
        affected.append(("transition", transitions[index]))
    return affected


def retry_plan(root: Path, world: dict, kind: str, node: dict, stage: str) -> list[Path]:
    paths: list[Path] = []
    if stage == "still":
        output = resolve(root, node["outputs"]["still"])
        key = re.sub(r"[^A-Za-z0-9._-]", "_", output.name)
        paths.extend([
            output,
            root / ".work" / "ark-image" / key,
            root / ".work" / "higgsfield-image" / output.name,
        ])
        modes = (["preview"] if world["generation"].get("preview_enabled", True) else []) + ["final"]
    else:
        modes = [stage]

    for affected_kind, affected in affected_video_nodes(world, kind, node):
        for mode in modes:
            raw_value, last_value = video_outputs(affected, mode)
            if raw_value:
                paths.append(resolve(root, raw_value))
            if last_value:
                paths.append(resolve(root, last_value))
            paths.append(task_dir(root, mode, affected_kind, affected["id"]))
            paths.append(root / ".work" / "first-frames" / f"{mode}-{affected['id']}.png")
            if mode == "final":
                for field in ["final_video", "mobile_video", "poster", "mobile_poster"]:
                    value = (affected.get("outputs") or {}).get(field)
                    if value:
                        paths.append(resolve(root, value))

    qa = root / ".work" / "qa"
    paths.extend([
        qa / "media-report.json",
        qa / "semantic-report.json",
        qa / "browser-report.json",
        root / ".work" / "cost-report.json",
        root / "COSTS.md",
        resolve(root, world["delivery"]["output_dir"]),
    ])
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def cmd_retry(args) -> int:
    _, root, world = load_project(args.world)
    kind, node = find_node(world, args.id)
    if args.stage == "still" and kind != "section":
        raise SystemExit("transitions do not have stills")
    planned = retry_plan(root, world, kind, node, args.stage)
    affected = [str(path.relative_to(root)) for path in planned if path.exists()]
    if args.explain:
        print(json.dumps({
            "stage": args.stage,
            "id": args.id,
            "architecture": world["project"]["architecture"],
            "existing_paths_to_archive": affected,
        }, ensure_ascii=False, indent=2))
        return 0
    archive = root / ".work" / "rejected" / f"{utc_stamp()}-{args.stage}-{args.id}"
    for path in planned:
        move_if_exists(path, archive)
    if args.stage == "still":
        write_approval(root, "images", False, f"invalidated by still retry: {args.id}")
        write_approval(root, "preview", False, f"invalidated by still retry: {args.id}")
        if node is world["sections"][0]:
            write_approval(root, "anchor", False, "invalidated by anchor retry")
    elif args.stage == "preview":
        write_approval(root, "preview", False, f"invalidated by preview retry: {args.id}")
    print(f"retry prepared; previous evidence archived at {archive}")
    return 0


def cmd_status(args) -> int:
    _, root, world = load_project(args.world)
    ledger = sw_tool.read_json(root / ".work" / "usage-ledger.json", {"operations": {}})
    ops = ledger.get("operations") or {}
    approval_state = approvals(root)
    approval_state["anchor_valid"] = approval_valid(root, world, "anchor")
    approval_state["images_valid"] = approval_valid(root, world, "images")
    output = {
        "provider": sw_tool.provider_name(world),
        "workflow_mode": sw_tool.workflow_mode(world),
        "approvals": approval_state,
        "budget": {
            "used_or_reserved": sum(sw_tool.budget_slots(op) for op in ops.values()),
            "accepted": sum(int(op.get("request_count") or 0) for op in ops.values()),
            "limit": world["project"]["generation_limit"],
        },
        "sections": {},
    }
    for section in world["sections"]:
        output["sections"][section["id"]] = {
            key: resolve(root, value).is_file()
            for key, value in section["outputs"].items()
            if isinstance(value, str)
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--project", default=".")
    p.add_argument("--brand", required=True)
    p.add_argument("--provider", choices=["doubao", "higgsfield"])
    p.add_argument("--mode", choices=["detailed", "fast"], default="detailed")
    p.add_argument("--theme", choices=sorted(sw_tool.theme_catalog()), default="low-poly-clay")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("setup")
    p.add_argument("--provider", choices=["doubao", "higgsfield"])
    p.add_argument("--api-key-env", help="Test/automation only; interactive use keeps key input hidden")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--status", action="store_true")
    p.set_defaults(func=cmd_setup)
    p = sub.add_parser("themes")
    p.set_defaults(func=cmd_themes)
    p = sub.add_parser("migrate")
    p.add_argument("--world", default="world.json")
    p.add_argument("--theme", choices=sorted(sw_tool.theme_catalog()), default="low-poly-clay")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_migrate)
    p = sub.add_parser("doctor")
    p.add_argument("--world")
    p.add_argument("--refresh", action="store_true", help="Refresh provider capability cache")
    p.set_defaults(func=cmd_doctor)
    for name, func in [("plan", cmd_plan), ("status", cmd_status)]:
        p = sub.add_parser(name)
        p.add_argument("--world", default="world.json")
        p.set_defaults(func=func)
    p = sub.add_parser("run")
    p.add_argument("--world", default="world.json")
    p.add_argument("--resume", action="store_true", help="explicitly document that an existing run is resumed")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("approve")
    p.add_argument("gate", choices=["anchor", "images", "preview"])
    p.add_argument("--world", default="world.json")
    p.add_argument("--note")
    p.set_defaults(func=cmd_approve)
    p = sub.add_parser("retry")
    p.add_argument("--world", default="world.json")
    p.add_argument("--stage", choices=["still", "preview", "final"], required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--explain", action="store_true", help="Show all downstream assets that would be invalidated")
    p.set_defaults(func=cmd_retry)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
