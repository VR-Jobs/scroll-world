#!/usr/bin/env python3
"""Opt-in, non-generating provider contract tests against installed CLIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


@unittest.skipUnless(os.getenv("SW_LIVE_CONTRACT") == "1", "set SW_LIVE_CONTRACT=1 to query live provider schemas")
class LiveProviderContractTests(unittest.TestCase):
    def test_higgsfield_catalog_and_selected_schemas_without_generation(self):
        if not shutil.which("higgsfield"):
            self.skipTest("official Higgsfield CLI is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = json.loads((SKILL / "assets" / "world.example.json").read_text(encoding="utf-8"))
            world.pop("$schema", None)
            world["project"]["provider"] = "higgsfield"
            world["models"] = {"image": "nano-banana-2", "video_preview": "preview", "video_final": "final"}
            world_path = root / "world.json"
            world_path.write_text(json.dumps(world), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "higgsfield_adapter.py"), "preflight",
                "--world", str(world_path), "--refresh",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads((root / ".work" / "provider-capabilities.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(len(report["models"]), 3)


if __name__ == "__main__":
    unittest.main()
