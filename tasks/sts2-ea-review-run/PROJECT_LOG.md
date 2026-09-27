# StS2 EA Review — first real-world pipeline run

**Date:** 2026-09-21 | **Project:** `sts2-ea-review` | **Result:** success, `output/sts2-ea-review/sts2-ea-review.fcpxml`

## Inputs

- `ingest/aroll/` — 5.3 GB, 989s, 3840×2160@30 A-roll (hardlinked, not copied — same volume)
- `ingest/broll/` — 17 SteelSeries Moments clips + `trailers/` (5 Mega Crit trailers)
- `ingest/music/` — 2 StS2 OST mp3s
- `ingest/voiceover/aroll_audio.mp3` — A-roll audio extracted via ffmpeg (`-ac 1 -b:a 128k`)
- `ingest/direction.txt` — guidance + full reference script embedded
- Backend: `GEMINI_BACKEND=antigravity` (agy), ElevenLabs Scribe STT, prod env. Est. cost $0.42.

## Outcome

- 229 narrative nodes from Scribe STT (real speech, ad-libs + retakes captured)
- 195 cuts: 154 V1, 31 B-roll overlays, 7 generated SFX cues, 3 music cuts (both OST tracks as beds)
- Final timeline 8:44 from 16.5 min raw — bad takes/dead air removed, structure followed script

## Quirks hit (worth remembering)

1. **Script asset vs STT**: any file resolving to SCRIPT role puts Pass 1 on the fake-5s-segment path and Skips Scribe entirely. For non-verbatim recordings (ad-libs, retakes), keep the script OUT of ingest — embed it in `direction.txt` instead, which lands verbatim in the Pass 3 prompt.
2. **Voice priority trap**: `_first_by` picks the STT source from audio assets in order VOICEOVER > AUTO > MUSIC > SFX. With only music mp3s in ingest, the soundtrack becomes the "voice" and gets transcribed. Fix: pre-extract A-roll audio into `ingest/voiceover/` so it wins.
3. **Multi-track B-roll**: SteelSeries clips carry 3 audio streams each → 51 stream assets extracted + classified. Harmless but noisy; the stream0 game audio got classified `voiceover` (doesn't matter — explicit VOICEOVER role file still wins).
4. **agy validation retry works**: one Pass-2 chunk failed response validation (21s call); `_call_agy` retried with corrective hint ~3.5 min later and it classified fine. ERROR line ≠ fatal.
5. **4K proxy time**: the 989s 4K→360p@1fps visual proxy took ~18 min alone; all other 22 proxies combined were seconds each.
6. **FCPXML refs hardlinked ingest paths** — deleting `ingest/` breaks media resolution; keep folder or relink in Resolve.

## Revision pass — `sts2-ea-review-v2.fcpxml` (2026-09-21, post-review)

User feedback on v1: audio levels off, "stray clips" feel (23 sub-1s jump-cut fragments on a static pillarboxed head), A-roll is portrait 2160×3840 (rotate=90 on a 3840×2160 file) sitting as a 607px column in the 1080p frame.

Fix applied via `tasks/sts2-ea-review-run/fix_fcpxml.py` (XML post-processor, deterministic, no re-run needed):

- **Voice**: measured -26.1 LUFS, TP +1.3 dBFS (can't just gain it). Rendered `ingest/voiceover/aroll_voice_norm.mp3` — highpass 80 Hz → compand → loudnorm I=-16/TP=-1.5 → landed -17.6 LUFS, LRA 8.1. Spine A-roll `<asset-clip>`s converted to `<video>` (video-only per spec = embedded mic silenced); normalized VO nested as `<audio lane="-1">` per clip with identical source windows.
- **Music/SFX**: duck keyframes deepened -12→-15 dB (music, ~-31 LUFS under -17.6 voice) and -6→-9 dB (SFX).
- **Portrait**: all 154 spine clips get `adjust-transform scale="3.2"` + `position="0 -260"` — fills 16:9, visible band biased toward face (~36% up source). Verified by cropping source frames to the predicted region: eyes land upper-third.
- **PiP over B-roll**: 73 `lane="2"` `<video>` twins of the spine clips cover every overlay span (overlays intersect spine cuts, so each covered clip fragment gets its own twin with matching source window) — `scale="0.55"`, `position="733 -183"` (bottom-right, 60px margins). Composite preview confirmed.
- **Not expressible in FCPXML**: circular mask. Resolve step: ellipse mask on one PiP clip → copy → select all V3 clips → paste attributes.

Keep both files; v1 remains untouched as fallback.

## Revision 2 — connected-clip offset bug (2026-09-21, after Resolve import of v2)

Symptom: clips before 0:00 (~-7:30) with no audio; voice plays for the first ~4s then only music. Root cause: conformer emitted connected-clip `offset` as a local value; FCPXML wants `parent.start + local`. First clip's start≈0.1s hid it; later clips shifted by -source_in. **Same bug caused v1's "stray clips"** (SFX/late overlays misplaced).

- Fixed in `src/directorcut/conformer/fcpxml.py` (`parent_start` param on `attach_*`), regression test added, 119/119 tests pass.
- `reconform.py` rebuilds `sts2-ea-review.fcpxml` from `blueprint.json` without re-running AI passes.
- `fix_fcpxml.py` updated the same way (VO lane -1 offset = parent start; PiP = src_in + local); music lowered a further -6 dB (unducked -6, ducked -21).
- Verified: all 268 children land within their parent; timeline positions span 0–524.2s.

## Revision 3 — `sts2-ea-review-v3.fcpxml` (2026-09-22, after Resolve import of v2)

Symptoms: A-roll frozen/not showing, gaps between cuts ("removed but not compacted"), ducking ineffective. Causes: v2 turned spine clips into `<video>` (Resolve renders spine `<video>` as frozen stills and misplaces them — already recorded in `test_primary_clip_is_asset_clip_with_probed_asset`; I re-hit it); `<param name="volume">` keyframes are ignored by Resolve.

Approach: take audio out of FCPXML entirely.
- `ingest/aroll/A-roll normalized.mp4` — `-c:v copy` remux of the raw A-roll with the -17.6 LUFS voice as its embedded track (rotation=90 side data preserved). Spine stays `<asset-clip>` ref'ing this file.
- `render_audio.py` builds from `blueprint.json`: `voice_guide.wav` (timeline-placed voice, -17.2 LUFS, sidechain source only), `music_bed_ducked.wav` (OST cuts placed → `sidechaincompress threshold=0.015 ratio=12 attack=40 release=500` → -8 dB; -25 LUFS in voice gaps, ≈-46 under voice), `sfx_bed.wav` (7 cues, flat -9 dB). Beds attach to the first spine clip as single lane -2/-3 clips, no keyframes.
- `verify_v3.py`: 154 asset-clips all on the new asset, 73 PiP, contiguous spine (max 1-frame jitter from blueprint), 0 offset violations, 0 `<param>`.
- Quirk: `<video>` is fine as a *connected* clip (v1 B-roll played), fatal on the *spine*. Recorded in MASTER_MEMORIES.

## Revision 4 — `sts2-ea-review-v4.fcpxml` (2026-09-22, after Resolve import of v3)

Symptoms: black frame where A-roll should show, "repeating clips", odd silence ~1:00. Checked: transcript has continuous speech across 0:46–1:25 and the remuxed audio is present/aligned there — so all three are Resolve placement/transform artefacts, not content. Resolve mis-applies FCPXML `adjust-transform` on rotated portrait footage (documented Fit-conform problem) → spine image pushed off-frame; the 73 lane-2 PiP twins then read as duplicates.

Fix: bake the reframe into media, remove every transform from the XML.
- `ingest/aroll/A-roll reframed.mp4` — `crop=2160:1215:0:1024,scale=1920:1080` of the autorotated source, h264_nvenc cq20, normalized voice as audio. 1.62 GB, ~17 min encode. No rotation metadata.
- v4 XML: 154 plain `<asset-clip>`s on the reframed file, 31 lane-1 B-roll `<video>`, one music bed + one SFX bed. Zero `adjust-transform`, zero PiP, zero `<param>`.
- PiP-over-B-roll is now a Resolve-side step (copy V1 clip onto V2 under overlays, scale + ellipse mask) if still wanted.
- Lesson: for Resolve targets, treat FCPXML as *placement only* — media, levels and framing must be baked into files.

## Revision 5 — `sts2-review-v3` full pipeline run (2026-09-27)

New inputs: `GEMINI_FREE_KEY_MAIN` (new free key), prepaid `GEMINI_PAID_KEY` (100 SEK), `ELEVENLABS_VOICE_ID` for pickups, and `A and B Roll.mp4` - a 118-min OBS stream hardlinked into both `ingest/aroll/` and `ingest/broll/` (dual-role source; track 1=program mix, track 2=Yeti mic, track 3=dead game channel - both live tracks ~-30dB mean, voice-dominated).

Key routing per user: FREE_MAIN -> FREE -> PAID_MAIN -> PAID -> agy, all tasks. Media prep normalized everything to -16 LUFS (Yeti measured -28.9, Pixel-8 A-roll already -16.6) using the same chain, so voice levels match across sources.

Three run-blocking bugs found and fixed mid-run:
1. `_words_to_nodes` crash: Scribe emits zero-duration words on long captures (echoed re-takes share a timestamp; 1,647 zero-duration entries in the 23k-word transcript). Floored node span at one frame. Regression tests added.
2. Tagger crash, same family: `_NarrativeNodeList` response model strict-validated model-emitted timecodes that callers discard anyway. Replaced with lenient `_TaggedNode` (id/tag/energy); originals preserved downstream.
3. Truncated-JSON crash: tagger batch hit the output-token cap mid-string (`Unterminated string`); the error bypassed failover. Added `_repair_truncated_json` (close string+containers, walk back to element boundary) and routed parse failures through `_ApiCallError` so sibling models get the call.
4. Bonus fix: `media_prep._bake` hardcoded `-map 0:v:0` - crashed on audio-only mp3 normalization; now branches the map by codec type.

### Result (after repair + re-finalize)

- `output/sts2-review-v3/sts2-review-v3.fcpxml`: 8:22 timeline (501.9s), 45 spine asset-clips, 29 overlays, 0 params/gaps, beds exactly timeline-length.
- Targets: 8.37min (in 7-9), 67% B-roll coverage, 4 secondary punch-ins all fully covered (2 seam tails patched), 0 source overlaps, 0 repeated lines.
- Editorial: covers every scripted beat in order incl. the Defect name-blank (secondary take), super-block run, Sovereign Blade, Havoc self-own, kids, $25 price + mobile caveat, Game Native aside. Director = gemini-3.8-flash, single pass, no corrective reprompt.
- Stream contributed 10 V2 overlays but 0 V1 cuts - director preferred scripted takes for spine despite the stream holding 82% of nodes. Defensible take-selection; telemetry ticket filed (`aroll-source-usage-telemetry`).
- Costs: $1.715 total (171 calls; visual Pass-2 dominated at $0.82 across ~143 chunks incl. duplicated stream proxies - dedupe ticket filed).
- Uniqueness validator upgraded: sub-tolerance source overlaps now head/tail-TRIM (timeline moves with source) instead of tolerating or dropping; interior containment still drops. Post-pad repair pass added in pipeline since edge padding can re-open reuse.

ElevenLabs pickups: not needed - every required line existed in a usable take.
