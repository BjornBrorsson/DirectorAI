# DirectorCut-AI — Master Memories

> Reusable bugs, platform quirks, and workarounds. Read before debugging or adding an integration.

## Entry template (Two-Strike Rule)

### <short title>

* **Verified:** YYYY-MM-DD
* **Context:** when it happens
* **Symptom:** what you see
* **Fix:** the durable workaround
* **Example:** minimal snippet

> Add it the second time you hit it — or the first if it cost more than ~30 minutes.

---

## Entries

### gemini CLI on Windows is a .cmd shim

* **Verified:** 2026-09-17
* **Context:** running the `gemini` CLI via `subprocess.run([path, ...])` on Windows (npm install puts `gemini`, `gemini.cmd`, `gemini.ps1` in `%APPDATA%\npm`).
* **Symptom:** `shutil.which("gemini")` returns the extensionless bash shim; `CreateProcess` can't exec it (or `gemini --version` just hangs in interactive mode). `.cmd` files fail the same way.
* **Fix:** prefer the `gemini.cmd`/`.exe` sibling of whatever `which` returns, and wrap `.cmd`/`.bat` in `cmd /c`. See `gemini_client._find_cli_binary` / `_cli_command`.
* **Example:** `["cmd", "/c", r"C:\...\npm\gemini.CMD", "--output-format", "json", "-p", prompt]`

### gemini CLI stdout isn't pure JSON

* **Verified:** 2026-09-17
* **Context:** `gemini -p ... --output-format json` in non-interactive mode.
* **Symptom:** stdout carries log lines ("Loaded cached credentials.", download notices) before the JSON envelope; the model's `response` field itself may be wrapped in a ` ```json ` fence.
* **Fix:** `raw_decode` from the first `{` for the envelope, then strip the code fence before parsing `.response`. Also: unauthenticated CLI exits 1 with a `code_assist` setup error - the user must run `gemini` once interactively (or set a project/API key) before the backend works.

### gemini CLI free-tier OAuth is retired - use agy

* **Verified:** 2026-09-18
* **Context:** `gemini` CLI 0.35.1 AND 0.60.0 on a Google-account (Code Assist free tier) login.
* **Symptom:** every call exits 1: `IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals ... migrate to the Antigravity suite`.
* **Fix:** switch to `GEMINI_BACKEND=antigravity` + the `agy` CLI (`irm https://antigravity.google/cli/install.ps1 | iex` → `%LOCALAPPDATA%\agy\bin`), or pass `GEMINI_API_KEY` which the gemini CLI also accepts.

### agy --json-schema captures tool calls, not the answer

* **Verified:** 2026-09-18
* **Context:** `agy -p ... --output-format json --json-schema <file>` headless runs.
* **Symptom:** `structured_output` holds the args of whatever structured step finished last (e.g. an internal `finish_task` tool call like `{"role":"Video Classification Specialist"}`), and `response` is prose + serialized tool-call JSON - never the schema payload.
* **Fix:** don't pass `--json-schema`; embed the schema in the prompt ("Respond with ONLY a JSON object matching this JSON schema"), then parse `response`: try the whole body, then every embedded `{...}` object, and take the LAST one that validates (tool-call junk sorts earlier). See `gemini_client._parse_agy_response`.

### agy headless soft-denies tools - empty response, SUCCESS status

* **Verified:** 2026-09-18
* **Context:** `agy -p` runs where the agent wants `command` (e.g. to inspect an image file in its workspace).
* **Symptom:** run exits 0, envelope shows `status: SUCCESS` but `response` is empty and `denied_actions` lists the blocked tool (stderr: `a tool required the "command" permission ... auto-denied`).
* **Fix:** pass `--dangerously-skip-permissions` (our `ANTIGRAVITY_SKIP_PERMISSIONS`, default on) or add `permissions.allow` rules in `~/.gemini/antigravity-cli/settings.json`. Media files must live under the subprocess `cwd` - workspace reads are auto-allowed there. Related: each file the agent opens costs a tool round-trip (~3min under load) - multi-frame media is tiled into ONE `media_sheet.jpg` contact image (`gemini_client._write_agy_media`) or Pass 2 blows the 15-min print-timeout. Also ask the agent to write `result.json` - its final turn is often a tool call, leaving `response` empty.


### WinError 206 - Windows caps CreateProcess args at ~32K; pass long prompts as workspace files

* **Verified:** 2026-09-18
* **Context:** the director pass embeds the narrative map in the `agy -p` argument; unscripted tiling raised it to 100+ nodes (~40KB).
* **Symptom:** `GeminiError: Could not run agy CLI: [WinError 206] The filename or extension is too long` right at the Pass-3 call - every earlier pass had already succeeded.
* **Fix:** write `full_prompt` to `prompt.txt` inside the agy workspace and pass `-p` a short "read prompt.txt and follow it" wrapper (`gemini_client._run_agy_once`). The agent reads workspace files itself; one extra tool turn beats a prompt-size ceiling. Applies to any CLI subprocess call on Windows, not just agy.

### FCPXML `<video>` is video-only; assets need `duration`/`format` to seek

* **Verified:** 2026-09-18 (Resolve import of annotest.fcpxml)
* **Context:** primary spine clips emitted as `<video>` elements on assets declared with only `src`/`start`/`hasAudio`/`hasVideo`.
* **Symptom:** Resolve showed every clip as a frozen frame-1 still with a silent timeline - `<video>` never carries audio, and without `duration`/`format` on the `<asset>` the rational `start` can't map to frames.
* **Fix:** primary visual cuts emit `<asset-clip>` (audio+video together); `_probe_asset()` ffprobes each asset for real duration + stream kinds and emits `duration=`/`format=` on the asset element. Overlays intentionally stay `<video>`.

### Unscripted dumps need full-duration narrative coverage

* **Verified:** 2026-09-18
* **Context:** livestreams/gameplay captures arrive with no script file - the stated common use case.
* **Symptom:** `transcribe()` emitted ONE 5s placeholder node ("Voiceover without script."), so Pass 3 could only cut a ~5s window: the FCPXML was "a 4 second clip of nothing" on a 1h42m source, with the mp3 on V1 and the video on the B-roll lane.
* **Fix:** `transcript._unscripted_segments()` tiles `manifest.duration_seconds` into 60s segment nodes; `tag_narrative` is skipped (nothing to tag); `_plan_to_blueprint` enforces `_TRACK_EXTS` so audio can never land on the V2 overlay lane again.

### Windows "Copy as path" produces literal quotes - sanitize before Path()

* **Verified:** 2026-09-18
* **Context:** User pasted `"C:\Users\Björn\Videos\2026-04-10 00-00-39.mp4"` (with quotes) into the upload form.
* **Symptom:** API reported `file not found` for an existing file - `Path('"C:\..."').is_file()` treats the quote characters as part of the path.
* **Fix:** `api.main._clean_path` strips one matching outer `"`/`'` pair, then `expandvars`/`expanduser`, before every `Path()` on user input. Also applies to `/browse`. Test: `test_ingest_accepts_quoted_windows_path`.

### .env values leak into test Settings() - pin backend/env in fixtures

* **Verified:** 2026-09-18
* **Context:** repo `.env` sets `GEMINI_BACKEND=antigravity` + `DIRECTORCUT_ENV=prod`.
* **Symptom:** `test_retries_on_503_then_succeeds` (respx-mocked HTTP) routed through `_call_agy` instead - the `live_settings` fixture inherited `gemini_backend` from `.env` because pydantic-settings reads it for unset fields.
* **Fix:** fixtures pin what they need (`gemini_backend="api"`, `env="dev"` via `model_copy` on `api_main.settings`). Any `Settings()` without explicit backend/env follows the local `.env`.

### Never run the pipeline on the event loop - WS handler blocked the whole API

* **Verified:** 2026-09-18
* **Context:** `run_pipeline` was called synchronously inside the `async` websocket handler (`api/main.py::ws`).
* **Symptom:** during any run the entire API froze - /health, /browse, other sockets all stalled for the pipeline's full duration; on a ~1h42m ingest the UI looked dead for ~1h.
* **Fix:** `_RunState` registry + `_start_run` daemon thread (`api/main.py` ~L293); both the WS and POST /run attach to the shared run. `_stream_run_events` replays the event log then live-tails, so reconnecting mid-run reattaches instead of starting a duplicate. `run_pipeline(on_progress=)` emits `{stage, detail, elapsed_s}` events per phase + per Pass-2 chunk.

### Integer-truncate rates before chunk-capacity math, never after

* **Verified:** 2026-09-18
* **Context:** `token_budget.chunk_proxy` sized `frames_per_chunk` with `max_tokens // int(263*1.05)`.
* **Symptom:** a max-capacity chunk *estimated* 950,568 tokens (float rate) vs 950,000 ceiling → `BudgetError` on a ~1h42m file at the FIRST chunk.
* **Fix:** divide by the untruncated float: `int(max_tokens / (TOKENS_PER_360P_FRAME * VIDEO_TOKEN_OVERHEAD))`. Sizing and estimating must use the same precision.

### FCPXML sequence without tcStart scatters clips by source time in Resolve

* **Verified:** 2026-09-18 (Resolve import of annotest.fcpxml, playhead read -01:29:52:28)
* **Context:** sequence emitted without `tcStart`/`duration`/`tcFormat`; spine offsets ran 0→137s.
* **Symptom:** ~1h of dead timeline, then clips clustered at the end at *source-time* positions (playhead showed negative source-derived timecode). Resolve defaults `tcStart` to broadcast ~3600s; offsets below it get re-anchored via `start` (the media in-point).
* **Fix:** emit `tcStart="0s"`, `duration`, `tcFormat="NDF"`, `audioLayout`, `audioRate` on `<sequence>`; `enabled="1"`/`tcFormat`/`format` on clips; `audioSources`/`audioChannels`/`audioRate` on `<asset>`; `<!DOCTYPE fcpxml>` header — mirror Resolve's own export shape.

### FCPXML connected-clip offset is in the PARENT's timebase (parent `start` + local), not local

* **Verified:** 2026-09-21 (Resolve import of sts2-ea-review v1/v2: SFX/overlays/nested VO landed up to -7:43 before the timeline)
* **Context:** `conformer/fcpxml.py::attach_video/attach_audio` emitted `offset = timeline_in - parent_timeline_in`.
* **Symptom:** children of the first spine clip (start≈0) look right; children of later clips land at `timeline_in - source_in` — negative when the cut trims heavily. Reads as "stray clips before 0:00 with no audio" and "voice stops after the first clip".
* **Fix:** `offset = parent.start + local` (Apple's own samples: `<asset-clip offset="0s" start="3600s"><asset-clip lane="1" offset="3600s"/>`). `<gap>` parents have no `start`, so their children keep a plain local offset. Any post-processor nesting children must do the same.

### pydantic-settings: two fields claiming one env alias cross-feed init kwargs

* **Verified:** 2026-09-22
* **Context:** `gemini_api_key`'s `AliasChoices` ("GEMINI_PAID_KEY_MAIN", "GEMINI_API_KEY", "GEMINI_PAID_KEY") and the dedicated `gemini_paid_key_main`/`gemini_paid_key` fields deliberately share env vars (the dual-claim keeps `Settings.gemini_api_key` backwards-compatible while key_chain() reads the dedicated fields).
* **Symptom:** originally `KeyError: 'GEMINI_PAID_KEY_MAIN'` inside `_settings_restore_init_kwarg_names` — surfaced as dozens of unrelated Settings() setup errors. Residual quirk (verified): passing a *field-name* kwarg restores it to its alias for downstream sources, so `Settings(gemini_paid_key='x')` also sets `gemini_api_key`/`gemini_paid_key_main` to 'x'. Plain `Settings()` + env vars resolves correctly.
* **Fix:** in tests always `Settings(_env_file=None, <explicit fields>)` so a real `.env` (which carries live keys) can't leak into assertions; never rely on one kwarg to isolate a sibling key field. If a dedicated field ever needs full independence, remove its var from `gemini_api_key`'s AliasChoices — but update `test_gemini_paid_key_main_outranks_other_aliases`, which pins the dual-claim.

### Spine clips must be `<asset-clip>`; `<video>` is only safe as a connected clip. Resolve ignores `<param name="volume">` keyframes.

* **Verified:** 2026-09-22 (Resolve import of sts2-ea-review v2)
* **Context:** to silence a clip's embedded mic I converted spine `<asset-clip>`s to `<video>` and nested the fixed voice on lane -1; music/SFX ducking relied on `<param name="volume"><keyframeAnimation>`.
* **Symptom:** A-roll rendered as frozen stills, clips placed with gaps (source-time-ish spacing), no ducking audible. Connected `<video lane="1">` B-roll on the same timeline played fine.
* **Fix:** never change the spine tag. If the embedded audio must be replaced, remux the media (`-c:v copy` + new audio track) and point the asset at the new file. For level automation, pre-render the bed (ffmpeg `sidechaincompress` against a timeline-placed voice guide) and attach it as one plain `<audio>` clip — see `tasks/sts2-ea-review-run/render_audio.py`.

### A non-reentrant Lock around a flush that reads the same state deadlocks

* **Verified:** 2026-09-23
* **Context:** `CostLedger._append` held `self._lock` while calling `flush()`, which calls `totals()`, which reacquires `self._lock` (a plain `threading.Lock`). Surfaced as `test_resume` hanging forever (124s timeout), not an error.
* **Symptom:** tests that exercise the ledger pass in isolation but the suite stalls mid-run; no traceback.
* **Fix:** take the lock only for the mutation (`self._calls.append`), release it, THEN flush/aggregate. If the persist path must read state under lock, either snapshot first or use `threading.RLock` - never let a locked section call a public method that locks again. See `safety/ledger.py::CostLedger._append`.

### str.format on a prompt template containing literal JSON braces raises KeyError

* **Verified:** 2026-09-23
* **Context:** `PATCH_PROMPT.format(listing=..., ...)` in `cutops.interpret_patch` - the template shows the model example ops like `{"op": "drop_cut", "index": N}`.
* **Symptom:** `KeyError: '"ops"'` at prompt-build time - format() treats every `{...}` as a field name, including the literal JSON in the examples.
* **Fix:** substitute placeholders with `.replace("{listing}", ...)` instead of `str.format` whenever a template embeds literal JSON braces; doubling every brace in the prompt works but makes the template unreadable. Applies to any prompt with inline JSON examples.

### Rotated/portrait A-roll mis-transforms in Resolve - bake the reframe into the media, never into FCPXML params

* **Verified:** 2026-09-22 (sts2-ea-review A-roll: 3840x2160 with rotation side-data; Resolve applied adjust-transform on the rotated frame)
* **Context:** Resolve mis-applies `adjust-transform` on media carrying rotation side-data or non-sequence aspect - the clip lands pillar-boxed, offset, or with the transform computed in the wrong frame space.
* **Symptom:** spine clips show with wrong framing/crop in Resolve even though the FCPXML transform values look right; the fix scales unpredictably per clip.
* **Fix:** bake at ingest (`media_prep.prepare_aroll_media`): probe `stream_side_data=rotation`, ffmpeg autorotates on decode so `crop`+`scale` run in the rotated frame space, encode a flat 1920x1080 h264 with no rotation metadata and point the asset at the baked file (`Asset.derived_from` keeps the original). For the proven 3840x2160 rot=-90 case the working filter was `crop=2160:1215:0:1024,scale=1920:1080` (face-biased crop ~39% down the slack). Same pattern for audio fixes: remux (`-c:v copy`) rather than FCPXML trickery.

### click.prompt(hide_input=True) hangs on piped stdin on Windows

* **Verified:** 2026-09-23
* **Context:** `directorcut setup` wizard collects API keys with hidden prompts.
* **Symptom:** piping answers via stdin (`echo k | directorcut setup`) deadlocks - win_getpass reads the console through msvcrt, never touching the pipe.
* **Fix:** hide input only when `sys.stdin.isatty()`; on piped stdin use a visible prompt. See `cli.py` setup command.

### API-key failover order and the agy last resort

* **Verified:** 2026-09-23 (sts2-review-final run: both paid keys 402 -> free key served; retry chain visible in final_run.log)
* **Context:** `Settings.key_chain(task)` orders keys per task - visual/interpret burn GEMINI_FREE_KEY first, heavy tasks run PAID_MAIN -> PAID -> FREE; `model_chain(task)` fails over models on the same key before rotating keys; 401/402/403 skip the key instantly; a 400 aborts (request bug, not a credential problem).
* **Symptom:** without these rungs a single exhausted key or quota'd model kills a 2h run.
* **Fix:** the rungs live in `GeminiClient._call_api`; after ALL keys x models die, `DIRECTORCUT_BACKEND_FALLBACK=antigravity` retries via `_call_agy` (its own auth survives API outages) - `_ChainExhaustedError` marks total exhaustion vs request bugs. Free-tier `gemini-3.8-flash` carries a ~20 req/day cap that resets at midnight Pacific - keep it late in chains when paid keys are dead, or each batched call burns ~10min of doomed retries first.

### Scribe words[] contains zero-duration and duplicate words on long captures

* **Verified:** 2026-09-27 (sts2-review-v3: 118-min stream transcript, 23k words, 1,647 zero-duration entries)
* **Context:** ElevenLabs Scribe `words[]` on multi-hour audio - echoed re-takes land two word entries on the same timestamp ("Yeah." and "Yeah," both at 2528.754), and `spacing` rows carry `end == start`.
* **Symptom:** `NarrativeNode` construction fails with `time_end must be after time_start` - in `narrative/stt._words_to_nodes` AND a second strike in the tagger's `_NarrativeNodeList` response model, where the model re-emitted a bad timecode.
* **Fix:** `_words_to_nodes` floors every node span at one frame (0.04s) and accumulates `node_end` with `max()`; the tagger parses a lenient `_TaggedNode` (node_id/tag/energy only) and callers preserve ORIGINAL timecodes. Never let model-emitted timestamps pass strict node validation - discard and keep the source of truth.

### Gemini structured output truncates at the output-token cap - repair the boundary, don't die

* **Verified:** 2026-09-27 (v3 tagger batch: `Unterminated string starting at line 5`)
* **Context:** large `response_json_schema` replies get cut mid-string when the emit budget runs out; `json.loads` raises `JSONDecodeError`.
* **Symptom:** a single malformed response killed the whole run - the error bypassed the key x model failover chain entirely.
* **Fix:** `gemini_client._repair_truncated_json` closes the open string + containers, then walks back to the last element boundary (bounded to 10 cuts) - list consumers like the tagger tolerate shortened batches anyway. Unrepairable/invalid output now raises `_ApiCallError` so sibling models get a shot instead of crashing.

### ffmpeg -map 0:v:0 on audio-only inputs - branch the map

* **Verified:** 2026-09-27 (media_prep normalize on extracted `*_streamN.mp3` files)
* **Context:** `media_prep._bake` hardcoded `-map 0:v:0` - an ffmpeg idiom that silently assumed every input had video.
* **Symptom:** `Stream map '0:v:0' matches no streams` when normalizing voiceover mp3s.
* **Fix:** probe codec types first; audio-only inputs get `-map 0:a:0` and no video codec args. `tests/test_media_prep.py` has the regression.

### Source-overlap tolerance: trim the sliver, drop the crime

* **Verified:** 2026-09-27 (v3 blueprint: two V1 cuts on `Alternate_reframed` shared 0.24s of source)
* **Context:** `_SOURCE_OVERLAP_TOLERANCE_S` (0.25s) let adjacent V1 cuts reuse a tail - audible stutter ("the-the") on the spine - while a naive fix (drop) would sacrifice a 12s cut over a sliver.
* **Symptom:** sub-second source reuse survived validation all the way to FCPXML.
* **Fix:** `_enforce_unique_source` now head/tail-trims any cut whose in/out lands inside an accepted range (timeline shifts with source, speed stays 1:1, `compact_timeline` repacks). Drops only happen on interior containment or post-trim residual > tolerance. Also re-run the enforcer after `pad_v1_cut_edges` - edge padding can re-open reuse the director pass already cleaned.
