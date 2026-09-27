# A-roll voice normalization as a pipeline step

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Feature |
| Created | 2026-09-22 |

## Summary

Phone/camera voice recordings measured -26 and -29 LUFS with clipped
peaks; manual chain `highpass=f=80 -> compand -> loudnorm=I=-16:TP=-1.5`
produced -16.6 LUFS broadcast-safe audio. Doing this by hand before ingest
is error-prone.

Add an ingest-time option: remux each A-roll (or the designated voiceover
source) with normalized audio (`-c:v copy`, AAC 192k stereo) and use the
remuxed file downstream. Reuse the exact filter chain from
tasks/*/render pipelines.

## Acceptance Criteria

- [ ] Config flag (e.g. DIRECTORCUT_NORMALIZE_VOICE=true) or per-asset role option
- [ ] Output measures -16..-18 LUFS integrated, TP <= -1 dBFS
- [ ] Rotation/metadata preserved through remux
- [ ] Blueprint refs point at the remuxed file

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented in `media_prep.py`, same bake pass as the
  reframe. `DIRECTORCUT_NORMALIZE_VOICE=true` (or `normalize_voice` field)
  measures integrated loudness via `ebur128` first; sources already inside
  the -18..-15 LUFS band are skipped (double-normalizing pumps the noise
  floor). Otherwise one ffmpeg pass remuxes video (`-c:v copy`, rotation
  preserved) and re-encodes audio `highpass=80 -> compand -> loudnorm
  I=-16:TP=-1.5 -> aresample=48k -> aac 192k stereo` to
  `media/<stem>_voicenorm.mp4`; the manifest/blueprint reference the baked
  file. Stubbed-ffmpeg tests in `tests/test_media_prep.py`.
