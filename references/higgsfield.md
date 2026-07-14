# Higgsfield provider contract

Use this reference only when `project.provider=higgsfield`. The integration follows the official
Higgsfield CLI and Skills repositories checked on 2026-07-14:

- CLI: <https://github.com/higgsfield-ai/cli>
- Official skills: <https://github.com/higgsfield-ai/skills>
- Agent entry page: <https://higgsfield.ai/skills>

## One-time installation and authentication

Higgsfield's current official agent/CLI flow uses browser OAuth, not a pasted API key. Install the
official CLI only with user approval:

```bash
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh -s -- --prefix="$HOME/.local"
higgsfield version
higgsfield auth login
higgsfield workspace list
higgsfield workspace set <workspace_id>
higgsfield account status
python3 "$SW/scripts/provider_config.py" configure --provider higgsfield
```

`higgsfield auth login` persists its own credentials under the user's Higgsfield config. Never copy
that credential file into a project or `.skill` package, never print its token, and never call
`api.higgsfield.ai` directly. If the session expires, stop and ask the user to run the login command
again; do not switch to Doubao automatically.

If `account status` says no workspace is selected, list available workspaces and ask the user which
workspace to activate. Never choose one on their behalf because it changes the billing/account scope.

Higgsfield also publishes server SDKs with key/secret credentials, but this skill intentionally uses
the official agent CLI because it supplies schema validation, local-file upload, retries, polling and
the current multi-model catalog. Do not ask for `HF_KEY`, `HF_API_KEY` or `HF_API_SECRET` in this flow.

## Mechanical capability preflight

The model roster changes. `scroll-world.py run` and `doctor --world` invoke the adapter preflight
before any budget reservation. It checks authentication, the unfiltered catalog and selected schemas,
then writes a 24-hour, model-fingerprinted cache to `.work/provider-capabilities.json`:

```bash
higgsfield model list --json
higgsfield model get nano_banana_2 --json
higgsfield model get seedance_2_0_mini --json
higgsfield model get seedance_2_0 --json
```

Default mapping:

| Stage | Model | Parameters |
|---|---|---|
| still | `nano_banana_2` | 2K, project aspect ratio, repeated `--image` references |
| detailed preview | `seedance_2_0_mini` | 720p, 4–15s, start/end/reference images |
| final / fast | `seedance_2_0` | 1080p, `mode=std`, 4–15s, start/end/reference images |

If a live schema no longer supports a required role, stop before reserving budget and ask the user
whether to choose another currently listed model. Never silently drop start/end-frame constraints.

## Adapter behavior

`scripts/higgsfield_adapter.py` is the only project-facing wrapper:

```bash
python3 "$SW/scripts/higgsfield_adapter.py" image PROMPT OUTPUT --reference REF
python3 "$SW/scripts/higgsfield_adapter.py" preflight --world "$WORLD" --refresh
python3 "$SW/scripts/higgsfield_adapter.py" video-submit final PROMPT FIRST TASK_DIR "" 16:9 5 --reference-image STILL
python3 "$SW/scripts/higgsfield_adapter.py" video-poll TASK_DIR RAW_VIDEO LAST_FRAME
```

The adapter:

- requires `higgsfield generate cost` to validate the exact flags before reserving budget; records the
  estimate without counting it as a generation;
- reserves the shared generation budget before `generate create`;
- stores the returned job ID and resumes with `generate get`, never blindly resubmitting;
- downloads only HTTPS result URLs;
- extracts the actual last frame from the downloaded MP4 with `ffmpeg` for the next seam;
- records provider, model, request count, `credits_actual`, `credits_estimated` and source separately;
- never stores OAuth credentials in project metadata.

`higgsfield generate create ... --wait` is used for stills. Video jobs remain asynchronous so a long
generation can be resumed without holding the agent turn open. Statuses `queued`, `pending`,
`running`, `in_progress` and `processing` are non-terminal; `failed`, `nsfw`, `canceled` and
`cancelled` require an explicit retry decision.

## Cost reporting

Higgsfield charges platform credits by model/settings. Preserve actual and estimated credits separately. Convert credits
to CNY only when the user supplies a current official `pricing.higgsfield.cny_per_credit` snapshot;
subscription credits do not imply a stable cash price. Report both actual/estimated credits and any
missing conversion reason in `COSTS.md`.
