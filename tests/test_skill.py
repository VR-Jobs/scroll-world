#!/usr/bin/env python3

from __future__ import annotations

import copy
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
sys.path.insert(0, str(SCRIPTS))
import sw_tool  # noqa: E402


class ScrollWorldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.world = json.loads((SKILL / "assets" / "world.example.json").read_text(encoding="utf-8"))
        self.world.pop("$schema", None)
        self.world_path = self.root / "world.json"
        self.write_world()

    def tearDown(self):
        self.temp.cleanup()

    def write_world(self):
        self.world_path.write_text(json.dumps(self.world, ensure_ascii=False, indent=2), encoding="utf-8")

    def fake_env(self):
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir(exist_ok=True)
        curl = fake_bin / "curl"
        curl.write_text(
            """#!/bin/sh
out=
kind=download
want_out=0
for arg in "$@"; do
  if test "$want_out" -eq 1; then out="$arg"; want_out=0; continue; fi
  case "$arg" in
    -o) want_out=1 ;;
    */images/generations) kind=image ;;
    */contents/generations/tasks/task-1) kind=video-status ;;
    */contents/generations/tasks) kind=video-submit ;;
    https://download/video) kind=video-download ;;
    https://download/frame) kind=frame-download ;;
    https://download/image) kind=image-download ;;
  esac
done
case "$kind" in
  image)
    printf '%s\n' '{"id":"image-1","data":[{"url":"https://download/image"}],"usage":{"generated_images":1}}' > "$out"
    printf 200
    ;;
  video-submit)
    printf '%s\n' '{"id":"task-1","request_id":"request-1"}' > "$out"
    printf 200
    ;;
  video-status)
    printf '%s\n' '{"status":"succeeded","content":{"video_url":"https://download/video","last_frame_url":"https://download/frame"},"usage":{"completion_tokens":1234}}' > "$out"
    printf 200
    ;;
  video-download) printf 'fake-video' > "$out" ;;
  frame-download) printf 'fake-frame' > "$out" ;;
  image-download) printf 'fake-image' > "$out" ;;
  *) printf 'unexpected fake curl request\n' >&2; exit 9 ;;
esac
""",
            encoding="utf-8",
        )
        curl.chmod(0o755)
        env = os.environ.copy()
        env.update({
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ARK_API_KEY": "test-only",
            "ARK_API_BASE": "https://fake/api/v3",
            "SW_WORLD_FILE": str(self.world_path),
            "SW_LEDGER_FILE": str(self.root / ".work" / "usage-ledger.json"),
        })
        return env

    def fake_higgsfield_env(self):
        fake_bin = self.root / "fake-higgsfield-bin"
        fake_bin.mkdir(exist_ok=True)
        image_source = self.root / "hf-source-image.jpg"
        video_source = self.root / "hf-source-video.mp4"
        image_source.write_bytes(b"fake-higgsfield-image")
        video_source.write_bytes(b"fake-higgsfield-video")
        higgsfield = fake_bin / "higgsfield"
        higgsfield.write_text(
            f"""#!/bin/sh
case "$1 $2" in
  "account status") exit 0 ;;
  "model list") printf '%s\n' '{{"models":[{{"id":"nano_banana_2"}},{{"id":"seedance_2_0_mini"}},{{"id":"seedance_2_0"}}]}}' ;;
  "model get") printf '%s\n' '{{"schema":{{"prompt":{{}},"aspect_ratio":{{}},"resolution":{{}},"start_image":{{}},"duration":{{}}}}}}' ;;
  "generate cost") printf '%s\n' '{{"credits":2.5}}' ;;
  "generate create")
    case " $* " in
      *" --wait "*) printf '%s\n' '[{{"id":"hf-image-1","status":"completed","result_url":"file://{image_source}","credits":2.5}}]' ;;
      *) printf '%s\n' '[{{"id":"hf-video-1","status":"queued"}}]' ;;
    esac
    ;;
  "generate get") printf '%s\n' '[{{"id":"hf-video-1","status":"completed","result_url":"file://{video_source}","credits":3.5}}]' ;;
  *) printf 'unexpected fake higgsfield command: %s\n' "$*" >&2; exit 9 ;;
esac
""",
            encoding="utf-8",
        )
        higgsfield.chmod(0o755)
        ffmpeg = fake_bin / "ffmpeg"
        ffmpeg.write_text(
            """#!/bin/sh
for last; do :; done
printf 'fake-higgsfield-last-frame' > "$last"
""",
            encoding="utf-8",
        )
        ffmpeg.chmod(0o755)
        env = os.environ.copy()
        env.update({
            "PATH": f"{fake_bin}:{env['PATH']}",
            "SW_WORLD_FILE": str(self.world_path),
            "SW_LEDGER_FILE": str(self.root / ".work" / "usage-ledger.json"),
            "SW_ALLOW_FILE_URLS": "1",
            "SW_CONFIG_HOME": str(self.root / "user-config"),
        })
        return env

    def run_cmd(self, command, *, env=None, expected=0):
        result = subprocess.run(command, cwd=self.root, env=env, text=True, capture_output=True, check=False)
        if result.returncode != expected:
            self.fail(f"expected {expected}, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def test_project_initializer_isolates_sites_and_rejects_collisions(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        command = [
            sys.executable,
            str(SCRIPTS / "init-project.py"),
            "--workspace-root",
            str(workspace),
            "--name",
            "NEW SITE",
            "--slug",
            "new-site",
            "--provider",
            "doubao",
        ]
        created = json.loads(self.run_cmd(command).stdout)
        project = Path(created["project_root"])
        self.assertEqual(project, workspace.resolve() / "new-site")
        self.assertEqual(created["status"], "created")
        self.assertTrue((project / ".scroll-world-project.json").is_file())
        self.assertTrue((project / "world.json").is_file())
        self.assertTrue((project / "prompts").is_dir())
        self.assertTrue((project / "assets" / "vid").is_dir())
        self.assertTrue((project / ".work").is_dir())
        self.assertTrue((project / "dist").is_dir())
        initialized_world = json.loads((project / "world.json").read_text(encoding="utf-8"))
        self.assertEqual(initialized_world["project"]["brand"], "NEW SITE")
        self.assertEqual(initialized_world["project"]["workflow_mode"], "detailed")
        self.assertEqual(initialized_world["project"]["provider"], "doubao")
        self.assertEqual(initialized_world["project"]["theme"], "low-poly-clay")
        self.assertEqual(initialized_world["schema_version"], 2)
        self.assertIn("style_prompt", initialized_world["design"])
        self.assertTrue(initialized_world["generation"]["preview_enabled"])
        self.assertTrue(initialized_world["quality"]["require_semantic_approval"])
        marker = json.loads((project / ".scroll-world-project.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["workflow_mode"], "detailed")
        self.assertEqual(marker["provider"], "doubao")
        self.assertEqual(marker["theme"], "low-poly-clay")
        self.assertNotIn("$schema", initialized_world)
        self.assertFalse((workspace / "world.json").exists())
        self.assertFalse((workspace / "assets").exists())

        collision = self.run_cmd(command, expected=3)
        self.assertIn("project directory already exists", collision.stderr)
        resumed = json.loads(self.run_cmd(command + ["--resume"]).stdout)
        self.assertEqual(resumed["status"], "resumed")

        foreign = workspace / "foreign"
        foreign.mkdir()
        (foreign / "index.html").write_text("foreign", encoding="utf-8")
        foreign_command = list(command)
        foreign_command[foreign_command.index("--slug") + 1] = "foreign"
        refused = self.run_cmd(
            foreign_command + ["--resume"],
            expected=4,
        )
        self.assertIn("refusing to resume unmarked directory", refused.stderr)

        fast_command = [
            sys.executable,
            str(SCRIPTS / "init-project.py"),
            "--workspace-root",
            str(workspace),
            "--name",
            "FAST SITE",
            "--slug",
            "fast-site",
            "--provider",
            "doubao",
            "--mode",
            "fast",
        ]
        fast_created = json.loads(self.run_cmd(fast_command).stdout)
        fast_world = json.loads((Path(fast_created["project_root"]) / "world.json").read_text(encoding="utf-8"))
        self.assertEqual(fast_created["workflow_mode"], "fast")
        self.assertEqual(fast_world["project"]["workflow_mode"], "fast")
        self.assertFalse(fast_world["generation"]["preview_enabled"])
        self.assertFalse(fast_world["quality"]["require_semantic_approval"])
        mode_mismatch = self.run_cmd(fast_command[:-1] + ["detailed", "--resume"], expected=4)
        self.assertIn("project marker does not match name/slug/mode/provider/theme", mode_mismatch.stderr)

    def test_provider_configuration_is_one_time_and_secret_safe(self):
        config_home = self.root / "provider-config"
        env = os.environ.copy()
        env.update({"SW_CONFIG_HOME": str(config_home), "TEST_ARK_KEY": "ark-test-secret-value"})
        script = str(SCRIPTS / "provider_config.py")
        initial = self.run_cmd([sys.executable, script, "status", "--json"], env=env, expected=20)
        self.assertFalse(json.loads(initial.stdout)["configured"])
        configured = self.run_cmd([
            sys.executable, script, "configure", "--provider", "doubao", "--api-key-env", "TEST_ARK_KEY",
        ], env=env)
        self.assertNotIn("ark-test-secret-value", configured.stdout + configured.stderr)
        self.assertEqual(json.loads(configured.stdout)["provider"], "doubao")
        credentials = config_home / "credentials.json"
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)
        self.assertEqual((config_home / "config.json").stat().st_mode & 0o777, 0o600)
        status = json.loads(self.run_cmd([sys.executable, script, "status", "--json"], env=env).stdout)
        self.assertEqual(status["credential_status"], "ready")
        self.assertNotIn("ark-test-secret-value", json.dumps(status))
        refused = self.run_cmd([
            sys.executable, script, "configure", "--provider", "higgsfield",
        ], env=env, expected=1)
        self.assertIn("already configured as doubao", refused.stderr)

    def test_higgsfield_configuration_reuses_official_cli_oauth(self):
        env = self.fake_higgsfield_env()
        script = str(SCRIPTS / "provider_config.py")
        configured = json.loads(self.run_cmd([
            sys.executable, script, "configure", "--provider", "higgsfield",
        ], env=env).stdout)
        self.assertEqual(configured["provider"], "higgsfield")
        self.assertEqual(configured["auth_method"], "official_cli_oauth")
        status = json.loads(self.run_cmd([
            sys.executable, script, "status", "--json",
        ], env=env).stdout)
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["auth_method"], "official_cli_oauth")
        self.assertFalse((Path(env["SW_CONFIG_HOME"]) / "credentials.json").exists())

    def test_higgsfield_setup_refuses_to_choose_billing_workspace(self):
        env = self.fake_higgsfield_env()
        cli = Path(env["PATH"].split(":", 1)[0]) / "higgsfield"
        text = cli.read_text(encoding="utf-8").replace('"account status") exit 0 ;;', '"account status") printf "Hint: Run: higgsfield workspace set <workspace_id>\\n" >&2; exit 4 ;;')
        cli.write_text(text, encoding="utf-8")
        result = self.run_cmd([
            sys.executable, str(SCRIPTS / "provider_config.py"), "configure", "--provider", "higgsfield",
        ], env=env, expected=1)
        self.assertIn("no workspace is selected", result.stderr)
        self.assertIn("workspace set", result.stderr)

    def test_world_valid_and_plan(self):
        errors = sw_tool.validate_world(self.world)
        self.assertEqual(errors, [])
        legacy = copy.deepcopy(self.world)
        legacy["project"].pop("workflow_mode")
        legacy["project"].pop("provider")
        self.assertEqual(sw_tool.validate_world(legacy), [])
        invalid = copy.deepcopy(self.world)
        invalid["delivery"]["portable"]["direct_video"] = False
        self.assertIn("delivery.portable.direct_video must be true", sw_tool.validate_world(invalid))
        result = self.run_cmd([sys.executable, str(SCRIPTS / "scroll-world.py"), "plan", "--world", str(self.world_path)])
        plan = json.loads(result.stdout)
        self.assertEqual(plan["workflow_mode"], "detailed")
        self.assertEqual(plan["planned_requests_without_retries"]["total"], 6)
        self.assertEqual(plan["retry_reserve"], 14)

        fast = copy.deepcopy(self.world)
        fast["project"]["workflow_mode"] = "fast"
        fast["generation"]["preview_enabled"] = False
        fast["quality"]["require_semantic_approval"] = False
        self.assertEqual(sw_tool.validate_world(fast), [])
        self.world = fast
        self.write_world()
        fast_plan = json.loads(self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "plan", "--world", str(self.world_path),
        ]).stdout)
        self.assertEqual(fast_plan["workflow_mode"], "fast")
        self.assertEqual(fast_plan["planned_requests_without_retries"]["total"], 4)
        self.assertEqual(fast_plan["retry_reserve"], 16)
        invalid_fast = copy.deepcopy(fast)
        invalid_fast["generation"]["preview_enabled"] = True
        self.assertIn("fast mode requires generation.preview_enabled=false", sw_tool.validate_world(invalid_fast))

    def test_atomic_budget_reservation(self):
        self.world["project"]["generation_limit"] = 1
        self.write_world()
        base = [sys.executable, str(SCRIPTS / "sw_tool.py"), "reserve", "--world", str(self.world_path), "--ledger", str(self.root / "ledger.json"), "--kind", "image", "--mode", "image", "--label", "x", "--model", "m", "--fingerprint", "f"]
        self.run_cmd(base)
        result = subprocess.run(base, cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("budget exhausted", result.stderr)

    def test_image_fake_api_cache_and_privacy(self):
        prompt = self.root / "prompt.txt"
        reference = self.root / "reference.png"
        output = self.root / "assets" / "image.jpg"
        prompt.write_text("blue low-poly world", encoding="utf-8")
        reference.write_bytes(b"fake-png")
        env = self.fake_env()
        command = [str(SCRIPTS / "ark-image.sh"), str(prompt), str(output), "--reference", str(reference), "--label", "still:test"]
        self.run_cmd(command, env=env)
        self.assertEqual(output.read_bytes(), b"fake-image")
        work = self.root / ".work" / "ark-image" / "image.jpg"
        self.assertFalse((work / "request.json").exists())
        self.assertEqual(list(work.glob("*.data-uri")), [])
        metadata = json.loads((work / "request-metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["image"], ["<redacted:data-uri>"])
        ledger = json.loads((self.root / ".work" / "usage-ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["operations"]), 1)
        self.run_cmd(command, env=env)
        ledger2 = json.loads((self.root / ".work" / "usage-ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger2["operations"]), 1)
        prompt.write_text("changed prompt", encoding="utf-8")
        result = self.run_cmd(command, env=env, expected=12)
        self.assertIn("stale image cache", result.stderr)

    def test_image_review_gates_block_video_and_support_targeted_retry(self):
        for section in self.world["sections"]:
            for field in ["still_prompt", "video_prompt"]:
                prompt = self.root / section[field]
                prompt.parent.mkdir(parents=True, exist_ok=True)
                prompt.write_text(f"prompt for {section['id']} {field}", encoding="utf-8")
        self.write_world()
        env = self.fake_env()
        run = [sys.executable, str(SCRIPTS / "scroll-world.py"), "run", "--world", str(self.world_path), "--resume"]

        anchor_gate = self.run_cmd(run, env=env, expected=20)
        self.assertIn("GATE anchor", anchor_gate.stdout)
        self.assertTrue((self.root / self.world["sections"][0]["outputs"]["still"]).is_file())
        self.assertFalse((self.root / self.world["sections"][1]["outputs"]["still"]).exists())
        self.assertFalse((self.root / ".work" / "video-preview-hero").exists())
        premature_images = subprocess.run([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "images", "--world", str(self.world_path),
        ], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(premature_images.returncode, 0)
        self.assertIn("current anchor is approved", premature_images.stderr)

        self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "anchor",
            "--world", str(self.world_path), "--note", "style approved",
        ])
        images_gate = self.run_cmd(run, env=env, expected=20)
        self.assertIn("GATE images", images_gate.stdout)
        self.assertIn("approve images", images_gate.stdout)
        self.assertTrue((self.root / self.world["sections"][1]["outputs"]["still"]).is_file())
        self.assertFalse((self.root / ".work" / "video-preview-hero").exists())
        premature_preview = subprocess.run([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "preview", "--world", str(self.world_path),
        ], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(premature_preview.returncode, 0)
        self.assertIn("image batch is approved", premature_preview.stderr)

        self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "images",
            "--world", str(self.world_path), "--note", "all images approved",
        ])
        approved = json.loads(self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "status", "--world", str(self.world_path),
        ]).stdout)
        self.assertTrue(approved["approvals"]["anchor_valid"])
        self.assertTrue(approved["approvals"]["images_valid"])
        premature_preview_files = subprocess.run([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "preview", "--world", str(self.world_path),
        ], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(premature_preview_files.returncode, 0)
        self.assertIn("every preview video", premature_preview_files.stderr)

        target_still = self.root / self.world["sections"][1]["outputs"]["still"]
        target_still.write_bytes(b"manually changed image")
        changed = json.loads(self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "status", "--world", str(self.world_path),
        ]).stdout)
        self.assertFalse(changed["approvals"]["images_valid"])

        self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "retry", "--stage", "still", "--id", "optics",
            "--world", str(self.world_path),
        ])
        invalidated = json.loads(self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "status", "--world", str(self.world_path),
        ]).stdout)
        self.assertTrue(invalidated["approvals"]["anchor_valid"])
        self.assertFalse(invalidated["approvals"]["images_valid"])
        regenerated_gate = self.run_cmd(run, env=env, expected=20)
        self.assertIn("GATE images", regenerated_gate.stdout)
        self.assertFalse((self.root / ".work" / "video-preview-hero").exists())

        self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "images",
            "--world", str(self.world_path), "--note", "targeted retry approved",
        ])
        preview_submitted = self.run_cmd(run, env=env, expected=10)
        self.assertIn("preview task submitted/running", preview_submitted.stdout)
        self.assertTrue((self.root / ".work" / "video-preview-hero" / "task-id.txt").is_file())

    def test_fast_mode_skips_intermediate_approval_and_preview(self):
        self.world["project"]["workflow_mode"] = "fast"
        self.world["generation"]["preview_enabled"] = False
        self.world["quality"]["require_semantic_approval"] = False
        for section in self.world["sections"]:
            for field in ["still_prompt", "video_prompt"]:
                prompt = self.root / section[field]
                prompt.parent.mkdir(parents=True, exist_ok=True)
                prompt.write_text(f"prompt for {section['id']} {field}", encoding="utf-8")
        self.write_world()
        env = self.fake_env()
        run = [sys.executable, str(SCRIPTS / "scroll-world.py"), "run", "--world", str(self.world_path), "--resume"]
        submitted = self.run_cmd(run, env=env, expected=10)
        self.assertIn("workflow mode: fast", submitted.stdout)
        self.assertIn("final task submitted/running", submitted.stdout)
        self.assertNotIn("GATE anchor", submitted.stdout)
        self.assertNotIn("GATE images", submitted.stdout)
        self.assertNotIn("preview task", submitted.stdout)
        self.assertTrue(all((self.root / section["outputs"]["still"]).is_file() for section in self.world["sections"]))
        self.assertTrue((self.root / ".work" / "video-final-hero" / "task-id.txt").is_file())
        self.assertFalse((self.root / ".work" / "video-preview-hero").exists())
        refused = subprocess.run([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "approve", "anchor", "--world", str(self.world_path),
        ], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("approval gates are disabled in fast mode", refused.stderr)

    def test_higgsfield_provider_routes_generation_and_tracks_credits(self):
        self.world["project"]["provider"] = "higgsfield"
        self.world["project"]["workflow_mode"] = "fast"
        self.world["models"] = {
            "image": "nano-banana-2",
            "video_preview": "preview",
            "video_final": "final",
        }
        self.world["generation"]["preview_enabled"] = False
        self.world["quality"]["require_semantic_approval"] = False
        for section in self.world["sections"]:
            for field in ["still_prompt", "video_prompt"]:
                prompt = self.root / section[field]
                prompt.parent.mkdir(parents=True, exist_ok=True)
                prompt.write_text(f"Higgsfield prompt for {section['id']} {field}", encoding="utf-8")
        self.write_world()
        env = self.fake_higgsfield_env()
        run = [sys.executable, str(SCRIPTS / "scroll-world.py"), "run", "--world", str(self.world_path), "--resume"]
        submitted = self.run_cmd(run, env=env, expected=10)
        self.assertIn("provider: higgsfield", submitted.stdout)
        self.assertIn("final task submitted/running", submitted.stdout)
        self.assertTrue((self.root / ".work" / "higgsfield-image" / "anchor-hero.jpg").is_dir())
        self.assertFalse((self.root / ".work" / "ark-image").exists())
        self.assertEqual((self.root / ".work" / "video-final-hero" / "task-id.txt").read_text().strip(), "hf-video-1")
        capability = json.loads((self.root / ".work" / "provider-capabilities.json").read_text(encoding="utf-8"))
        self.assertEqual(capability["status"], "pass")

        adapter = str(SCRIPTS / "higgsfield_adapter.py")
        output = self.root / self.world["sections"][0]["outputs"]["raw_final"]
        last = self.root / self.world["sections"][0]["outputs"]["final_last_frame"]
        self.run_cmd([
            sys.executable, adapter, "video-poll", str(self.root / ".work" / "video-final-hero"),
            str(output), str(last),
        ], env=env)
        self.assertEqual(output.read_bytes(), b"fake-higgsfield-video")
        self.assertEqual(last.read_bytes(), b"fake-higgsfield-last-frame")
        ledger = json.loads((self.root / ".work" / "usage-ledger.json").read_text(encoding="utf-8"))
        image_ops = [op for op in ledger["operations"].values() if op["kind"] == "image"]
        video_ops = [op for op in ledger["operations"].values() if op["kind"] == "video"]
        self.assertEqual(len(image_ops), 2)
        self.assertTrue(all(op["provider"] == "higgsfield" for op in image_ops + video_ops))
        self.assertTrue(all(op["usage"]["credits"] == 2.5 for op in image_ops))
        self.assertTrue(all(op["usage"]["credits_actual"] == 2.5 for op in image_ops))
        self.assertTrue(all(op["usage"]["credits_estimated"] == 2.5 for op in image_ops))
        self.assertEqual(video_ops[0]["usage"]["credits"], 3.5)
        report = sw_tool.build_report(self.world, ledger)
        self.assertEqual(report["provider"], "higgsfield")
        self.assertEqual(report["ai_image"]["credits"], 5.0)
        self.assertEqual(report["ai_image"]["credits_actual"], 5.0)
        self.assertEqual(report["ai_image"]["credits_estimated"], 5.0)
        self.assertEqual(report["ai_image"]["billing_unit"], "credits")
        self.assertEqual(report["ai_video"]["modes"]["final"]["credits"], 3.5)
        self.assertEqual(report["generation_credits_total"], 8.5)
        self.assertEqual(report["generation_credits"]["actual"], 8.5)
        self.assertEqual(report["generation_credits"]["estimated"], 7.5)
        markdown = sw_tool.report_markdown(report)
        self.assertIn("AI 图片：2 次请求", markdown)
        self.assertIn("actual credits 5.0", markdown)
        self.assertIn("Higgsfield credits：actual=8.5，estimated=7.5", markdown)

    def test_higgsfield_schema_drift_stops_before_budget_reservation(self):
        self.world["project"]["provider"] = "higgsfield"
        self.world["models"] = {"image": "nano-banana-2", "video_preview": "preview", "video_final": "final"}
        self.write_world()
        env = self.fake_higgsfield_env()
        cli = Path(env["PATH"].split(":", 1)[0]) / "higgsfield"
        cli.write_text(cli.read_text(encoding="utf-8").replace(',"duration":{}', ''), encoding="utf-8")
        result = self.run_cmd([
            sys.executable, str(SCRIPTS / "higgsfield_adapter.py"), "preflight",
            "--world", str(self.world_path), "--refresh",
        ], env=env, expected=1)
        self.assertIn("missing required capabilities", result.stderr)
        self.assertFalse((self.root / ".work" / "usage-ledger.json").exists())

    def test_doctor_and_theme_catalog_cover_first_run_preflight(self):
        env = os.environ.copy()
        env.update({"ARK_API_KEY": "ark-doctor-test-secret", "SW_CONFIG_HOME": str(self.root / "doctor-config")})
        doctor = json.loads(self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "doctor", "--world", str(self.world_path),
        ], env=env).stdout)
        self.assertEqual(doctor["status"], "pass")
        self.assertEqual(doctor["provider"], "doubao")
        self.assertTrue(any(item["name"] == "doubao:credential" and item["status"] == "pass" for item in doctor["checks"]))
        themes = json.loads(self.run_cmd([sys.executable, str(SCRIPTS / "scroll-world.py"), "themes"]).stdout)
        self.assertGreaterEqual(len(themes), 6)
        self.assertIn("chrome-futurism", themes)

    def test_video_fake_api_reference_usage_and_privacy(self):
        prompt = self.root / "video.txt"
        first = self.root / "first.png"
        reference = self.root / "scene.png"
        prompt.write_text("图片1保持连续，图片2定义场景", encoding="utf-8")
        first.write_bytes(b"first")
        reference.write_bytes(b"reference")
        task = self.root / ".work" / "video-final-test"
        video = self.root / "assets" / "raw-final-test.mp4"
        frame = self.root / "assets" / "raw-final-test-last.png"
        env = self.fake_env()
        submit = [str(SCRIPTS / "ark-video-submit.sh"), "final", str(prompt), str(first), str(task), "", "16:9", "5", "--reference-image", str(reference), "--label", "section:final:test"]
        self.run_cmd(submit, env=env)
        self.assertEqual((task / "task-id.txt").read_text(encoding="utf-8").strip(), "task-1")
        self.assertFalse((task / "request.json").exists())
        self.assertEqual(list(task.glob("*.data-uri")), [])
        metadata = json.loads((task / "request-metadata.json").read_text(encoding="utf-8"))
        image_items = [item for item in metadata["content"] if item["type"] == "image_url"]
        self.assertEqual([item["role"] for item in image_items], ["first_frame", "reference_image"])
        self.assertTrue(all(item["image_url"]["url"] == "<redacted:data-uri>" for item in image_items))
        self.run_cmd([str(SCRIPTS / "ark-video-poll.sh"), str(task), str(video), str(frame)], env=env)
        self.assertEqual(video.read_bytes(), b"fake-video")
        ledger = json.loads((self.root / ".work" / "usage-ledger.json").read_text(encoding="utf-8"))
        operation = next(iter(ledger["operations"].values()))
        self.assertEqual(operation["usage"]["completion_tokens"], 1234)
        self.assertEqual(operation["request_count"], 1)

    def test_cost_report_exact_when_usage_and_rates_exist(self):
        self.world["pricing"] = {
            "currency": "CNY",
            "source_url": "https://official.example/pricing",
            "captured_at": "2026-07-14",
            "image": {"billing_unit": "output", "cny_per_output": 0.25, "cny_per_million_output_tokens": None},
            "video": {"preview": {"cny_per_million_tokens": 10}, "final": {"cny_per_million_tokens": 20}},
            "codex": {
                "billing_mode": "api", "model": "test",
                "cny_per_million_input_tokens": 5,
                "cny_per_million_cached_input_tokens": 1,
                "cny_per_million_output_tokens": 15,
            },
        }
        ledger = {
            "operations": {
                "i": {"kind": "image", "mode": "image", "status": "succeeded", "request_count": 1, "output_count": 2},
                "v1": {"kind": "video", "mode": "preview", "status": "succeeded", "request_count": 1, "usage": {"completion_tokens": 1_000_000}},
                "v2": {"kind": "video", "mode": "final", "status": "succeeded", "request_count": 1, "usage": {"completion_tokens": 2_000_000}},
            },
            "codex": {"billing_mode": "api", "model": "test", "input_tokens": 1_000_000, "cached_input_tokens": 500_000, "output_tokens": 1_000_000},
        }
        report = sw_tool.build_report(self.world, ledger)
        self.assertEqual(report["workflow_mode"], "detailed")
        self.assertEqual(report["ai_image"]["cost_cny"], 0.5)
        self.assertEqual(report["ai_video"]["cost_cny"], 50.0)
        self.assertEqual(report["codex_text"]["cost_cny"], 18.0)
        self.assertEqual(report["total_cost_cny"], 68.5)
        self.assertEqual(report["cost_status"], "complete")
        token_world = copy.deepcopy(self.world)
        token_world["pricing"]["image"] = {
            "billing_unit": "million_output_tokens",
            "cny_per_output": None,
            "cny_per_million_output_tokens": 0.5,
        }
        token_ledger = copy.deepcopy(ledger)
        token_ledger["operations"]["i"]["usage"] = {"output_tokens": 2_000_000}
        token_report = sw_tool.build_report(token_world, token_ledger)
        self.assertEqual(token_report["ai_image"]["cost_cny"], 1.0)
        self.assertEqual(token_report["total_cost_cny"], 69.0)

    def test_production_allowlist_and_budget(self):
        (self.root / "assets").mkdir()
        (self.root / "index.html").write_text('<link href="app.css"><img src="assets/poster.webp"><script>const config={directVideo:true}</script>', encoding="utf-8")
        (self.root / "app.css").write_text("body{color:#fff}", encoding="utf-8")
        (self.root / "assets" / "poster.webp").write_bytes(b"poster")
        self.world["delivery"] = {
            "output_dir": "dist",
            "initial_files": ["index.html", "app.css", "assets/poster.webp"],
            "public_files": ["index.html", "app.css", "assets/poster.webp"],
            "budgets": {"max_total_mb": 1, "max_single_video_mb": 1, "max_initial_mb": 1},
        }
        self.world["quality"]["require_browser_qa"] = False
        self.write_world()
        self.run_cmd([sys.executable, str(SCRIPTS / "build-production.py"), "--world", str(self.world_path)])
        self.assertTrue((self.root / "dist" / "asset-manifest.json").is_file())
        portable = json.loads((self.root / "dist" / "portable-report.json").read_text(encoding="utf-8"))
        self.assertEqual(portable["status"], "pass")
        self.assertEqual(portable["supported_launch_modes"], ["file:// double-click", "http://", "https://"])
        self.assertFalse((self.root / "dist" / "world.json").exists())
        self.world["delivery"]["public_files"].append("assets/raw-final-leak.mp4")
        self.world["delivery"]["initial_files"].append("assets/raw-final-leak.mp4")
        (self.root / "assets" / "raw-final-leak.mp4").write_bytes(b"raw")
        self.write_world()
        result = subprocess.run([sys.executable, str(SCRIPTS / "build-production.py"), "--world", str(self.world_path), "--dry-run"], cwd=self.root, text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked source/work asset", result.stderr)

    def test_portable_verifier_rejects_server_only_output(self):
        (self.root / "assets").mkdir()
        (self.root / "assets" / "poster.webp").write_bytes(b"poster")
        entry = self.root / "index.html"
        entry.write_text('<img src="assets/poster.webp"><script>const config={directVideo:true}</script>', encoding="utf-8")
        result = self.run_cmd([sys.executable, str(SCRIPTS / "verify-portable.py"), "--root", str(self.root)])
        self.assertIn('"status": "pass"', result.stdout)
        entry.write_text('<img src="/assets/poster.webp"><script type="module">const config={directVideo:true}</script>', encoding="utf-8")
        failed = subprocess.run(
            [sys.executable, str(SCRIPTS / "verify-portable.py"), "--root", str(self.root)],
            cwd=self.root, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("relative path", failed.stderr)
        self.assertIn('script type="module"', failed.stderr)

    def test_retry_invalidates_downstream_chain_and_derived_evidence(self):
        for section in self.world["sections"]:
            for value in section["outputs"].values():
                path = self.root / value
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(section["id"].encode())
            for mode in ["preview", "final"]:
                task = self.root / ".work" / f"video-{mode}-{section['id']}"
                task.mkdir(parents=True, exist_ok=True)
                (task / "task-id.txt").write_text(section["id"], encoding="utf-8")
        for value in ["media-report.json", "semantic-report.json", "browser-report.json"]:
            path = self.root / ".work" / "qa" / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        (self.root / "dist").mkdir()
        (self.root / "dist" / "index.html").write_text("old", encoding="utf-8")
        (self.root / "COSTS.md").write_text("old", encoding="utf-8")
        self.write_world()
        explain = json.loads(self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "retry", "--world", str(self.world_path),
            "--stage", "still", "--id", "optics", "--explain",
        ]).stdout)
        self.assertIn("assets/raw-final-optics.mp4", explain["existing_paths_to_archive"])
        self.assertNotIn("assets/raw-final-hero.mp4", explain["existing_paths_to_archive"])
        self.run_cmd([
            sys.executable, str(SCRIPTS / "scroll-world.py"), "retry", "--world", str(self.world_path),
            "--stage", "still", "--id", "optics",
        ])
        self.assertTrue((self.root / "assets" / "raw-final-hero.mp4").is_file())
        self.assertFalse((self.root / "assets" / "raw-final-optics.mp4").exists())
        self.assertFalse((self.root / "assets" / "vid" / "final-optics.mp4").exists())
        self.assertFalse((self.root / "dist").exists())
        self.assertFalse((self.root / ".work" / "qa" / "browser-report.json").exists())

    def test_browser_qa_is_fingerprint_bound_completion_gate(self):
        self.world["delivery"]["public_files"] = ["index.html", "app.css", "assets/vid/final-hero.mp4"]
        self.world["delivery"]["initial_files"] = ["index.html", "app.css"]
        self.world["delivery"]["portable"]["entry"] = "index.html"
        for value in self.world["delivery"]["public_files"]:
            path = self.root / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        self.write_world()
        runs = []
        for mode in ["file", "http"]:
            for viewport in ["desktop", "mobile", "tablet"]:
                runs.append({
                    "launch_mode": mode,
                    "viewport": viewport,
                    "console_errors": [],
                    "visual_pass": True,
                    "smoke": {"pass": True, "videoCount": 1, "videos": [{"seekableEnd": 5, "currentTimeChanged": True}]},
                })
        evidence = self.root / "browser-evidence.json"
        evidence.write_text(json.dumps({"runs": runs}), encoding="utf-8")
        script = str(SCRIPTS / "browser-qa.py")
        self.run_cmd([sys.executable, script, "prepare", "--world", str(self.world_path)])
        self.run_cmd([sys.executable, script, "record", "--world", str(self.world_path), "--evidence", str(evidence)])
        self.run_cmd([sys.executable, script, "check", "--world", str(self.world_path)])
        (self.root / "index.html").write_bytes(b"changed")
        stale = self.run_cmd([sys.executable, script, "check", "--world", str(self.world_path)], expected=4)
        self.assertIn("stale", stale.stderr)

    def test_schema_v1_project_migrates_to_theme_and_browser_gate(self):
        legacy = copy.deepcopy(self.world)
        legacy["schema_version"] = 1
        legacy["project"].pop("theme", None)
        legacy.pop("design", None)
        legacy["quality"].pop("require_browser_qa", None)
        legacy["quality"].pop("still_alignment_warn_below", None)
        legacy["quality"].pop("motion_jump_warn_ratio", None)
        self.world_path.write_text(json.dumps(legacy), encoding="utf-8")
        script = str(SCRIPTS / "migrate-project.py")
        dry = json.loads(self.run_cmd([sys.executable, script, "--world", str(self.world_path), "--dry-run"]).stdout)
        self.assertEqual(dry["from"], 1)
        self.assertFalse((self.root / ".work" / "migrations").exists())
        migrated = json.loads(self.run_cmd([sys.executable, script, "--world", str(self.world_path)]).stdout)
        value = json.loads(self.world_path.read_text(encoding="utf-8"))
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual(value["project"]["theme"], "low-poly-clay")
        self.assertTrue(value["quality"]["require_browser_qa"])
        self.assertIn("style_prompt", value["design"])
        self.assertTrue(Path(migrated["backup"]).is_file())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_real_media_and_semantic_qa_pipeline(self):
        (self.root / "assets").mkdir()
        (self.root / "assets" / "vid").mkdir()
        for section in self.world["sections"]:
            item_id = section["id"]
            raw = self.root / section["outputs"]["raw_final"]
            still = self.root / section["outputs"]["still"]
            raw.parent.mkdir(parents=True, exist_ok=True)
            still.parent.mkdir(parents=True, exist_ok=True)
            self.run_cmd([
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=160x90:d=1:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw),
            ])
            self.run_cmd([
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=160x90", "-frames:v", "1", str(still),
            ])
        self.write_world()
        self.run_cmd([sys.executable, str(SCRIPTS / "media-pipeline.py"), "--world", str(self.world_path)])
        media = json.loads((self.root / ".work" / "qa" / "media-report.json").read_text(encoding="utf-8"))
        self.assertEqual(media["schema_version"], 2)
        self.assertEqual(media["seams"][0]["verdict"], "PASS")
        self.assertIn("motion_jump_ratio", media["seams"][0])
        self.assertEqual(len(media["automatic_quality"]), len(self.world["sections"]))
        self.assertTrue(all("black_frame_ratio" in item and "still_to_first_ssim" in item for item in media["automatic_quality"]))
        self.run_cmd([sys.executable, str(SCRIPTS / "qa-assets.py"), "prepare", "--world", str(self.world_path)])
        for section in self.world["sections"]:
            self.run_cmd([
                sys.executable, str(SCRIPTS / "qa-assets.py"), "review", "--world", str(self.world_path),
                "--section", section["id"], "--status", "pass", "--notes", "synthetic regression fixture",
            ])
        self.run_cmd([sys.executable, str(SCRIPTS / "qa-assets.py"), "check", "--world", str(self.world_path)])
        report = json.loads((self.root / ".work" / "qa" / "semantic-report.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["status"] == "pass" for item in report["sections"].values()))

    def test_eval_and_trigger_sets(self):
        evals = json.loads((SKILL / "evals" / "evals.json").read_text(encoding="utf-8"))
        triggers = json.loads((SKILL / "evals" / "trigger-evals.json").read_text(encoding="utf-8"))
        self.assertEqual(evals["skill_name"], "scroll-world")
        self.assertGreaterEqual(len(evals["evals"]), 3)
        self.assertEqual(len(triggers), 20)
        self.assertEqual(sum(item["should_trigger"] for item in triggers), 10)
        self.assertTrue(all(item.get("expectations") for item in evals["evals"]))
        engine = (SKILL / "references" / "scrub-engine.js").read_text(encoding="utf-8")
        template = (SKILL / "references" / "index-template.html").read_text(encoding="utf-8")
        self.assertIn("window.location.protocol === 'file:'", engine)
        self.assertIn("config.directVideo === true", engine)
        self.assertIn("new URL(url, document.baseURI).href", engine)
        self.assertIn("directVideo: true", template)
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("allowed-tools:", skill_text.split("---", 2)[1])
        self.assertLessEqual(len(skill_text.splitlines()), 200)
        interface = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("$scroll-world", interface)
        themes = json.loads((SKILL / "assets" / "themes" / "index.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(themes["themes"]), 6)


if __name__ == "__main__":
    unittest.main()
