"""Provider interface used by the resumable orchestrator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import sw_tool


class Provider(ABC):
    name: str

    def __init__(self, script_dir: Path):
        self.script_dir = script_dir

    def model_plan(self, world: dict) -> dict[str, str]:
        configured = world["models"]
        try:
            return {
                "image": sw_tool.model_value("image", configured["image"], "model_id", self.name),
                "preview": sw_tool.model_value("video", configured["video_preview"], "model_id", self.name),
                "final": sw_tool.model_value("video", configured["video_final"], "model_id", self.name),
            }
        except (KeyError, SystemExit) as exc:
            raise SystemExit(f"unknown {self.name} model alias in world.json: {exc}") from exc

    @abstractmethod
    def default_model_aliases(self) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def image_command(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def video_submit_command(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def video_poll_command(self) -> list[str]:
        raise NotImplementedError

    def preflight_command(self, world_path: Path) -> list[str] | None:
        return None
