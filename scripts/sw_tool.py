#!/usr/bin/env python3
"""Low-level state, budget and cost utilities for scroll-world."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


SCRIPT_DIR = Path(__file__).resolve().parent
MODELS_FILE = SCRIPT_DIR.parent / "references" / "models.json"
THEMES_FILE = SCRIPT_DIR.parent / "assets" / "themes" / "index.json"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_world(path: Path) -> dict:
    world = read_json(path, {}) or {}
    world.setdefault("project", {})
    world["project"].setdefault("provider", "doubao")
    world["project"].setdefault("workflow_mode", "detailed")
    world["project"].setdefault("generation_limit", int(os.getenv("SW_GENERATION_LIMIT", "20")))
    world.setdefault("pricing", {})
    return world


def workflow_mode(world: dict) -> str:
    return (world.get("project") or {}).get("workflow_mode", "detailed")


def provider_name(world: dict) -> str:
    return (world.get("project") or {}).get("provider", "doubao")


def theme_catalog() -> dict:
    value = read_json(THEMES_FILE, {}) or {}
    return value.get("themes") or {}


def load_theme(name: str) -> dict:
    theme = theme_catalog().get(name)
    if not isinstance(theme, dict):
        raise SystemExit(f"unknown theme: {name}; choose from {', '.join(sorted(theme_catalog()))}")
    return json.loads(json.dumps(theme))


@contextlib.contextmanager
def locked_ledger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{path}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        ledger = read_json(path, {"schema_version": 1, "operations": {}})
        ledger.setdefault("schema_version", 1)
        ledger.setdefault("operations", {})
        yield ledger
        atomic_write(path, ledger)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_fingerprint(args) -> int:
    digest = hashlib.sha256()
    for value in [args.model, *args.param]:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for name in [args.prompt, *args.input]:
        path = Path(name)
        if not path.is_file():
            raise SystemExit(f"missing fingerprint input: {path}")
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha_file(path).encode("ascii"))
        digest.update(b"\0")
    print(digest.hexdigest())
    return 0


def provider_models(provider: str) -> dict:
    models = read_json(MODELS_FILE, {})
    providers = models.get("providers")
    if isinstance(providers, dict):
        node = providers.get(provider)
        if not isinstance(node, dict):
            raise SystemExit(f"unknown provider model registry: {provider}")
        return node
    if provider == "doubao":
        return models
    raise SystemExit(f"model registry does not define provider: {provider}")


def model_value(kind: str, alias: str, field: str, provider: str = "doubao"):
    models = provider_models(provider)
    if kind == "image":
        if alias == "default":
            alias = models["image"]["default"]
        node = models["image"].get(alias)
    else:
        node = models["video"].get(alias)
    if not node or field not in node:
        raise SystemExit(f"unknown model lookup: {kind}.{alias}.{field}")
    return node[field]


def cmd_model(args) -> int:
    value = model_value(args.kind, args.alias, args.field, args.provider)
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(value)
    return 0


def cmd_themes(_args) -> int:
    print(json.dumps(theme_catalog(), ensure_ascii=False, indent=2))
    return 0


def budget_slots(op: dict) -> int:
    if op.get("status") in {"released", "rejected"}:
        return 0
    return 1 if op.get("kind") in {"image", "video"} else 0


def cmd_reserve(args) -> int:
    ledger_path = Path(args.ledger)
    world = load_world(Path(args.world))
    limit = int(world["project"]["generation_limit"])
    with locked_ledger(ledger_path) as ledger:
        used = sum(budget_slots(op) for op in ledger["operations"].values())
        if used >= limit:
            raise SystemExit(f"generation budget exhausted: {used}/{limit}")
        operation_id = str(uuid.uuid4())
        ledger["operations"][operation_id] = {
            "provider": provider_name(world),
            "kind": args.kind,
            "mode": args.mode,
            "label": args.label,
            "model": args.model,
            "fingerprint": args.fingerprint,
            "status": "reserved",
            "reserved_at": now(),
            "request_count": 0,
        }
        used += 1
    print(operation_id)
    print(f"budget reserved: {used}/{limit}", file=sys.stderr)
    return 0


def get_op(ledger: dict, operation_id: str) -> dict:
    try:
        return ledger["operations"][operation_id]
    except KeyError as exc:
        raise SystemExit(f"unknown operation id: {operation_id}") from exc


def response_usage(response: dict) -> dict:
    usage = response.get("usage") or response.get("Usage") or {}
    return usage if isinstance(usage, dict) else {}


def cmd_accept_image(args) -> int:
    response = read_json(Path(args.response), {})
    outputs = response.get("data") if isinstance(response.get("data"), list) else []
    with locked_ledger(Path(args.ledger)) as ledger:
        op = get_op(ledger, args.operation_id)
        op.update({
            "status": "succeeded",
            "accepted_at": now(),
            "completed_at": now(),
            "request_count": 1,
            "output_count": len(outputs) or 1,
            "provider_request_id": response.get("request_id") or response.get("id"),
            "usage": response_usage(response),
        })
    return 0


def cmd_accept_video(args) -> int:
    response = read_json(Path(args.response), {})
    task_id = response.get("id")
    if not task_id:
        raise SystemExit("video response missing id")
    with locked_ledger(Path(args.ledger)) as ledger:
        op = get_op(ledger, args.operation_id)
        op.update({
            "status": "submitted",
            "accepted_at": now(),
            "request_count": 1,
            "provider_task_id": task_id,
            "provider_request_id": response.get("request_id"),
        })
    print(task_id)
    return 0


def cmd_complete_video(args) -> int:
    response = read_json(Path(args.response), {})
    with locked_ledger(Path(args.ledger)) as ledger:
        op = get_op(ledger, args.operation_id)
        op.update({
            "status": "succeeded",
            "completed_at": now(),
            "output_count": 1,
            "usage": response_usage(response),
        })
    return 0


def cmd_fail(args) -> int:
    with locked_ledger(Path(args.ledger)) as ledger:
        op = get_op(ledger, args.operation_id)
        if args.ambiguous:
            op.update({"status": "ambiguous", "request_count": 1, "failed_at": now()})
        elif op.get("request_count"):
            op.update({"status": "failed", "failed_at": now()})
        else:
            op.update({"status": "released", "released_at": now()})
        if args.reason:
            op["reason"] = args.reason
    return 0


def numeric(node, *keys):
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return None
    return node if isinstance(node, (int, float)) else None


def completion_tokens(op: dict) -> int | None:
    usage = op.get("usage") or {}
    value = usage.get("completion_tokens")
    if value is None:
        value = usage.get("CompletionTokens")
    if value is None:
        value = usage.get("total_tokens")
    return int(value) if isinstance(value, (int, float)) else None


def image_output_tokens(op: dict) -> int | None:
    usage = op.get("usage") or {}
    value = usage.get("output_tokens")
    if value is None:
        value = usage.get("OutputTokens")
    if value is None:
        value = usage.get("total_tokens")
    return int(value) if isinstance(value, (int, float)) else None


def usage_credits(op: dict) -> float | None:
    usage = op.get("usage") or {}
    actual = usage.get("credits_actual")
    if isinstance(actual, (int, float)):
        return float(actual)
    for key in ["credits", "credit_cost", "cost_credits", "estimated_credits"]:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def usage_credit_field(op: dict, field: str) -> float | None:
    value = (op.get("usage") or {}).get(field)
    return float(value) if isinstance(value, (int, float)) else None


def money(value):
    return None if value is None else round(float(value), 6)


def build_report(world: dict, ledger: dict) -> dict:
    provider = provider_name(world)
    pricing = world.get("pricing") or {}
    image_ops = [op for op in ledger["operations"].values() if op.get("kind") == "image" and op.get("request_count")]
    video_ops = [op for op in ledger["operations"].values() if op.get("kind") == "video" and op.get("request_count")]
    image_outputs = sum(int(op.get("output_count") or 0) for op in image_ops)
    image_price = pricing.get("image") or {}
    image_rate = numeric(image_price, "cny_per_output")
    image_token_rate = numeric(image_price, "cny_per_million_output_tokens")
    image_token_values = [image_output_tokens(op) for op in image_ops]
    image_tokens = sum(value for value in image_token_values if value is not None)
    image_credit_values = [usage_credits(op) for op in image_ops]
    image_credits = sum(value for value in image_credit_values if value is not None)
    image_actual_values = [usage_credit_field(op, "credits_actual") for op in image_ops]
    image_estimated_values = [usage_credit_field(op, "credits_estimated") for op in image_ops]
    image_actual = sum(value for value in image_actual_values if value is not None)
    image_estimated = sum(value for value in image_estimated_values if value is not None)
    image_unit = image_price.get("billing_unit")
    if not image_unit:
        image_unit = "output" if image_rate is not None else "million_output_tokens" if image_token_rate is not None else "unknown"
    if image_outputs == 0:
        image_cost = 0.0
    elif image_unit == "output" and image_rate is not None:
        image_cost = money(image_outputs * image_rate)
    elif image_unit == "million_output_tokens" and image_token_rate is not None and all(value is not None for value in image_token_values):
        image_cost = money(image_tokens / 1_000_000 * image_token_rate)
    else:
        image_cost = None
    if provider == "higgsfield":
        credit_rate = numeric(pricing, "higgsfield", "cny_per_credit")
        image_unit = "credits"
        image_cost = money(image_actual * credit_rate) if credit_rate is not None and all(value is not None for value in image_actual_values) else None
        image_estimated_cost = money(image_estimated * credit_rate) if credit_rate is not None and all(value is not None for value in image_estimated_values) else None
    else:
        image_estimated_cost = None

    video_modes = {}
    video_cost_total = 0.0
    all_video_cost_known = True
    for mode in sorted({op.get("mode", "unknown") for op in video_ops}):
        ops = [op for op in video_ops if op.get("mode", "unknown") == mode]
        tokens = [completion_tokens(op) for op in ops]
        known_tokens = sum(token for token in tokens if token is not None)
        credit_values = [usage_credits(op) for op in ops]
        known_credits = sum(value for value in credit_values if value is not None)
        actual_values = [usage_credit_field(op, "credits_actual") for op in ops]
        estimated_values = [usage_credit_field(op, "credits_estimated") for op in ops]
        actual_credits = sum(value for value in actual_values if value is not None)
        estimated_credits = sum(value for value in estimated_values if value is not None)
        rate = numeric(pricing, "video", mode, "cny_per_million_tokens")
        if provider == "higgsfield":
            credit_rate = numeric(pricing, "higgsfield", "cny_per_credit")
            cost = money(actual_credits * credit_rate) if credit_rate is not None and all(value is not None for value in actual_values) else None
            estimated_cost = money(estimated_credits * credit_rate) if credit_rate is not None and all(value is not None for value in estimated_values) else None
        else:
            cost = money(known_tokens / 1_000_000 * rate) if rate is not None and all(token is not None for token in tokens) else None
            estimated_cost = None
        if cost is None:
            all_video_cost_known = False
        else:
            video_cost_total += cost
        video_modes[mode] = {
            "requests": len(ops),
            "completed": sum(op.get("status") == "succeeded" for op in ops),
            "completion_tokens": known_tokens if all(token is not None for token in tokens) else None,
            "credits": known_credits if all(value is not None for value in credit_values) else None,
            "credits_reported": known_credits if all(value is not None for value in credit_values) else None,
            "credits_actual": actual_credits if all(value is not None for value in actual_values) else None,
            "credits_estimated": estimated_credits if all(value is not None for value in estimated_values) else None,
            "rate_cny_per_million_tokens": rate,
            "cost_cny": cost,
            "estimated_cost_cny": estimated_cost,
        }

    codex = ledger.get("codex") or world.get("codex_usage") or {}
    codex_price = pricing.get("codex") or {}
    billing_mode = codex.get("billing_mode") or codex_price.get("billing_mode") or "unknown"
    codex_cost = None
    codex_note = None
    if billing_mode == "subscription":
        codex_cost = 0.0
        codex_note = "增量用量费为 0；订阅费未按单次对话分摊。"
    elif billing_mode == "api":
        inp = numeric(codex, "input_tokens")
        cached = numeric(codex, "cached_input_tokens") or 0
        out = numeric(codex, "output_tokens")
        ir = numeric(codex_price, "cny_per_million_input_tokens")
        cr = numeric(codex_price, "cny_per_million_cached_input_tokens")
        orate = numeric(codex_price, "cny_per_million_output_tokens")
        if None not in (inp, out, ir, cr, orate):
            codex_cost = money((inp - cached) / 1_000_000 * ir + cached / 1_000_000 * cr + out / 1_000_000 * orate)
        else:
            codex_note = "缺少主机暴露的 token 用量或 API 价格快照，不能可靠计算。"
    else:
        codex_note = "Codex 计费模式/用量未暴露，不能从项目文件推断。"

    generation_credit_values = image_credit_values + [usage_credits(op) for op in video_ops]
    generation_actual_values = image_actual_values + [usage_credit_field(op, "credits_actual") for op in video_ops]
    generation_estimated_values = image_estimated_values + [usage_credit_field(op, "credits_estimated") for op in video_ops]
    generation_credits = sum(value for value in generation_credit_values if value is not None) if provider == "higgsfield" and all(value is not None for value in generation_credit_values) else None
    generation_actual = sum(value for value in generation_actual_values if value is not None) if provider == "higgsfield" and all(value is not None for value in generation_actual_values) else None
    generation_estimated = sum(value for value in generation_estimated_values if value is not None) if provider == "higgsfield" and all(value is not None for value in generation_estimated_values) else None
    known_costs = [value for value in [image_cost, video_cost_total if all_video_cost_known else None, codex_cost] if value is not None]
    total = money(sum(known_costs)) if len(known_costs) == 3 else None
    return {
        "schema_version": 1,
        "generated_at": now(),
        "provider": provider,
        "workflow_mode": workflow_mode(world),
        "currency": pricing.get("currency") or world.get("project", {}).get("currency") or "CNY",
        "pricing_snapshot": {"source_url": pricing.get("source_url"), "captured_at": pricing.get("captured_at")},
        "generation_budget": {
            "used_or_reserved": sum(budget_slots(op) for op in ledger["operations"].values()),
            "accepted_requests": sum(int(op.get("request_count") or 0) for op in ledger["operations"].values()),
            "limit": world.get("project", {}).get("generation_limit", 20),
        },
        "ai_image": {
            "requests": len(image_ops),
            "outputs": image_outputs,
            "output_tokens": image_tokens if all(value is not None for value in image_token_values) else None,
            "credits": image_credits if all(value is not None for value in image_credit_values) else None,
            "credits_reported": image_credits if all(value is not None for value in image_credit_values) else None,
            "credits_actual": image_actual if all(value is not None for value in image_actual_values) else None,
            "credits_estimated": image_estimated if all(value is not None for value in image_estimated_values) else None,
            "billing_unit": image_unit,
            "rate_cny_per_output": image_rate,
            "rate_cny_per_million_output_tokens": image_token_rate,
            "cost_cny": image_cost,
            "estimated_cost_cny": image_estimated_cost,
        },
        "ai_video": {"modes": video_modes, "cost_cny": money(video_cost_total) if all_video_cost_known else None},
        "generation_credits_total": generation_credits,
        "generation_credits": {
            "reported": generation_credits,
            "actual": generation_actual,
            "estimated": generation_estimated,
        },
        "codex_text": {
            "billing_mode": billing_mode,
            "model": codex.get("model") or codex_price.get("model"),
            "input_tokens": codex.get("input_tokens"),
            "cached_input_tokens": codex.get("cached_input_tokens"),
            "output_tokens": codex.get("output_tokens"),
            "cost_cny": codex_cost,
            "note": codex_note,
        },
        "total_cost_cny": total,
        "cost_status": "complete" if total is not None else "partial",
    }


def display(value) -> str:
    return "不可得" if value is None else f"¥{value:.6f}".rstrip("0").rstrip(".")


def report_markdown(report: dict) -> str:
    video = report["ai_video"]
    token_text = lambda value: "不可得" if value is None else str(value)
    lines = [
        "# Scroll World 成本与用量报告",
        "",
        f"- 生成供应商：{report['provider']}",
        f"- 工作流模式：{report['workflow_mode']}",
        f"- 生成请求：{report['generation_budget']['accepted_requests']} 次；占用/上限 {report['generation_budget']['used_or_reserved']}/{report['generation_budget']['limit']}",
        f"- AI 图片：{report['ai_image']['requests']} 次请求，{report['ai_image']['outputs']} 张输出，output tokens {token_text(report['ai_image']['output_tokens'])}，actual credits {token_text(report['ai_image']['credits_actual'])}，estimated credits {token_text(report['ai_image']['credits_estimated'])}，实际费用 {display(report['ai_image']['cost_cny'])}，预估费用 {display(report['ai_image']['estimated_cost_cny'])}",
    ]
    for mode, row in video["modes"].items():
        tokens = row["completion_tokens"] if row["completion_tokens"] is not None else "不可得"
        actual = token_text(row.get("credits_actual"))
        estimated = token_text(row.get("credits_estimated"))
        lines.append(f"- AI 视频（{mode}）：{row['requests']} 次请求，completion tokens {tokens}，actual credits {actual}，estimated credits {estimated}，实际费用 {display(row['cost_cny'])}，预估费用 {display(row.get('estimated_cost_cny'))}")
    codex = report["codex_text"]
    lines.append(f"- Codex 文本：input={token_text(codex['input_tokens'])}，cached={token_text(codex['cached_input_tokens'])}，output={token_text(codex['output_tokens'])}，费用 {display(codex['cost_cny'])}")
    if codex.get("note"):
        lines.append(f"  - 说明：{codex['note']}")
    if report.get("provider") == "higgsfield":
        credit_totals = report.get("generation_credits") or {}
        lines.append(f"- Higgsfield credits：actual={token_text(credit_totals.get('actual'))}，estimated={token_text(credit_totals.get('estimated'))}，reported={token_text(credit_totals.get('reported'))}")
    lines.extend([
        f"- 总计：{display(report['total_cost_cny'])}",
        "",
        "> 只有在实际用量和带日期的价格快照都存在时才给出金额；缺项不会按猜测补齐。",
        "",
    ])
    return "\n".join(lines)


def cmd_report(args) -> int:
    world = load_world(Path(args.world))
    ledger = read_json(Path(args.ledger), {"schema_version": 1, "operations": {}})
    ledger.setdefault("operations", {})
    report = build_report(world, ledger)
    atomic_write(Path(args.json_output), report)
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(report_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_record_codex(args) -> int:
    with locked_ledger(Path(args.ledger)) as ledger:
        ledger["codex"] = {
            "billing_mode": args.billing_mode,
            "model": args.model,
            "input_tokens": args.input_tokens,
            "cached_input_tokens": args.cached_input_tokens,
            "output_tokens": args.output_tokens,
            "source": args.source,
            "recorded_at": now(),
        }
    return 0


def validate_world(world: dict) -> list[str]:
    errors = []
    if world.get("schema_version", 1) not in {1, 2}:
        errors.append("schema_version must be 1 or 2; run migrate-project.py for older projects")
    project = world.get("project") or {}
    provider = provider_name(world)
    if provider not in {"doubao", "higgsfield"}:
        errors.append("project.provider must be doubao or higgsfield")
    mode = workflow_mode(world)
    if mode not in {"detailed", "fast"}:
        errors.append("project.workflow_mode must be detailed or fast")
    if project.get("architecture") not in {"A", "B"}:
        errors.append("project.architecture must be A or B")
    if not isinstance(project.get("generation_limit"), int) or project.get("generation_limit", 0) < 1:
        errors.append("project.generation_limit must be a positive integer")
    theme = project.get("theme", "low-poly-clay")
    if theme not in theme_catalog():
        errors.append("project.theme is unknown")
    if world.get("schema_version", 1) >= 2 and not isinstance(world.get("design"), dict):
        errors.append("schema v2 requires a design object copied from the selected theme")
    generation = world.get("generation") or {}
    if generation.get("ratio") not in {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        errors.append("generation.ratio is unsupported")
    if not 4 <= int(generation.get("duration_seconds", 0)) <= 15:
        errors.append("generation.duration_seconds must be 4..15")
    semantic_gate = (world.get("quality") or {}).get("require_semantic_approval", True)
    if mode == "detailed":
        if generation.get("preview_enabled") is not True:
            errors.append("detailed mode requires generation.preview_enabled=true")
        if semantic_gate is not True:
            errors.append("detailed mode requires quality.require_semantic_approval=true")
    elif mode == "fast":
        if generation.get("preview_enabled") is not False:
            errors.append("fast mode requires generation.preview_enabled=false")
        if semantic_gate is not False:
            errors.append("fast mode requires quality.require_semantic_approval=false")
    sections = world.get("sections")
    if not isinstance(sections, list) or len(sections) < 2:
        errors.append("sections must contain at least two scenes")
        return errors
    ids = [section.get("id") for section in sections]
    if len(set(ids)) != len(ids) or any(not item for item in ids):
        errors.append("section ids must be present and unique")
    required_outputs = {"still", "raw_final", "final_last_frame", "final_video", "mobile_video", "poster", "mobile_poster"}
    for section in sections:
        missing = required_outputs - set((section.get("outputs") or {}).keys())
        if missing:
            errors.append(f"section {section.get('id')}: missing outputs {sorted(missing)}")
        qa = section.get("qa") or {}
        if not isinstance(qa.get("must_include"), list) or not isinstance(qa.get("must_not_include"), list):
            errors.append(f"section {section.get('id')}: qa constraints must be arrays")
    if project.get("architecture") == "B" and len(world.get("transitions") or []) != len(sections) - 1:
        errors.append("architecture B needs exactly sections-1 transitions")
    try:
        models = provider_models(provider)
    except SystemExit:
        models = {}
    configured = world.get("models") or {}
    if configured.get("image") not in (models.get("image") or {}):
        errors.append("models.image alias is unknown")
    for field in ["video_preview", "video_final"]:
        if configured.get(field) not in (models.get("video") or {}):
            errors.append(f"models.{field} alias is unknown")
    if project.get("architecture") == "B":
        preview_enabled = bool(generation.get("preview_enabled"))
        for transition in world.get("transitions") or []:
            if not transition.get("id") or not transition.get("video_prompt"):
                errors.append("each transition needs id and video_prompt")
            outputs = transition.get("outputs") or {}
            needed = {"raw_final", "final_last_frame", "final_video", "mobile_video"}
            if preview_enabled:
                needed |= {"raw_preview", "preview_last_frame"}
            missing = needed - set(outputs)
            if missing:
                errors.append(f"transition {transition.get('id')}: missing outputs {sorted(missing)}")
    delivery = world.get("delivery") or {}
    portable = delivery.get("portable") or {}
    entry = portable.get("entry", "index.html")
    if not isinstance(entry, str) or not entry or Path(entry).is_absolute() or ".." in Path(entry).parts:
        errors.append("delivery.portable.entry must be a safe relative HTML path")
    if portable.get("direct_video", True) is not True:
        errors.append("delivery.portable.direct_video must be true")
    if portable.get("require_relative_assets", True) is not True:
        errors.append("delivery.portable.require_relative_assets must be true")
    public_files = delivery.get("public_files") or []
    if entry not in public_files:
        errors.append("delivery.portable.entry must be included in delivery.public_files")
    return errors


def cmd_validate(args) -> int:
    world = read_json(Path(args.world), {})
    errors = validate_world(world)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"world valid: {len(world['sections'])} sections, architecture {world['project']['architecture']}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fingerprint")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--input", action="append", default=[])
    p.add_argument("--param", action="append", default=[])
    p.set_defaults(func=cmd_fingerprint)
    p = sub.add_parser("model")
    p.add_argument("kind", choices=["image", "video"])
    p.add_argument("alias")
    p.add_argument("field")
    p.add_argument("--provider", choices=["doubao", "higgsfield"], default="doubao")
    p.set_defaults(func=cmd_model)
    p = sub.add_parser("themes")
    p.set_defaults(func=cmd_themes)
    p = sub.add_parser("reserve")
    p.add_argument("--ledger", default=".work/usage-ledger.json")
    p.add_argument("--world", default="world.json")
    p.add_argument("--kind", required=True, choices=["image", "video"])
    p.add_argument("--mode", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--fingerprint", required=True)
    p.set_defaults(func=cmd_reserve)
    for name, func in [("accept-image", cmd_accept_image), ("accept-video", cmd_accept_video), ("complete-video", cmd_complete_video)]:
        p = sub.add_parser(name)
        p.add_argument("--ledger", default=".work/usage-ledger.json")
        p.add_argument("--operation-id", required=True)
        p.add_argument("--response", required=True)
        p.set_defaults(func=func)
    p = sub.add_parser("fail")
    p.add_argument("--ledger", default=".work/usage-ledger.json")
    p.add_argument("--operation-id", required=True)
    p.add_argument("--reason")
    p.add_argument("--ambiguous", action="store_true")
    p.set_defaults(func=cmd_fail)
    p = sub.add_parser("record-codex")
    p.add_argument("--ledger", default=".work/usage-ledger.json")
    p.add_argument("--billing-mode", choices=["subscription", "api", "unknown"], required=True)
    p.add_argument("--model")
    p.add_argument("--input-tokens", type=int)
    p.add_argument("--cached-input-tokens", type=int, default=0)
    p.add_argument("--output-tokens", type=int)
    p.add_argument("--source", default="user-or-host-provided")
    p.set_defaults(func=cmd_record_codex)
    p = sub.add_parser("report")
    p.add_argument("--world", default="world.json")
    p.add_argument("--ledger", default=".work/usage-ledger.json")
    p.add_argument("--json-output", default=".work/cost-report.json")
    p.add_argument("--markdown-output", default="COSTS.md")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("validate")
    p.add_argument("--world", default="world.json")
    p.set_defaults(func=cmd_validate)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
