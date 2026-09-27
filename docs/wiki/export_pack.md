# Feature: Creator export pack (captions / chapters / thumbnails)

> Status: Draft | Last verified: 2026-09-23

## What it does

After finalization, `write_export_pack()` writes `output/<project>/pack/` —
the creator-facing artifacts that ride alongside the FCPXML:

* `captions.srt` + `captions.vtt` — the spoken text actually on the
  timeline. Each V1 cut's source range is filled from **STT word timing**
  (`narrative.stt.load_cached_words` on the source's extracted audio proxy —
  free on a rerun because the cache is content-addressed); where no word
  timing exists, the covered narrative nodes' `spoken_text` is distributed
  over the cut as one cue per node. Words are grouped into cues of ~45
  chars / ≤4s, split at sentence ends, and clamped to the cut's on-screen
  span.
* `chapters.txt` — `HH:MM:SS title` lines (YouTube/FFmpeg chapter format).
* `thumbs/` — 3–5 candidate JPEGs pulled by ffmpeg (`-ss` input seek,
  `-frames:v 1`, `-q:v 2`).

## Where the code lives

* `directorcut.export_pack.write_export_pack()` (`src/directorcut/export_pack.py`)
  — invoked from `finalize.finalize_blueprint()` so both the pipeline and
  `directorcut cut` re-conforms refresh the pack. **Strictly best-effort**:
  the caller wraps it in try/except and per-thumbnail ffmpeg failures skip —
  a pack failure must never sink a good conform.
* `_audio_for_source()` — maps a cut's `source_file` back to the audio file
  its STT transcript was keyed on (`<stem>_stream0.mp3` / `<stem>_audio.mp3`,
  incl. pre-bake `derived_from` stems). It NEVER extracts audio — a pack
  refresh must not run ffmpeg just to find a cache key.

## Chapter heuristic

Each V1 cut inherits the `thematic_tag` of the **first** narrative node it
covers (EXPOSITION when it covers none); consecutive same-tag cuts merge
into one chapter run. Runs shorter than `_MIN_CHAPTER_S` (60s) fold into the
previous chapter — rapid tag flips (spike → exposition → spike) would
otherwise spam chapters. A short FIRST run is kept (a short opener beats a
missing first chapter). Boundaries sit at V1-cut edges — the only
guaranteed seam positions on the timeline. Titles come from the chapter's
leading spoken text (first 8 words, ≤60 chars); "Unscripted segment …"
coverage markers and empty text fall back to `"<Tag> (part N)"`.

## Thumbnail heuristic

Candidates are `(source_file, source_seconds, timeline_seconds)` picks:

1. HIGH-energy `EMOTIONAL_SPIKE` node midpoints first, mapped through the
   covering V1 cut to source time (spikes are where a face/reaction is most
   likely);
2. then remaining (lower-energy) spikes;
3. then midpoints of the **longest** V1 cuts until at least `_THUMB_MIN`
   (3) picks exist — the ticket's 3–5 target;
4. non-video sources and picks within `_THUMB_MIN_SEPARATION_S` (1.5s) of an
   earlier pick on the same file are dropped; the list caps at 5.

Filenames carry the timeline stamp (`thumb_01_00-01-23.jpg`) so the creator
can find the moment on the timeline.

## Gotchas

* Word-timing is keyed on the extracted audio proxy's **content hash** — a
  source whose `A_stream0.mp3` was never produced (scripted runs, pre-cache
  projects) silently falls back to node-level captions.
* Nodes carry source-time ranges; captions map `tl_in + (node.start -
  cut.source_in)` — the mapping assumes the cut ships its source window
  linearly (true for every V1 cut the director emits).
* `pack/` is regenerated on every conform — manual edits inside it are
  overwritten. Edit the blueprint, then let the pack follow.
