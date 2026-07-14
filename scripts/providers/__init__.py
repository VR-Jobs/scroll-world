"""Generation provider registry for scroll-world."""

from __future__ import annotations

from pathlib import Path

from .base import Provider
from .doubao import DoubaoProvider
from .higgsfield import HiggsfieldProvider


def get_provider(name: str, script_dir: Path) -> Provider:
    providers = {
        "doubao": DoubaoProvider(script_dir),
        "higgsfield": HiggsfieldProvider(script_dir),
    }
    try:
        return providers[name]
    except KeyError as exc:
        raise SystemExit(f"unknown generation provider: {name}") from exc


__all__ = ["Provider", "get_provider"]
