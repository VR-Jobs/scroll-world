#!/usr/bin/env python3
"""One-time provider selection and credential bootstrap for scroll-world."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SCHEMA_VERSION = 1
PROVIDERS = {"doubao", "higgsfield"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def config_root() -> Path:
    override = os.getenv("SW_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "scroll-world").resolve()


def config_path() -> Path:
    return config_root() / "config.json"


def credentials_path() -> Path:
    return config_root() / "credentials.json"


def read_json(path: Path, default: dict | None = None) -> dict:
    if not path.is_file():
        return dict(default or {})
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read provider config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"provider config must be a JSON object: {path}")
    return value


def secure_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def selected_provider() -> str | None:
    provider = read_json(config_path()).get("provider")
    return provider if provider in PROVIDERS else None


def doubao_api_key() -> str | None:
    value = os.getenv("ARK_API_KEY")
    if value:
        return value
    stored = read_json(credentials_path())
    value = (stored.get("doubao") or {}).get("api_key")
    return value if isinstance(value, str) and value else None


def runtime_env(provider: str) -> dict[str, str]:
    if provider == "doubao":
        key = doubao_api_key()
        if not key:
            raise SystemExit(
                "Doubao is selected but no stored API key exists; run provider_config.py configure --provider doubao"
            )
        return {"ARK_API_KEY": key}
    if provider == "higgsfield":
        if not shutil.which("higgsfield"):
            raise SystemExit(
                "Higgsfield is selected but its official CLI is missing; install it, then run higgsfield auth login"
            )
        return {}
    raise SystemExit(f"unknown provider: {provider}")


def higgsfield_auth_status() -> tuple[bool, str | None]:
    if not shutil.which("higgsfield"):
        return False, "cli_missing"
    try:
        result = subprocess.run(
            ["higgsfield", "account", "status"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "authentication check timed out"
    if result.returncode == 0:
        return True, None
    detail = (result.stderr or "not authenticated").strip().splitlines()[-1]
    return False, detail


def cmd_status(args) -> int:
    provider = selected_provider()
    output: dict[str, object] = {
        "configured": provider is not None,
        "provider": provider,
        "config_path": str(config_path()),
    }
    if provider == "doubao":
        output["credential_status"] = "ready" if doubao_api_key() else "missing"
        output["credential_source"] = "environment" if os.getenv("ARK_API_KEY") else "stored"
    elif provider == "higgsfield":
        authenticated, reason = higgsfield_auth_status()
        output.update({
            "cli_installed": shutil.which("higgsfield") is not None,
            "authenticated": authenticated,
            "auth_method": "official_cli_oauth",
        })
        if reason:
            output["reason"] = reason
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif not provider:
        print("scroll-world provider is not configured")
    elif provider == "doubao":
        print(f"provider=doubao credential={output['credential_status']}")
    else:
        print(f"provider=higgsfield authenticated={str(output['authenticated']).lower()}")
    return 0 if provider else 20


def cmd_show_provider(_args) -> int:
    provider = selected_provider()
    if not provider:
        raise SystemExit("scroll-world provider is not configured")
    print(provider)
    return 0


def cmd_configure(args) -> int:
    provider = args.provider
    existing = selected_provider()
    if existing and existing != provider and not args.replace:
        raise SystemExit(
            f"provider is already configured as {existing}; pass --replace only after the user explicitly switches"
        )

    if provider == "doubao":
        key = os.getenv(args.api_key_env) if args.api_key_env else None
        if not key:
            key = getpass.getpass("Paste Doubao ARK API key (input hidden): ").strip()
        if len(key) < 12 or any(char.isspace() for char in key):
            raise SystemExit("Doubao API key is empty or malformed")
        secrets = read_json(credentials_path(), {"schema_version": SCHEMA_VERSION})
        secrets["schema_version"] = SCHEMA_VERSION
        secrets["doubao"] = {"api_key": key, "updated_at": now()}
        secure_write(credentials_path(), secrets)
        auth_method = "stored_api_key"
    else:
        authenticated, reason = higgsfield_auth_status()
        if not authenticated:
            if reason == "cli_missing":
                raise SystemExit(
                    "Higgsfield CLI is not installed. Install the official CLI, run 'higgsfield auth login', then retry."
                )
            if reason and ("workspace set" in reason.lower() or "workspace selected" in reason.lower()):
                raise SystemExit(
                    "Higgsfield is authenticated but no workspace is selected. Run 'higgsfield workspace list', "
                    "then 'higgsfield workspace set <workspace_id>', and retry configuration."
                )
            raise SystemExit(
                "Higgsfield is not authenticated. Run 'higgsfield auth login' once, then retry configuration."
            )
        auth_method = "official_cli_oauth"

    secure_write(config_path(), {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "auth_method": auth_method,
        "configured_at": now(),
    })
    print(json.dumps({
        "configured": True,
        "provider": provider,
        "auth_method": auth_method,
        "config_path": str(config_path()),
    }, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    show = sub.add_parser("show-provider")
    show.set_defaults(func=cmd_show_provider)
    configure = sub.add_parser("configure")
    configure.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    configure.add_argument(
        "--api-key-env",
        help="Read the Doubao key from this environment variable; omit for hidden interactive input",
    )
    configure.add_argument("--replace", action="store_true")
    configure.set_defaults(func=cmd_configure)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
