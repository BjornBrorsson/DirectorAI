# DirectorCut-AI — Schema Index

> Maps runtime JSON schemas and file formats to their definitions.

| Schema | File | Description |
|---|---|---|
| Narrative Node Output | `narrative_node.schema.json` | Pass 1 output: spoken lines mapped to timestamps with thematic tags. |
| Visual State Event Output | `visual_event.schema.json` | Pass 2 output: gameplay state events from visual proxy. Now includes optional `source_file` identifying which asset the event came from. |
| Assembly Blueprint | `assembly_blueprint.schema.json` | Pass 3 output: final cut list for FCPXML generation. `track` now also allows `A2_MUSIC`/`A3_SFX`; blueprint carries `audio_cues` for generated audio. |
| Asset Interpretation | `directorcut.assets._AssetInterpretation` | Interpret pass output: role + description per dumped file. |
| Audio Cue | `directorcut.models.AudioCue` | Director's request for generated audio (`kind`, `prompt`, timeline range). |
| FCPXML Template | `fcpxml_template.xml` | Reference FCPXML 1.8 sequence skeleton. |
| Cost Ledger | `output/<project>/costs.json` | Metered-call ledger written by `directorcut.safety.ledger.CostLedger`: `{version, updated_at, cap_usd, totals, calls[]}` — Gemini entries carry `task`/`model`/`input_tokens`/`output_tokens`/`cost_usd`; ElevenLabs entries carry `kind`/`seconds`/`cost_usd`; dev stubs carry `stub: true` at cost 0. |
| Patch Plan | `directorcut.cutops.PatchPlan` / `PatchOp` | `cut patch` interpret output: `{"ops": [...], "rationale"}` — ops are `drop_cut`, `move_cut(index,to_index)`, `swap_source(index,source_file)`, `move_overlay(index,to_timecode)`, `drop_overlay`. |

*Schemas live as pydantic models in `src/directorcut/models.py`; this index is the map.*
