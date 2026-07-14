#!/usr/bin/env python3
"""Resumable Higgsfield CLI adapter with scroll-world budget and cache semantics."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

import sw_tool


SCRIPT_DIR = Path(__file__).resolve().parent
CAPABILITY_CACHE_HOURS = 24


def world_context(world_override: str | None = None) -> tuple[Path, dict, Path]:
    world_path = Path(world_override or os.getenv("SW_WORLD_FILE", "world.json")).resolve()
    world = sw_tool.load_world(world_path)
    ledger = Path(os.getenv("SW_LEDGER_FILE", str(world_path.parent / ".work" / "usage-ledger.json"))).resolve()
    if sw_tool.provider_name(world) != "higgsfield":
        raise SystemExit("higgsfield adapter requires project.provider=higgsfield")
    if not shutil.which("higgsfield"):
        raise SystemExit("official Higgsfield CLI is missing; install it and run 'higgsfield auth login'")
    return world_path, world, ledger


def model_for(world: dict, kind: str, mode: str | None = None) -> tuple[str, str | None]:
    configured = world.get("models") or {}
    if kind == "image":
        alias = configured["image"]
        model = os.getenv("HF_IMAGE_MODEL") or sw_tool.model_value("image", alias, "model_id", "higgsfield")
        size = sw_tool.model_value("image", alias, "default_size", "higgsfield")
        return model, size
    alias = configured["video_preview" if mode == "preview" else "video_final"]
    env_name = "HF_VIDEO_PREVIEW_MODEL" if mode == "preview" else "HF_VIDEO_FINAL_MODEL"
    model = os.getenv(env_name) or sw_tool.model_value("video", alias, "model_id", "higgsfield")
    resolution = sw_tool.model_value("video", alias, "resolution", "higgsfield")
    return model, resolution


def fingerprint(model: str, prompt: Path, inputs: list[Path], params: list[str]) -> str:
    digest = hashlib.sha256()
    for value in ["provider=higgsfield", model, *params]:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for path in [prompt, *inputs]:
        if not path.is_file():
            raise SystemExit(f"missing fingerprint input: {path}")
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sw_tool.sha_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def run_cli(arguments: list[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["higgsfield", *arguments, "--json", "--no-color"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode and not allow_failure:
        detail = (result.stderr or result.stdout or "Higgsfield CLI failed").strip()
        raise RuntimeError(detail)
    return result


def parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Higgsfield CLI did not return valid JSON") from exc


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def first_value(value, keys: list[str]):
    for node in dictionaries(value):
        for key in keys:
            candidate = node.get(key)
            if candidate not in (None, ""):
                return candidate
    return None


def job_id(value) -> str | None:
    candidate = first_value(value, ["job_id", "jobId", "id", "job_set_id", "jobSetId"])
    return str(candidate) if candidate else None


def result_url(value) -> str | None:
    candidate = first_value(value, ["result_url", "resultUrl", "media_url", "video_url", "image_url"])
    if isinstance(candidate, dict):
        candidate = candidate.get("url")
    if isinstance(candidate, str) and candidate:
        return candidate
    for node in dictionaries(value):
        candidate = node.get("url")
        if isinstance(candidate, str) and candidate.startswith(("https://", "http://", "file://")):
            return candidate
    return None


def credits(value) -> float | None:
    candidate = first_value(value, ["credits", "credit_cost", "cost_credits", "estimated_credits"])
    if isinstance(candidate, (int, float)):
        return float(candidate)
    if isinstance(candidate, dict):
        nested = candidate.get("credits") or candidate.get("amount")
        return float(nested) if isinstance(nested, (int, float)) else None
    return None


def estimate_credits(model: str, flags: list[str]) -> float | None:
    result = run_cli(["generate", "cost", model, *flags], allow_failure=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown cost validation error").strip()
        raise RuntimeError(f"Higgsfield cost/schema validation failed before budget reservation: {detail}")
    try:
        raw = parse_json(result.stdout)
    except RuntimeError as exc:
        raise RuntimeError("Higgsfield cost validation returned invalid JSON before budget reservation") from exc
    value = credits(raw)
    if value is None:
        raise RuntimeError("Higgsfield cost validation did not return a credit estimate")
    return value


def usage_payload(raw, estimated: float | None) -> dict:
    actual = credits(raw)
    return {
        "credits": actual if actual is not None else estimated,
        "credits_actual": actual,
        "credits_estimated": estimated,
        "credits_source": "provider_response" if actual is not None else "generate_cost_estimate",
    }


def capability_fingerprint(world: dict) -> str:
    selected = model_for(world, "image") + model_for(world, "video", "preview") + model_for(world, "video", "final")
    value = json.dumps({"models": selected, "generation": world.get("generation")}, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cache_fresh(value: dict, fingerprint_value: str) -> bool:
    if value.get("fingerprint") != fingerprint_value or value.get("status") != "pass":
        return False
    try:
        checked = dt.datetime.fromisoformat(value["checked_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) - checked < dt.timedelta(hours=CAPABILITY_CACHE_HOURS)


def schema_text(value) -> str:
    pieces: list[str] = []
    for node in dictionaries(value):
        pieces.extend(str(key).lower().replace("-", "_") for key in node)
        for item in node.values():
            if isinstance(item, str):
                pieces.append(item.lower().replace("-", "_"))
    return " ".join(pieces)


def require_schema(model: str, raw, groups: list[tuple[str, ...]]) -> None:
    text_value = schema_text(raw)
    missing = ["/".join(group) for group in groups if not any(name in text_value for name in group)]
    if missing:
        raise RuntimeError(f"Higgsfield live schema for {model} is missing required capabilities: {', '.join(missing)}")


def preflight_command(args) -> int:
    world_path, world, _ledger = world_context(args.world)
    cache_path = world_path.parent / ".work" / "provider-capabilities.json"
    fingerprint_value = capability_fingerprint(world)
    cached = sw_tool.read_json(cache_path, {})
    if not args.refresh and cache_fresh(cached, fingerprint_value):
        print(f"Higgsfield capability preflight cached: {cache_path}")
        return 0

    account = run_cli(["account", "status"], allow_failure=True)
    if account.returncode:
        detail = (account.stderr or account.stdout or "not authenticated").strip()
        raise SystemExit(f"Higgsfield authentication preflight failed: {detail}")
    catalog = parse_json(run_cli(["model", "list"]).stdout)
    catalog_text = schema_text(catalog)
    image_model, _ = model_for(world, "image")
    preview_model, _ = model_for(world, "video", "preview")
    final_model, _ = model_for(world, "video", "final")
    models = [image_model, preview_model, final_model]
    missing_models = [model for model in models if model.lower() not in catalog_text]
    if missing_models:
        raise SystemExit("Higgsfield model catalog no longer contains: " + ", ".join(missing_models))

    schemas = {}
    for model in models:
        raw = parse_json(run_cli(["model", "get", model]).stdout)
        if model == image_model:
            require_schema(model, raw, [("prompt",), ("aspect_ratio", "aspect"), ("resolution", "size")])
        else:
            require_schema(model, raw, [("prompt",), ("start_image", "first_frame"), ("duration",), ("resolution", "size")])
        schemas[model] = raw
    sw_tool.atomic_write(cache_path, {
        "schema_version": 1,
        "provider": "higgsfield",
        "status": "pass",
        "fingerprint": fingerprint_value,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "expires_after_hours": CAPABILITY_CACHE_HOURS,
        "models": models,
        "schemas": schemas,
    })
    print(f"Higgsfield capability preflight passed: {cache_path}")
    return 0


def run_tool(arguments: list[str], *, capture=False) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "sw_tool.py"), *arguments],
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.returncode)
    return result.stdout.strip() if capture else ""


def reserve(world_path: Path, ledger: Path, kind: str, mode: str, label: str, model: str, digest: str) -> str:
    return run_tool([
        "reserve", "--world", str(world_path), "--ledger", str(ledger), "--kind", kind,
        "--mode", mode, "--label", label, "--model", model, "--fingerprint", digest,
    ], capture=True)


def fail(ledger: Path, operation: str, reason: str, ambiguous: bool = False) -> None:
    args = ["fail", "--ledger", str(ledger), "--operation-id", operation, "--reason", reason]
    if ambiguous:
        args.append("--ambiguous")
    run_tool(args)


def safe_download(url: str, target: Path) -> None:
    scheme = urllib.parse.urlparse(url).scheme
    if scheme != "https" and not (scheme == "file" and os.getenv("SW_ALLOW_FILE_URLS") == "1"):
        raise RuntimeError(f"refusing non-HTTPS generation result URL: {scheme or 'missing scheme'}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, open(temporary, "wb") as output:
            shutil.copyfileobj(response, output)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def archive(path: Path) -> None:
    if not path.exists():
        return
    index = 1
    target = path.with_name(f"{path.name}.rejected-{index}")
    while target.exists():
        index += 1
        target = path.with_name(f"{path.name}.rejected-{index}")
    shutil.move(str(path), str(target))


def image_command(args) -> int:
    world_path, world, ledger = world_context()
    prompt = Path(args.prompt).resolve()
    output = Path(args.output).resolve()
    references = [Path(item).resolve() for item in args.reference]
    model, size = model_for(world, "image")
    ratio = world["generation"]["ratio"]
    digest = fingerprint(model, prompt, references, [f"resolution={size}", f"aspect_ratio={ratio}"])
    work = world_path.parent / ".work" / "higgsfield-image" / output.name
    meta_path = work / "cache-meta.json"
    response_path = work / "response.json"
    work.mkdir(parents=True, exist_ok=True)
    if args.force:
        archive(output)
        archive(work)
        work.mkdir(parents=True, exist_ok=True)
    meta = sw_tool.read_json(meta_path, {})
    if output.is_file():
        if meta.get("fingerprint") == digest:
            print(f"image cached (fingerprint matched): {output}")
            return 0
        raise SystemExit("stale image cache (use --force to archive and regenerate)")
    previous = sw_tool.read_json(response_path, {})
    if meta.get("fingerprint") == digest and result_url(previous):
        safe_download(result_url(previous), output)
        print(f"image recovered from accepted Higgsfield response: {output}")
        return 0

    flags = ["--prompt", prompt.read_text(encoding="utf-8"), "--aspect_ratio", ratio, "--resolution", str(size)]
    for reference in references:
        flags.extend(["--image", str(reference)])
    try:
        estimated = estimate_credits(model, flags)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    operation = reserve(world_path, ledger, "image", "image", args.label or output.name, model, digest)
    sw_tool.atomic_write(meta_path, {
        "provider": "higgsfield", "fingerprint": digest, "model": model,
        "operation_id": operation, "reference_count": len(references), "estimated_credits": estimated,
        "status": "reserved",
    })
    try:
        result = run_cli(["generate", "create", model, *flags, "--wait", "--wait-timeout", "20m"])
        raw = parse_json(result.stdout)
    except RuntimeError as exc:
        fail(ledger, operation, str(exc), ambiguous=True)
        raise SystemExit(f"Higgsfield image generation failed; reservation kept as ambiguous: {exc}") from exc
    url = result_url(raw)
    if not url:
        fail(ledger, operation, "completed response missing result URL", ambiguous=True)
        raise SystemExit("Higgsfield image response is missing a result URL")
    normalized = {
        "id": job_id(raw), "data": [{"url": url}],
        "usage": usage_payload(raw, estimated),
        "provider_response": raw,
    }
    sw_tool.atomic_write(response_path, normalized)
    run_tool(["accept-image", "--ledger", str(ledger), "--operation-id", operation, "--response", str(response_path)])
    safe_download(url, output)
    meta = sw_tool.read_json(meta_path, {})
    meta["status"] = "succeeded"
    sw_tool.atomic_write(meta_path, meta)
    print(f"Higgsfield image downloaded: {output}")
    return 0


def video_flags(model: str, mode: str, prompt: Path, first: Path, last: Path | None,
                references: list[Path], ratio: str, duration: int, resolution: str) -> list[str]:
    flags = [
        "--prompt", prompt.read_text(encoding="utf-8"), "--start-image", str(first),
        "--aspect_ratio", ratio, "--duration", str(duration), "--resolution", resolution,
        "--generate_audio", "false",
    ]
    if last:
        flags.extend(["--end-image", str(last)])
    for reference in references:
        if reference not in {first, last}:
            flags.extend(["--image", str(reference)])
    if mode == "final" and model == "seedance_2_0":
        flags.extend(["--mode", "std"])
    return flags


def video_submit_command(args) -> int:
    world_path, world, ledger = world_context()
    prompt = Path(args.prompt).resolve()
    first = Path(args.first_frame).resolve()
    last = Path(args.last_frame).resolve() if args.last_frame else None
    references = [Path(item).resolve() for item in args.reference_image]
    task = Path(args.task_dir).resolve()
    model, resolution = model_for(world, "video", args.mode)
    inputs = [first, *([last] if last else []), *references]
    digest = fingerprint(model, prompt, inputs, [
        f"mode={args.mode}", f"resolution={resolution}", f"ratio={args.ratio}", f"duration={args.duration}",
    ])
    if args.force:
        archive(task)
    task.mkdir(parents=True, exist_ok=True)
    task_id_path = task / "task-id.txt"
    meta_path = task / "task-meta.json"
    meta = sw_tool.read_json(meta_path, {})
    if task_id_path.is_file():
        if meta.get("fingerprint") == digest:
            print(f"Higgsfield task cached (fingerprint matched): {task_id_path.read_text().strip()}")
            return 0
        raise SystemExit("stale Higgsfield task cache (use --force to archive and resubmit)")

    flags = video_flags(model, args.mode, prompt, first, last, references, args.ratio, args.duration, resolution)
    try:
        estimated = estimate_credits(model, flags)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    operation = reserve(world_path, ledger, "video", args.mode, args.label or task.name, model, digest)
    try:
        result = run_cli(["generate", "create", model, *flags])
        raw = parse_json(result.stdout)
    except RuntimeError as exc:
        fail(ledger, operation, str(exc), ambiguous=True)
        raise SystemExit(f"Higgsfield video submission failed; reservation kept as ambiguous: {exc}") from exc
    identifier = job_id(raw)
    if not identifier:
        fail(ledger, operation, "submission response missing job id", ambiguous=True)
        raise SystemExit("Higgsfield submission response is missing a job id")
    create_response = task / "create-response.json"
    sw_tool.atomic_write(create_response, {"id": identifier, "request_id": identifier, "provider_response": raw})
    run_tool(["accept-video", "--ledger", str(ledger), "--operation-id", operation, "--response", str(create_response)])
    task_id_path.write_text(identifier + "\n", encoding="utf-8")
    sw_tool.atomic_write(meta_path, {
        "provider": "higgsfield", "fingerprint": digest, "model": model, "mode": args.mode,
        "operation_id": operation, "ratio": args.ratio, "resolution": resolution,
        "duration": args.duration, "reference_count": len(references), "estimated_credits": estimated,
        "status": "submitted", "task_id": identifier,
    })
    print(f"Higgsfield video task submitted: {identifier}")
    return 0


def video_poll_command(args) -> int:
    _world_path, _world, ledger = world_context()
    task = Path(args.task_dir).resolve()
    output = Path(args.output_video).resolve()
    last_frame = Path(args.output_last_frame).resolve()
    task_id_path = task / "task-id.txt"
    meta_path = task / "task-meta.json"
    response_path = task / "status-response.json"
    if not task_id_path.is_file() or not meta_path.is_file():
        raise SystemExit(f"missing Higgsfield task state: {task}")
    meta = sw_tool.read_json(meta_path, {})
    operation = meta.get("operation_id")
    if output.is_file() and last_frame.is_file():
        if response_path.is_file():
            run_tool(["complete-video", "--ledger", str(ledger), "--operation-id", operation, "--response", str(response_path)])
        print(f"Higgsfield video cached: {output}")
        return 0
    identifier = task_id_path.read_text(encoding="utf-8").strip()
    try:
        result = run_cli(["generate", "get", identifier])
        raw = parse_json(result.stdout)
    except RuntimeError as exc:
        raise SystemExit(f"Higgsfield task query failed; job id preserved: {identifier}: {exc}") from exc
    status = str(first_value(raw, ["status", "state"]) or "").lower()
    if status in {"queued", "pending", "running", "in_progress", "processing"}:
        print(f"Higgsfield video task {identifier}: {status}")
        return 10
    if status not in {"completed", "complete", "succeeded", "success"}:
        if status in {"failed", "error", "canceled", "cancelled", "nsfw"}:
            fail(ledger, operation, f"Higgsfield task {status}")
            meta["status"] = status
            sw_tool.atomic_write(meta_path, meta)
            raise SystemExit(f"Higgsfield video task {identifier}: {status}")
        raise SystemExit(f"Higgsfield video task {identifier}: unknown status '{status}'")
    url = result_url(raw)
    if not url:
        raise SystemExit("completed Higgsfield video task is missing a result URL")
    safe_download(url, output)
    last_frame.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-sseof", "-0.04", "-i", str(output),
        "-frames:v", "1", str(last_frame),
    ], check=False)
    if ffmpeg.returncode or not last_frame.is_file():
        raise SystemExit("failed to extract the actual last frame from the Higgsfield video")
    normalized = {
        "status": "succeeded", "id": identifier, "content": {"video_url": url},
        "usage": usage_payload(raw, meta.get("estimated_credits")),
        "provider_response": raw,
    }
    sw_tool.atomic_write(response_path, normalized)
    run_tool(["complete-video", "--ledger", str(ledger), "--operation-id", operation, "--response", str(response_path)])
    meta["status"] = "succeeded"
    sw_tool.atomic_write(meta_path, meta)
    print(f"Higgsfield video task {identifier}: downloaded")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--world")
    preflight.add_argument("--refresh", action="store_true")
    preflight.set_defaults(func=preflight_command)
    image = sub.add_parser("image")
    image.add_argument("prompt")
    image.add_argument("output")
    image.add_argument("--reference", action="append", default=[])
    image.add_argument("--label")
    image.add_argument("--force", action="store_true")
    image.set_defaults(func=image_command)
    submit = sub.add_parser("video-submit")
    submit.add_argument("mode", choices=["preview", "final"])
    submit.add_argument("prompt")
    submit.add_argument("first_frame")
    submit.add_argument("task_dir")
    submit.add_argument("last_frame", nargs="?", default="")
    submit.add_argument("ratio", nargs="?", default="16:9")
    submit.add_argument("duration", nargs="?", default=5, type=int)
    submit.add_argument("--reference-image", action="append", default=[])
    submit.add_argument("--label")
    submit.add_argument("--force", action="store_true")
    submit.set_defaults(func=video_submit_command)
    poll = sub.add_parser("video-poll")
    poll.add_argument("task_dir")
    poll.add_argument("output_video")
    poll.add_argument("output_last_frame")
    poll.set_defaults(func=video_poll_command)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
