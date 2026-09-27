# v1 backlog quad — cost caps, export pack, NL patches, NLE matrix

**Date:** 2026-09-23 | **Scope:** four draft tickets in
`Tickets/Backlog/Needs further specification/` (kept in place per repo rule)

## What shipped

1. **Cost accounting + `DIRECTORCUT_COST_CAP_USD`** —
   `safety/ledger.py` (`CostLedger`, `activate_cost_ledger`, contextvar-
   scoped). `run_pipeline` + `cut patch` activate it; GeminiClient
   (api/cli/agy + dev stub) and ElevenLabsClient (stt/music/sfx) meter into
   it. `costs.json` persists per-call entries + totals; `BudgetExceeded`
   aborts on cap breach (v1 = abort, not pause). `cli run` prints recorded
   spend vs the dry-run estimate.
2. **Creator export pack** — `export_pack.py`, invoked from
   `finalize.finalize_blueprint` (best-effort, try/except). Writes
   `pack/captions.srt|.vtt` (STT word timing via `load_cached_words`, node
   fallback), `pack/chapters.txt` (thematic-tag runs, <60s fold), and
   `pack/thumbs/*.jpg` (HIGH EMOTIONAL_SPIKE midpoints → longest-cut
   backfill, 3-5 picks).
3. **`directorcut cut patch`** — `cutops.PatchOp`/`PatchPlan` pydantic
   schema (5 ops), `interpret_patch` (one `task="interpret"` call on the
   fast model), `preview_patch`/`apply_patch` (snapshot → write →
   re-finalize), `cutops._fake_patch` dev stub. `move_cut` uses
   `_relayout`, the permutation sibling of `compact_timeline`, so anchored
   overlays ride along.
4. **`premiere` FCPXML target** — `fcpxml_target` Literal +
   `build_fcpxml` accept it; shares the fcpx shape
   (`emit_ducking = target in ("fcpx","premiere")`). Compatibility matrix +
   Premiere TODOs in `docs/wiki/nle_targets.md`.

## Quirks hit

- **Ledger self-deadlock**: `_append` held a plain `threading.Lock` while
  `flush()` → `totals()` reacquired it → `test_resume` hung with no error.
  Fixed by locking only the append, then flushing outside the lock.
  Recorded in `docs/MASTER_MEMORIES.md`.
- **`PATCH_PROMPT.format` KeyError**: the template embeds literal JSON
  braces (`{"ops": ...}` examples) — `str.format` ate them as field names.
  Switched to `.replace()` substitution. Recorded in MASTER_MEMORIES.
- **`IngestManifest_from_json` typo** during patch work — replaced with
  `IngestManifest.model_validate_json` (there is no such helper).

## Verification

- `.venv/Scripts/python.exe -m pytest -q` → **243 passed** (was 212 at
  baseline; +31 new: test_costs, test_export_pack, test_patch, premiere
  conformer tests)
- `.venv/Scripts/python.exe -m ruff check .` → clean

## Open items

- ElevenLabs per-second rates in `ELEVENLABS_USD_PER_SECOND` are
  placeholders — calibrate against real plan pricing.
- No real Premiere/FCPX/CapCut import has been run — nle_targets.md lists
  the TODOs to check on first import.
- `cut patch` undo is the existing single-slot swap snapshot; a multi-level
  undo stack is future work.
- `swap_source` keeps the source_in/source_out window (the "take B instead"
  semantic); offsetting/re-timeing the window is out of v1 scope.
