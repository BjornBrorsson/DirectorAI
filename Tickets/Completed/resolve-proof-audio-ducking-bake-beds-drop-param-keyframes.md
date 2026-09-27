# Resolve-proof audio ducking: bake beds, drop param keyframes

| Field | Value |
|---|---|
| Status | Completed |
| Assignee | unassigned |
| Priority | High |
| Type | Bug / Feature |
| Created | 2026-09-22 |

## Summary

`conformer/ducking.py` emits `<param name="volume">` keyframes that
DaVinci Resolve ignores entirely (verified 2026-09-22: no audible ducking).
The task-folder recipe `tasks/sts2-ea-review-run/render_audio.py` works:
place music cuts into one bed, `sidechaincompress` it against a
timeline-placed voice guide, attach as a single `<audio>` clip.

Productize: a pipeline step (after Pass 3, before conform) that renders
`music_bed_ducked.wav` + `sfx_bed.wav` from the blueprint and swaps the
A2/A3 cuts for the beds when targeting Resolve. Keep the keyframe path for
FCPX targets (keyframed params are legal FCPXML there).

## Acceptance Criteria

- [ ] Resolve-target FCPXML contains no `<param name="volume">`
- [ ] Ducked bed measures >=12 dB lower under voice than in gaps (eBur128 check in test via short fixture)
- [ ] Voice guide built per-source_file for multi-A-roll projects
- [ ] Config/CLI flag to choose baked-bed vs keyframe ducking; Resolve target defaults to baked

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
- 2026-09-22: implemented. `conformer/mixdown.py::render_mixdown` renders
  `voice_guide.wav` (each V1 cut's source audio placed at `timeline_in`,
  grouped per `source_file` for multi-A-roll), `music_bed_ducked.wav`
  (`sidechaincompress` threshold=0.015 ratio=12 attack=40 release=500 keyed
  off the guide) and `sfx_bed.wav` into `output/<project>/`. New
  `finalize.py::finalize_blueprint` runs mixdown then `build_fcpxml` for
  `fcpxml_target="resolve"` (default; `DIRECTORCUT_FCPXML_TARGET`); beds are
  attached as single clips on lane -2/-3 with zero `<param>` keyframes.
  `fcpx` target keeps the keyframe ducking. Filter graphs via
  `-filter_complex_script` (Windows 32K cmdline cap). Tests:
  `tests/test_mixdown.py` (synthesized WAVs, duck depth asserted) +
  `tests/test_conformer.py` XML shape; `pytest -q` green.
