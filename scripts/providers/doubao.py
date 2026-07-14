"""Doubao ARK provider command mapping."""

from __future__ import annotations

from .base import Provider


class DoubaoProvider(Provider):
    name = "doubao"

    def default_model_aliases(self) -> dict[str, str]:
        return {"image": "seedream-5-pro", "video_preview": "preview", "video_final": "final"}

    def image_command(self) -> list[str]:
        return [str(self.script_dir / "ark-image.sh")]

    def video_submit_command(self) -> list[str]:
        return [str(self.script_dir / "ark-video-submit.sh")]

    def video_poll_command(self) -> list[str]:
        return [str(self.script_dir / "ark-video-poll.sh")]
