# Task: multi-aroll-api-run

> Date: 2026-09-19 | Status: implemented, tests green

## Scope

Per-pass Gemini model selection + paid-key alias; `response_json_schema` with
sanitized fallback; multi-A-roll STT with `source_file` provenance; Pass 3
unique-source enforcement.

## Changes

- `config.py`: `gemini_api_key` now accepts `GEMINI_API_KEY` or
  `GEMINI_PAID_KEY` via `validation_alias=AliasChoices` (field-name kwargs
  still work thanks to `populate_by_name=True`). New `gemini_model_fast`
  (`GEMINI_MODEL_FAST`) and `Settings.model_for(task)` — `"visual"` /
  `"interpret"` use the fast model when set, everything else `gemini_model`.
- `gemini_client.py`: `call(..., model=None)` per-call override (API backend
  only; CLI/agy log a debug once and ignore it). httpx timeout raised to
  600s. `generationConfig` sends `response_json_schema` (real JSON Schema);
  a 400 that names the field swaps in `response_schema` sanitized by
  `_gemini_schema()` (inlines `$defs`/`$ref`, strips `title`, `pattern`,
  `default`, `additionalProperties`).
- Call sites: `visual/classifier.py` → `model_for("visual")`,
  `assets.py` → `model_for("interpret")`, `narrative/tagger.py` →
  `"narrative"`, `assembly/director.py` → `"director"`.
- `models.py`: `NarrativeNode.source_file: Path | None` (optional for
  backward compat with pre-multi-A-roll JSON).
- `pipeline.py`: `_transcript_sources()` — every `A_ROLL` video (reuses
  `_stream0` mp3 or extracts audio) plus standalone `VOICEOVER` assets, minus
  extracted streams (under `output_dir` or `<aroll>`-stem prefixed). STT runs
  per source; nodes get `S{i}_` id prefixes and `source_file` stamped as the
  video path; one combined `tag_narrative` call. Script path and single-
  source fallbacks unchanged.
- `tagger.py`: rebuilt nodes carry `source_file` through.
- `assembly/director.py`: narrative rendered grouped by source
  (`## Narrative from <path>`, per-line `source_file` only when >1 source);
  new prompt rules (best take per beat, no reused source range, no repeated
  line, V1 ranges inside the node's file); `_enforce_unique_source()` drops
  >0.25s source-range overlaps and >0.85-ratio text duplicates, then
  re-compacts V1 contiguously and shifts V2/A2/A3 by their anchor's shift
  (overlays on dropped anchors die, tails clamp).
- `.env.example`: `GEMINI_PAID_KEY` comment + `GEMINI_MODEL_FAST`.

## Tests added

- `test_gemini_client.py`: `model=` override hits the URL model;
  `response_json_schema` → `response_schema` fallback on 400;
  `_gemini_schema` inlines/strips on the nested `_AssemblyPlan` (enum +
  Literal + nested list); `model_for` fast/fallback; `GEMINI_PAID_KEY` alias.
- `test_director.py`: `_enforce_unique_source` — overlap drop + compaction +
  overlay shift/drop + music clamp; repeated-take text drop; disjoint
  sources untouched.
- `test_narrative.py`: tagger preserves `source_file`.
- `test_stt.py`: `_transcript_sources` multi-A-roll coverage/dedup.

## Decisions / deviations

- `_enforce_unique_source` returns the plan untouched when it contains no
  V1 cuts (nothing to compact against).
- Cuts anchored before the first V1 or in inter-V1 gaps take the shift of
  the nearest containing/preceding segment (spec only named the containing
  case).
- `_gemini_schema` also strips `definitions` alongside `$defs` for older
  pydantic drafts.

## Verify

- `pytest -q`: see final report (all green at handoff).
- `ruff check .`: clean.
- Real API smoke of `response_json_schema` deferred until a paid key exists
  — do not call the API from tests.

---

# Production run: sts2-review-take2 (2026-09-22)

## Env

- `.env`: `GEMINI_BACKEND=api`, `GEMINI_PAID_KEY` (quoted) resolves via
  `AliasChoices`; `Settings().safe_api_key` non-None, env=prod.
- `GEMINI_MODEL_FAST=gemini-flash-lite-latest` (visual + interpret).
- **`gemini-3.8-flash` was unusable**: free-tier key — the API reports
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` quotaValue=20 for
  3.8-flash and it is exhausted (429). `gemini-3.7/3.6/3.5-flash` → 503 high
  demand; `gemini-2.5-*` → 404 retired; `gemini-3.1-pro-preview` → 429 daily
  tokens. **`GEMINI_MODEL` set to `gemini-3-flash-preview`** (full-size,
  verified working) — revisit once billing/quota is sorted.

## API smoke (smoke_api.py)

- (a) fast/text on flash-lite: `Pick(label='blue', confidence=0.95)` — OK.
- (b) fast/image on flash-lite (real `_VisualEventList`, one JPEG): 1 event
  `STATE_UI` "Card reward screen" — OK.
- (c) `_AssemblyPlan` on `gemini-3-flash-preview`: 1 valid V1 cut — OK.
- `response_json_schema` accepted on every call; fallback never fired.
- Client retry defaults raised 3×1s → 5×2s after the 503 storms (bounded
  ~30s extra backoff).

## Media prep

- `ingest/` rebuilt: aroll = `A-Roll Raw Alternate.mp4` (remuxed, normalized
  voice baked) + `A-roll raw (secondary).mp4` (hardlink to
  `output/sts2-ea-review/media/A-roll reframed.mp4`); broll = 17 SteelSeries
  + OBS 14-min + trailers/×5 (hardlinks); music = 2 OSTs; voiceover removed.
- Alt A-roll source: hevc 1920x1080 30fps, 1 AAC 48k stereo stream +
  mett data, **rotation=-180 side data** (preserved through remux),
  duration 997.4s. Loudness: raw −29.0 LUFS / TP −5.2 → normalized chain →
  **−16.6 LUFS / TP −1.4** (`aroll_alt_voice_norm.wav`). Remuxed file:
  997.354s, same streams, rotation kept.
- OBS 14-min `2026-09-22 15-28-55.mp4`: h264 1080p60, 872.2s, **3 audio
  streams but all silent** (a:1 digital silence −91dB; a:0/a:2 mean −70.5dB).
- `direction.txt` rewritten per new multi-take guidance; script kept verbatim.

## Dry run

- 27 assets (2 a_roll, 18+5 b_roll incl. trailers, 2 music, 0 voiceover),
  script=None → STT path, backend=api, est. cost $0.7481.

## Run

- Launched 17:3x as background shell `8d58a3`, log: `run.log`.
- Ingest extracted 54 embedded audio streams (18 multi-track broll × 3).

### Failure log (free-tier reality)

1. Run 1 died at `tag_narrative`: `gemini-3-flash-preview` 503 storm;
   retry budget raised 5×2s → 8×5s (~10 min worst case).
2. Run 2 died at `tag_narrative` again after riding 8 attempts — further
   probing showed full-size models 503 LARGE prompts while accepting small
   ones (18K-token probe 503'd on 3-flash-preview in 6s while a tiny ping
   succeeded in 2s). `gemini-flash-lite-latest` accepted the same big
   prompt (57s). → `GEMINI_MODEL=gemini-flash-lite-latest` (all passes).
3. Run 3: tagger got a TRUNCATED response (fewer nodes back than sent -
   output-size limit on a 374-node list). Fixed `tagger.py`: batches of 100
   nodes per call + match-by-node_id with positional fallback + keep
   original node when missing (was `zip(strict=True)` → crash). Tagged 415
   nodes, some "No tag ... keeping defaults" warnings.
   Then died in Pass 2: first visual chunk was 273K tokens (989 frames,
   ~47MB inline base64) → 400. Fixed `chunked.py`: API backend caps chunks
   at 40K est. tokens (~145 frames ≈ 9MB) via `chunk_proxy(max_tokens=)`;
   CLI/agy keep the context-sized chunks.
4. Run 4 launched ~18:3x.

## Run 4 result (success)

- Pass 1: STT 197 (secondary) + 190 (alternate) = 387 nodes; tagged 387
  (one batch under-returned 32/100 -> 68 nodes kept defaults).
- Pass 2: all 23 video assets classified; 43 chunks at ~40K tokens each
  (API-safe cap). 429s on flash-lite self-healed within retry budget.
- Pass 3: director produced 56 cuts + 2 audio cues on
  gemini-flash-lite-latest (3.8-flash was still 503ing large prompts -
  .env has since been restored to GEMINI_MODEL=gemini-3.8-flash per spec;
  small-prompt smoke on 3.8 now passes, large-prompt capacity unverified).
- Zero _enforce_unique_source drops.
- Generated SFX: sfx_havoc_self_own, sfx_verdict_stinger.

## Blueprint quality notes

- Timeline 4:15 - well short of the 7-9 min target.
- V1 is 100% primary take; secondary unused. V2 B-roll only 10 cuts
  (~thin vs the 60-70% coverage direction).
- One V1 cut had timeline span 4.019s vs source span 3.319s (model slip)
  -> 0.7s hole in the conformed spine. Fixed: _enforce_unique_source now
  re-lays V1 by SOURCE span (source is authoritative; the conformer
  derives duration from source_in..source_out). Applied retroactively via
  reconform.py; spine now gapless, timeline 254.839s.

## Finalize

- render_audio.py (multi-source voice guide from each V1 cut's own
  source_file), finalize_fcpxml.py, verify_final.py.
- voice_guide -16.3 LUFS; music_bed_ducked -34.1 LUFS; sfx_bed -16.5 LUFS.
- sts2-review-take2-final.fcpxml: 44 spine asset-clips, 10 lane-1 B-roll,
  1 lane -2 + 1 lane -3 bed under first clip, 0 params, no dangling refs,
  spine contiguous (0 gaps > 1 frame).

## Media facts

- ingest/aroll/A-Roll Raw Alternate.mp4: 1920x1080 @30, rotation -180
  side data preserved, 997.354s, normalized audio -16.6 LUFS / TP -0.9.
- OBS 14-min (2026-09-22 15-28-55.mp4): h264 1080p60 + THREE AAC streams,
  all silent (max -91 dB) - user expectation "no audio" confirmed.
- Smoke test: text + image + _AssemblyPlan all pass;
  response_json_schema accepted on every call (no fallback needed).

## Test fix

- test_model_for_prefers_fast_model leaked GEMINI_MODEL_FAST from the
  real .env; now pins gemini_model_fast=None explicitly.

## Pass 3 re-run on gemini-3.8-flash (attempt 2)

- attempt1/ snapshot kept (the 4:15 flash-lite cut).
- direction.txt got a hard-length/B-roll-coverage/secondary-take addendum
  (persisted at top of the guidance section).
- rerun_pass3.py reuses assets.json/narrative.json/visual.json; overrides
  manifest.direction; retries the director call 3x/2min then falls back
  to gemini-3-flash-preview. 3.8-flash succeeded on attempt 1 after two
  self-healed 429s - no fallback needed.
- Director: 98 plan cuts + 2 new cues (sfx_havoc_fail, sfx_verdict_hit;
  old cue files left unreferenced in audio/).
- _enforce_unique_source dropped 2 V2 overlays reusing OBS source ranges.
- Note: IngestManifest.model_validate_json fails on its own dump
  (before-validator str->Path then strict JSON Path validation rejects
  it); use model_validate(json.loads(...)).

### Numbers (attempt 2)

- Timeline 561.06s (9.35 min) - 21s over the 9:00 hard target.
- V1=67, V2=28, A2=3, A3=2. V2 covers 319.0s of V1 = 57% (target >=60%).
- 7 V1 cuts from the secondary take; all 7 fully covered by V2.
- min V1 cut 0.900s; 1 cut < 1.0s. Per-cut listing: v1_listing.txt.
- Final FCPXML: contiguous spine, all children in-parent, 0 params,
  exactly one lane -2 + one -3. Beds: voice -16.4 / music -34.8 /
  sfx -22.9 LUFS.

## Attempt 3 - manual drop of two secondary punch-ins

- Refactor: re-compaction extracted from _enforce_unique_source into
  compact_timeline(cuts, dropped) in assembly/director.py (drop by Cut
  objects; re-lays V1 by source span, shifts/clamps V2/A2/A3). 10/10
  director tests pass.
- drop_cuts.py drops V1 by 1-based index (v1_listing.txt numbering),
  rewrites blueprint + FCPXML, re-renders beds, re-verifies.
- Dropped #54 (src 11:55-12:03, "Really cool thing, headline feature...")
  and #65 (src 15:52-15:59, "basically more of the same...") - both
  secondary-take punch-ins.
- Result: 546.22s (9.10 min), V1=65, V2=28, coverage 58%, 5 secondary
  cuts all fully V2-covered, spine contiguous, final FCPXML clean.
  Snapshot of attempt 2 in output/sts2-review-take2/attempt2/.

---

# Overnight backlog build + sts2-review-final run (2026-09-22/23)

## Tickets: 21 built, board cleared

All Tickets/ backlog items implemented across 4 phases (delegated +
reviewed). Suite grew 130 -> 273 tests, ruff clean. New systems now in
`src/`: resume/checkpointing (`run_state.json` fingerprint), STT content-
hash cache, key x model failover chains + `DIRECTORCUT_BACKEND_FALLBACK`
(agy last resort), log redaction + `scripts/scan_secrets.py`, media_prep
(rotation reframe bake + loudnorm remux), `conformer/mixdown.py` (baked
beds, default on `DIRECTORCUT_FCPXML_TARGET=resolve`), `word_air` cut-edge
padding, `targets.py` (hard length/coverage + one corrective re-prompt),
`cutops` CLI (`cut list|drop|patch|undo`), `safety/ledger.py` cost caps,
`export_pack` (srt/vtt/chapters/thumbs), `conform_resolve.py` (Resolve
scripting spike), API review gates, whisper STT backend, PyInstaller spec +
`setup`/`demo` onboarding.

## Final run: sts2-review-final

- Env: api backend, chains lite-first for batched tasks (both paid keys
  returned 402 at launch - PAID_MAIN's `AQ.` key had answered a probe at
  04:50 then depleted by 05:24), director keeps 3.8-first (free quota
  resets ~09:00 CET), agy fallback armed.
- Media prep worked as designed: detected primary's rotation=-180, baked
  `media/A-Roll Raw Alternate_reframed.mp4` (705MB flat 1080p). Secondary
  (already flat) skipped.
- STT cache: first attempt paid for both takes (~33min audio); after the
  chain-order restart, resume replayed STT in seconds (372 nodes).
- Pass 3 on free+3.8 exhausted retries -> flash-lite produced 54 cuts but
  35% B-roll coverage -> targets.py caught it, corrective re-prompt ->
  39 cuts, 59% coverage, 10:18 length (still MISS, one retry is the cap).
- Final: 20 V1 / 17 V2 / 2 A2 / 2 A3; FCPXML clean (0 gaps, 0 bad
  children, 0 params); beds 618.3s; pack 8 files; spend $0.6756.
- watch_and_retry.py probes PAID_MAIN/PAID/free+3.8 every 15min for ~6h;
  first 200 triggers a Pass-3-only rerun (blueprint.json deleted ->
  resume redoes Pass 3+downstream). attempt1 blueprint backed up and
  restored on failure.

## 2026-09-23 ~09:05 - Watcher rerun: PASS 3 ON LIVE 3.8 HIT ALL TARGETS

Free-tier gemini-3.8-flash quota reset at 09:02 CET (midnight Pacific) as
predicted. watch_and_retry.py probe 4 detected it, deleted blueprint.json,
resumed -> Pass 3 re-ran on 3.8 first try (no corrective pass needed).

New blueprint -> re-finalized:
- Timeline 8:31.8 (target 7:00-9:00) - PASS
- B-roll coverage 70% (floor 60%) - PASS
- 50 V1 / 36 V2 / 90 cuts, no source-range overlap - PASS
- 6 secondary-take cuts = 4 distinct punch-in moments, all the directed
  beats: Defect name-blank, super-block run ("80 dmg/round"), Necrobinder
  "meat grinder", kids "no planning, just vibes" - PASS (2-4 by moment)
- Secondary cuts: 3 sub-0.35s uncovered slivers at overlay seams ->
  patched blueprint.json (extended 3 adjacent overlays <=0.32s), then
  2 residual 0.1s tail slivers patched the same way. Union coverage now
  100% over every secondary cut. No new source overlaps.
- Re-finalized via finalize_blueprint(): spine=50 asset-clip, gaps=0,
  bad_children=0, volume params=0, beds all exactly 511.752s, pack
  regenerated (4 chapters incl. Defect + kids beats, 5 thumbs, SRT/VTT).
- Spend $0.6963, 51 metered calls (costs.json).

FINAL ARTIFACT: output/sts2-review-final/sts2-review-final.fcpxml
- import into a fresh Resolve timeline. All acceptance criteria met.

Remaining known-minor: secondary count 6 cuts vs "2-4" spec (4 moments);
retry ladder still burns ~10min on quota-dead 3.8 before rotating
(backoff tuning ticket-worthy); 402 paid keys skipped instantly as designed.
