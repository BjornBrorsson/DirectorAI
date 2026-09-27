"""Create the Tickets/ backlog from the sts2 review session findings.

Run from repo root: .venv/Scripts/python.exe tasks/multi-aroll-api-run/make_tickets.py
Idempotent: overwrites files it owns.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READY = ROOT / "Tickets" / "Backlog" / "Ready"
DRAFT = ROOT / "Tickets" / "Backlog" / "Needs further specification"

TICKET = """# {title}

| Field | Value |
|---|---|
| Status | {status} |
| Assignee | unassigned |
| Priority | {priority} |
| Type | {type} |
| Created | 2026-09-22 |

## Summary

{summary}

## Acceptance Criteria

{acs}

## Work Log

- 2026-09-22: created from the StS2 EA-review production runs (see
  `tasks/sts2-ea-review-run/PROJECT_LOG.md`,
  `tasks/multi-aroll-api-run/PROJECT_LOG.md`, `docs/MASTER_MEMORIES.md`).
"""


def ticket(status, priority, type_, title, summary, acs):
    ac_lines = "\n".join(f"- [ ] {a}" for a in acs)
    return TICKET.format(
        title=title, status=status, priority=priority,
        type=type_, summary=summary.strip(), acs=ac_lines,
    )


ready_tickets = [
    (
        "Cache STT transcripts across runs",
        "High", "Bug / Cost",
        """Every pipeline restart re-runs ElevenLabs Scribe on the same audio.
During the take-2 run, the pipeline restarted five times fighting API 503s
and each restart re-transcribed both A-roll takes (~$0.10/file/run).

Cache transcripts keyed on a content hash of the audio file (+ model id) in
`output/<project>/stt_cache/` so `narrative/stt.py::transcribe_audio` is
called at most once per file per project.""",
        [
            "Re-running a project that already transcribed a file performs zero STT API calls for it",
            "Cache key covers audio content hash + STT model; different model re-transcribes",
            "Unit test: fake STT client called once across two _transcript_sources-driven Pass-1 runs",
            "Cache path documented in docs/wiki/pass1_narrative.md",
        ],
    ),
    (
        "Fix IngestManifest JSON round-trip",
        "High", "Bug",
        """`IngestManifest.model_validate_json` fails on the pipeline's own
`manifest.model_dump_json()` output: the `before`-validator coerces str→Path,
then strict JSON-mode Path validation rejects the Path objects. Worked
around in `tasks/multi-aroll-api-run/rerun_pass3.py` with
`model_validate(json.loads(...))` — the durable fix belongs in
`models.py` (coerce only str/Path, or validator mode).""",
        [
            "`IngestManifest.model_validate_json(m.model_dump_json())` round-trips",
            "Same for NarrativeNode/VisualEvent/Cut Path fields if affected",
            "Regression test in tests/",
        ],
    ),
    (
        "Resolve-proof audio ducking: bake beds, drop param keyframes",
        "High", "Bug / Feature",
        """`conformer/ducking.py` emits `<param name="volume">` keyframes that
DaVinci Resolve ignores entirely (verified 2026-09-22: no audible ducking).
The task-folder recipe `tasks/sts2-ea-review-run/render_audio.py` works:
place music cuts into one bed, `sidechaincompress` it against a
timeline-placed voice guide, attach as a single `<audio>` clip.

Productize: a pipeline step (after Pass 3, before conform) that renders
`music_bed_ducked.wav` + `sfx_bed.wav` from the blueprint and swaps the
A2/A3 cuts for the beds when targeting Resolve. Keep the keyframe path for
FCPX targets (keyframed params are legal FCPXML there).""",
        [
            "Resolve-target FCPXML contains no `<param name=\"volume\">`",
            "Ducked bed measures >=12 dB lower under voice than in gaps (eBur128 check in test via short fixture)",
            "Voice guide built per-source_file for multi-A-roll projects",
            "Config/CLI flag to choose baked-bed vs keyframe ducking; Resolve target defaults to baked",
        ],
    ),
    (
        "Conformer guard: never emit <video> on the spine",
        "Medium", "Bug / Guardrail",
        """Resolve renders spine `<video>` elements as frozen stills and
misplaces them (verified 2026-09-22, also recorded in
`test_primary_clip_is_asset_clip_with_probed_asset` and MASTER_MEMORIES).
`<video>` is only safe as a connected clip. Add a conform-time assertion:
any visual source on V1_A1_PRIMARY must emit `<asset-clip>`; `<video>` may
only appear nested with a lane. Fail loudly rather than produce a broken
timeline.""",
        [
            "build_fcpxml raises ConformError if a spine visual would become <video>",
            "Unit test covering the assertion",
            "MASTER_MEMORIES cross-linked",
        ],
    ),
    (
        "Portrait / rotated A-roll: bake the reframe at ingest",
        "High", "Feature",
        """Rotated phone footage (rotation=90, 2160x3840 effective) pillarboxes
in a 16:9 timeline, and Resolve mis-applies FCPXML adjust-transform on it
(black frames, verified). Working recipe exists:
`crop=2160:1215:0:1024,scale=1920:1080` into `A-roll reframed.mp4`
(tasks/sts2-ea-review-run).

At ingest (or a dedicated reframe pass), detect stream rotation / non-target
aspect; offer an automatic reframe-to-sequence-AR bake (h264, no rotation
metadata) and point the blueprint at the reframed file. Treat FCPXML
transforms as placement-only for Resolve targets.""",
        [
            "Portrait A-roll produces a 16:9 spine clip with no adjust-transform needed",
            "Reframe keeps audio (stream copy or normalized audio when normalize ticket lands)",
            "Rotation detection + unit test with a rotated fixture",
            "Direction/config opt-out keeps original file",
        ],
    ),
    (
        "A-roll voice normalization as a pipeline step",
        "High", "Feature",
        """Phone/camera voice recordings measured -26 and -29 LUFS with clipped
peaks; manual chain `highpass=f=80 -> compand -> loudnorm=I=-16:TP=-1.5`
produced -16.6 LUFS broadcast-safe audio. Doing this by hand before ingest
is error-prone.

Add an ingest-time option: remux each A-roll (or the designated voiceover
source) with normalized audio (`-c:v copy`, AAC 192k stereo) and use the
remuxed file downstream. Reuse the exact filter chain from
tasks/*/render pipelines.""",
        [
            "Config flag (e.g. DIRECTORCUT_NORMALIZE_VOICE=true) or per-asset role option",
            "Output measures -16..-18 LUFS integrated, TP <= -1 dBFS",
            "Rotation/metadata preserved through remux",
            "Blueprint refs point at the remuxed file",
        ],
    ),
    (
        "Pipeline resume / stage checkpointing",
        "High", "Reliability",
        """The take-2 run restarted 5x against free-tier API failures; each
restart redid ingest, proxies, stream extraction, STT, tagging, and all
finished Pass-2 chunks (~1.5h duplicated work + paid STT calls).

Persist per-stage outputs to `output/<project>/` (proxies, extracted
streams, transcripts, tagged nodes, visual events, blueprint) and add
`--resume` (or auto-resume) so a rerun skips completed stages. Combined
with the STT-cache ticket this makes failures cheap.""",
        [
            "Killing a run mid-Pass-2 and re-running resumes at Pass 2 with finished chunks reused",
            "Stale artifacts invalidated when ingest/direction changes (manifest hash)",
            "--fresh flag forces full re-run",
            "Resume documented in cli_client.md",
        ],
    ),
    (
        "Model fallback chain + dual-key routing",
        "Medium", "Reliability",
        """Free-tier reality (2026-09-22): 3.8-flash has a 20 req/day cap,
full-size models 503 on large prompts, flash-lite survives everything.
The run only finished because a human watched .env get hot-swapped
mid-flight.

Config: ordered model list per task (e.g. GEMINI_MODEL_CHAIN) so persistent
429-quota/503-on-large-prompt falls to the next model with a warning.
Optional second key (GEMINI_FREE_KEY) routed to `model_for("fast")` calls
to keep paid quota for heavy prompts.""",
        [
            "Simulated persistent-429 on model 1 falls through to model 2 mid-run",
            "Fallback logged with which pass/task switched",
            "Optional fast-key env wired into model_for routing",
            ".env.example documents the chain format",
        ],
    ),
    (
        "V1 cut edges: sentence boundaries with air",
        "Medium", "Quality",
        """Prompt-level "cut at sentence boundaries with ~0.2s air" mostly
worked but several take-2 cuts start/end tight on words. STT returns
word-level timing — use it: for each V1 cut, pad edges to the nearest
>=0.15s of silence before/after the covered words (clamped to keep cuts
contiguous or allow overlap-free trims).""",
        [
            "No V1 cut boundary lands inside a word (verified vs transcript timing)",
            "Edges gain ~0.2s air when the source allows it",
            "Unit test with fabricated nodes/cuts",
        ],
    ),
    (
        "Blueprint target validation (length / B-roll coverage)",
        "Medium", "Quality",
        """Attempt 1 ignored the 7-9min target (produced 4:15) and the 60%
B-roll direction (10 overlays) until an addendum spelled out hard numbers.
Don't rely on prompt wishes: configurable `target_length_s` +
`broll_coverage_min` (or parsed from direction), checked post-director;
on violation, re-prompt the director once with the measured violation,
then surface a warning in the run output either way.""",
        [
            "Out-of-target blueprint triggers one corrective director call with the measured deltas",
            "Final blueprint report prints length + coverage vs target",
            "Defaults sensible; overridable via config/direction",
        ],
    ),
    (
        "Productize blueprint patch commands (drop/move cuts)",
        "Medium", "Tooling",
        """`tasks/multi-aroll-api-run/drop_cuts.py` drops V1 cuts by index,
re-compacts via `compact_timeline`, rebuilds FCPXML + beds. That's the
start of a post-assemble patch CLI every user will want after reviewing
a rough cut.

Promote to `directorcut cut drop <project> <n>...` plus a couple of
neighbours (`list`, `undo` via blueprint snapshots). Beds + FCPXML must
re-render after each patch (drop_cuts already chains render_audio +
finalize).""",
        [
            "CLI subcommand works on any project dir with blueprint.json",
            "Drops produce a compact, gapless spine + re-rendered beds",
            "Undo restores the previous blueprint snapshot",
            "docs/wiki/cli_client.md updated",
        ],
    ),
    (
        "Secrets hygiene: redaction + CI guard",
        "Low", "Chore",
        """.env is gitignored but logs/tests have needed manual sed-redaction
of keys during debugging, and `smoke_*.py` reads the raw key. Add a shared
`redact()` in log formatting (mask API-key-shaped strings + configured
secrets) and a CI/pre-commit check (gitleaks or a simple regex scan) so
keys can't land in repo files.""",
        [
            "Log lines containing GEMINI_* key values print redacted",
            "Pre-commit or CI step fails on committed secrets",
            "No test depends on real .env contents (GEMINI_MODEL_FAST leak already fixed once)",
        ],
    ),
]

draft_tickets = [
    (
        "Direct Resolve timeline build via scripting API (skip FCPXML)",
        "High", "Feature / Spec needed",
        """Half the bugs this session were Resolve's FCPXML importer
reinterpreting things (tcStart, connected-clip offsets, <video> spine,
param volume, adjust-transform on rotated media). DaVinci Resolve has a
Lua/Python scripting API that can create timelines, place clips, set
transforms and audio levels natively — removing the interpretation layer
entirely.

Spec needed: prototype scope (resolve installed locally?), script to
recreate a blueprint timeline + baked beds, comparison vs FCPXML path,
fallback story for users without Resolve.""",
        [
            "Feasibility spike documented (API coverage for clips/lanes/transforms/levels)",
            "Prototype builds the sts2 timeline natively and matches the verified-good v4 layout",
            "Decision record: FCPXML vs native per target NLE",
        ],
    ),
    (
        "NLE target matrix (FCPX / Premiere / CapCut)",
        "Medium", "Feature / Spec needed",
        """FCPXML semantics are FCP-native; we've only ever verified Resolve.
For a sellable tool, define target matrix: DaVinci Resolve (current),
Final Cut Pro (native FCPXML - transforms + param volume may actually
work there), Premiere XML/AAF, CapCut. Per-target quirks doc + conform
options (ducking baked vs keyframed is already target-dependent).""",
        [
            "One real import test per NLE documented",
            "Conform target flag routes track/ducking/transform choices",
            "docs/wiki/fcpxml_conformer.md gains a compatibility table",
        ],
    ),
    (
        "Creator export pack (captions, chapters, thumbnails)",
        "Medium", "Feature",
        """Cheap wins on top of existing data: SRT/VTT captions from narrative
nodes (word timing is already there), chapter markers from
script/direction sections, thumbnail still extraction at EMOTIONAL_SPIKE
nodes. Bundle as `output/<project>/pack/` alongside the fcpxml.""",
        [
            "SRT validates + imports alongside the timeline",
            "Chapter markers land on section transitions",
            "3-5 candidate thumbnails written to pack/",
        ],
    ),
    (
        "Local STT fallback (faster-whisper) for keyless/offline use",
        "Medium", "Feature",
        """ElevenLabs Scribe costs ~$0.40/hr and requires a key+network.
A faster-whisper backend (small/medium model, CPU OK for A-roll lengths)
makes the tool sellable to users without an EL subscription and removes
a recurring cost. Word-level timing is required (used for node split +
cut-air ticket).""",
        [
            "stt backend flag: elevenlabs | whisper | auto",
            "Whisper path produces NarrativeNode-compatible timing within ~0.2s of Scribe on a fixture",
            "Offline test runs without any keys set",
        ],
    ),
    (
        "Packaging: distributable build + first-run onboarding",
        "High (product)", "Feature",
        """To sell to actual people: PyInstaller/portable build, no dev env;
first-run wizard collecting keys + backend + model prefs (stored in
user config dir, not repo .env); sane defaults so a creator gets a
timeline without reading docs. Includes secrets-in-config handling and
a no-key demo mode using stubs.""",
        [
            "Clean machine installs and runs ingest->fcpxml demo with bundled fixtures",
            "Keys entered in onboarding never written to repo",
            "Demo mode produces a valid FCPXML offline",
        ],
    ),
    (
        "Cost accounting + budget caps",
        "Medium", "Feature",
        """Dry-run shows an estimate (~$0.75) but nothing enforces it.
Track per-pass spend (Gemini tokens by model + ElevenLabs minutes),
persist to the project dir, add a hard cap config that pauses the run
for approval, and surface totals in the final report.""",
        [
            "Per-pass cost recorded in output/<project>/costs.json",
            "Cap breach pauses + asks before continuing",
            "Final log prints spend vs estimate",
        ],
    ),
    (
        "Interactive re-cut: natural-language blueprint patches",
        "Medium", "Feature / Spec needed",
        """drop_cuts.py is the mechanical version. The sellable version:
\"drop cut 54\", \"use the secondary take for the Defect bit\", \"move the
trailer overlay to the verdict\" — small LLM call that edits
blueprint.json through `compact_timeline` + constrained patch ops
(drop/reorder/swap-source/move-overlay), then reconform + re-render beds.
Each patch produces an undoable snapshot.""",
        [
            "Patch op schema (4-6 ops) defined + validated",
            "NL -> ops via one small model call; dry-run shows diff before apply",
            "Undo stack across a session",
        ],
    ),
    (
        "Web UI review gates between passes",
        "Medium", "Feature / Spec needed",
        """The FastAPI + frontend exist (browse, projects, WS progress).
For paying users the trust problem is 'run finished, cut is wrong'.
Add per-stage review: after Pass 1 show/edit the transcript+tags, after
Pass 2 the visual log, after Pass 3 a timeline preview (blueprint
listing like v1_listing.txt) — approve/edit/rerun-that-pass before
continuing. Resume/checkpointing ticket is a prerequisite.""",
        [
            "Pause points configurable (auto | gate at each pass)",
            "UI shows narrative/visual/blueprint and accepts edits persisted to the JSONs",
            "Reject+rerun a single pass without redoing earlier ones",
        ],
    ),
]


def main():
    READY.mkdir(parents=True, exist_ok=True)
    DRAFT.mkdir(parents=True, exist_ok=True)
    for title, prio, typ, summary, acs in ready_tickets:
        slug = (
            title.lower()
            .replace(" / ", "-").replace("/", "-")
            .replace("(", "").replace(")", "")
            .replace(": ", "-").replace(" ", "-").replace("--", "-")
            .replace(",", "").replace("<", "").replace(">", "")
        )[:60].rstrip("-")
        (READY / f"{slug}.md").write_text(
            ticket("Backlog / Ready", prio, typ, title, summary, acs),
            encoding="utf-8",
        )
        print("ready:", slug)
    for title, prio, typ, summary, acs in draft_tickets:
        slug = (
            title.lower()
            .replace(" / ", "-").replace("/", "-")
            .replace("(", "").replace(")", "")
            .replace(": ", "-").replace(" ", "-").replace("--", "-")
            .replace(",", "").replace("<", "").replace(">", "")
        )[:60].rstrip("-")
        (DRAFT / f"{slug}.md").write_text(
            ticket("Backlog / Needs further specification", prio, typ, title, summary, acs),
            encoding="utf-8",
        )
        print("draft:", slug)


if __name__ == "__main__":
    main()
