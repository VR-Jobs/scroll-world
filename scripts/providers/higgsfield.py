"""Higgsfield official CLI provider command mapping."""

from __future__ import annotations

from pathlib import Path
import sys

from .base import Provider


class HiggsfieldProvider(Provider):
    name = "higgsfield"

    def default_model_aliases(self) -> dict[str, str]:
        return {"image": "nano-banana-2", "video_preview": "preview", "video_final": "final"}

    def _adapter(self, command: str) -> list[str]:
        return [sys.executable, str(self.script_dir / "higgsfield_adapter.py"), command]

    def image_command(self) -> list[str]:
        return self._adapter("image")

    def video_submit_command(self) -> list[str]:
        return self._adapter("video-submit")

    def video_poll_command(self) -> list[str]:
        return self._adapter("video-poll")

    def preflight_command(self, world_path: Path) -> list[str]:
        return [*self._adapter("preflight"), "--world", str(world_path)]
