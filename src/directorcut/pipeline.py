"""End-to-end DirectorCut-AI pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from directorcut.assembly.director import (
    _canonical_source_keys,
    _enforce_unique_source,
    run_director,
)
from directorcut.assembly.targets import (
    correction_note,
    effective_targets,
    measure_blueprint,
    report_line,
    violations,
)
from directorcut.assembly.word_air import pad_v1_cut_edges, words_from_scribe
from directorcut.assets import interpret_assets
from directorcut.audio_design import render_cues
from directorcut.config import Settings
from directorcut.ffmpeg import run_ffprobe
from directorcut.finalize import finalize_blueprint
from directorcut.ingest import VIDEO_EXTS, _load_direction, ingest
from directorcut.log import get_logger
from directorcut.media_prep import MEDIA_DIR_NAME, prepare_aroll_media
from directorcut.models import (
    AssemblyBlueprint,
    Asset,
    AssetRole,
    Cut,
    IngestManifest,
    NarrativeNode,
    VisualEvent,
)
from directorcut.narrative.ingest import load_narrative_input
from directorcut.narrative.stt import load_cached_words, transcribe_audio
from directorcut.narrative.tagger import tag_narrative
from directorcut.narrative.transcript import transcribe
from directorcut.proxy.audio import extract_audio, extract_audio_tracks
from directorcut.proxy.visual import generate_visual_proxy
from directorcut.safety.ledger import activate_cost_ledger
from directorcut.visual.chunked import analyze_visual_proxy

logger = get_logger(__name__)

# --- Stage checkpointing -----------------------------------------------------
# output/<project>/run_state.json stores a content-hash fingerprint of every
# ingest file plus the effective direction. A rerun with a matching
# fingerprint resumes from the checkpoint artifacts (assets.json,
# narrative.json, visual.json + visual_chunks/*.jsonl, blueprint.json);
# anything else invalidates what changed. STT transcripts have their own
# content-addressed cache under stt_cache/ (see narrative/stt.py).
_RUN_STATE_FILE = "run_state.json"
_STT_CACHE_DIR = "stt_cache"
_CHUNK_DIR = "visual_chunks"
_AUDIO_CUES_DIR = "audio"
_STAGE_JSONS = ("assets.json", "narrative.json", "visual.json", "blueprint.json")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def _ingest_fingerprint(ingest_dir: Path, direction: str | None) -> dict:
    """Content-hash every ingest file plus the effective direction.

    Content hashes (not mtimes) answer "did the inputs change" honestly -
    file copies preserve mtime while re-exports change bytes. The direction
    is resolved the way ingest() resolves it: the --direction flag wins over
    direction.txt, whose content is covered by the file hashes anyway.
    """
    files: dict[str, str] = {}
    if ingest_dir.exists():
        for path in sorted(ingest_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ingest_dir).as_posix()
            try:
                files[rel] = _sha256_file(path)
            except OSError:
                files[rel] = "<unreadable>"
    return {
        "ingest_dir": str(ingest_dir.resolve()),
        "direction": direction or _load_direction(ingest_dir) or "",
        "files": files,
    }


def _load_run_state(output_dir: Path) -> dict | None:
    path = output_dir / _RUN_STATE_FILE
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


def _save_run_state(output_dir: Path, fingerprint: dict, stages: dict) -> None:
    payload = {
        "fingerprint": fingerprint,
        "stages": stages,
        "updated_at": _now(),
    }
    # Write-then-rename: a crash mid-write must not leave a torn state file
    # that would silently invalidate (or worse, validate) the wrong artifacts.
    tmp = output_dir / f"{_RUN_STATE_FILE}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, output_dir / _RUN_STATE_FILE)


def _write_json(path: Path, payload: str) -> None:
    """Atomically write a JSON artifact (tmp file + rename)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _load_models(path: Path, model) -> list | None:
    """Load a checkpointed model list; None when missing or unreadable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [model.model_validate(item) for item in data]
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        logger.warning("Ignoring unreadable checkpoint %s: %s", path.name, exc)
        return None


def _chunk_checkpoint_path(chunk_dir: Path, source: Path) -> Path:
    """Per-asset Pass-2 checkpoint file, disambiguated by path hash.

    Two assets can share a filename stem (a/take.mp4, b/take.mp4) - the
    checkpoint name must differ or a resumed run would cross-wire events.
    """
    suffix = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    return chunk_dir / f"{source.stem}_{suffix}.jsonl"


def _is_generated_cue_cut(cut: Cut, output_dir: Path) -> bool:
    """True for cuts sourced from render_cues output (output_dir/audio/*).

    Those cuts are reproducible artifacts, not director decisions: on resume
    they are dropped and re-derived from the reused audio files, which keeps
    a checkpointed blueprint idempotent (no doubled cue cuts).
    """
    try:
        return cut.source_file.resolve().is_relative_to(
            (output_dir / _AUDIO_CUES_DIR).resolve()
        )
    except OSError:
        return False


def _invalidate_output(
    output_dir: Path,
    project_name: str,
    changed_stems: set[str],
    fresh: bool,
) -> None:
    """Drop checkpoint artifacts that can no longer be trusted.

    Stage JSONs and the FCPXML mix inputs from every asset, so ANY input
    change invalidates all of them. Derived media is per-asset: only files
    whose ingest source changed get their proxies/streams deleted (ffmpeg
    re-renders on demand via the existing exists()-skip). fresh=True wipes
    every derived artifact including the content-addressed STT cache.
    """
    for name in _STAGE_JSONS:
        (output_dir / name).unlink(missing_ok=True)
    (output_dir / f"{project_name}.fcpxml").unlink(missing_ok=True)
    shutil.rmtree(output_dir / _CHUNK_DIR, ignore_errors=True)
    # Rendered beds are blueprint-derived - an invalidated blueprint makes
    # them stale, and they are cheap to re-render on the next conform.
    for name in ("voice_guide.wav", "music_bed_ducked.wav", "sfx_bed.wav"):
        (output_dir / name).unlink(missing_ok=True)
    for f in output_dir.glob("*.ffgraph"):
        f.unlink(missing_ok=True)
    if fresh:
        shutil.rmtree(output_dir / _STT_CACHE_DIR, ignore_errors=True)
        shutil.rmtree(output_dir / _AUDIO_CUES_DIR, ignore_errors=True)
        shutil.rmtree(output_dir / MEDIA_DIR_NAME, ignore_errors=True)
        for pattern in ("*_visual.mp4", "*_audio.mp3", "*_stream*.mp3"):
            for f in output_dir.glob(pattern):
                f.unlink(missing_ok=True)
    else:
        for stem in changed_stems:
            (output_dir / f"{stem}_visual.mp4").unlink(missing_ok=True)
            (output_dir / f"{stem}_audio.mp3").unlink(missing_ok=True)
            for f in output_dir.glob(f"{stem}_stream*.mp3"):
                f.unlink(missing_ok=True)
            # Baked media keyed on the ingest stem, plus the proxies/streams
            # that were derived from the baked file (named after its stem).
            for f in (output_dir / MEDIA_DIR_NAME).glob(f"{stem}_*"):
                f.unlink(missing_ok=True)
            for baked_stem in ("_reframed", "_voicenorm"):
                for f in output_dir.glob(f"{stem}{baked_stem}_*"):
                    f.unlink(missing_ok=True)


def _audio_proxy(manifest: IngestManifest, output_dir: Path) -> Path | None:
    """Pick the audio file Pass 1 transcribes.

    Prefers the classified voiceover (which may itself be an extracted
    stream - already an mp3 in output_dir, no re-encode needed), then
    stream 0 of a multi-track video, then the video's mixed audio.
    """
    voice = manifest.audio_path
    if voice is not None:
        try:
            if voice.resolve().is_relative_to(output_dir.resolve()):
                return voice
        except OSError:
            pass
        return extract_audio(voice, output_dir)
    if manifest.video_path is not None:
        stream0 = output_dir / f"{manifest.video_path.stem}_stream0.mp3"
        if stream0.exists():
            return stream0
        return extract_audio(manifest.video_path, output_dir)
    return None


def _transcript_sources(
    manifest: IngestManifest, output_dir: Path
) -> list[tuple[Path, Path]]:
    """Return (audio_to_transcribe, narrative source_file) pairs for Pass 1.

    Every A-roll video contributes its audio (the already-extracted
    `_stream0` when multi-track splitting ran, else a fresh `extract_audio`
    proxy), and every standalone VOICEOVER asset contributes itself - except
    audio files that are just extracted streams of an A-roll video (they'd
    transcribe the same speech twice). The `source_file` stamped on nodes is
    the *video* path for video-derived audio so Pass 3 can cut it directly.
    """
    sources: list[tuple[Path, Path]] = []
    aroll_stems: list[str] = []
    for asset in manifest.assets_with_role(AssetRole.A_ROLL):
        if asset.path.suffix.lower() not in VIDEO_EXTS:
            continue
        aroll_stems.append(asset.path.stem)
        stream0 = output_dir / f"{asset.path.stem}_stream0.mp3"
        if asset.derived_from is not None:
            # Streams were extracted from the ingest original before media
            # prep re-pointed the asset - `X_stream0.mp3` (under the ORIGINAL
            # stem) still belongs to this source, and keeps the STT input
            # identical to a no-bake run.
            aroll_stems.append(asset.derived_from.stem)
            orig_stream0 = output_dir / f"{asset.derived_from.stem}_stream0.mp3"
            if not stream0.exists() and orig_stream0.exists():
                stream0 = orig_stream0
        audio = (
            stream0 if stream0.exists() else extract_audio(asset.path, output_dir)
        )
        sources.append((audio, asset.path))
    for asset in manifest.assets_with_role(AssetRole.VOICEOVER):
        path = asset.path
        try:
            if path.resolve().is_relative_to(output_dir.resolve()):
                continue  # extracted stream, already covered by its video
        except OSError:
            pass
        if any(path.stem.startswith(stem) for stem in aroll_stems):
            continue  # e.g. <aroll>_stream0.mp3 / <aroll>_audio.mp3
        sources.append((path, path))
    return sources


def _run_gate(gate: Callable[[str], None] | None, stage: str) -> None:
    """Invoke the caller's review-gate hook for `stage` (pass1/2/3).

    The stage's checkpoint JSON is already on disk when this fires. A broken
    gate callback must not sink a run - log and continue.
    """
    if gate is None:
        return
    try:
        gate(stage)
    except Exception:  # noqa: BLE001
        logger.warning("gate hook for %s failed - continuing run", stage, exc_info=True)


def _director_with_target_check(
    manifest: IngestManifest,
    nodes: list[NarrativeNode],
    events: list[VisualEvent],
    settings: Settings,
    emit: Callable[[str, str], None] | None = None,
) -> AssemblyBlueprint:
    """Run the director, then enforce hard length/B-roll targets.

    Production finding (StS2 EA-review): attempt 1 shipped 4:15 against a
    7-9min direction - the model can't see its own numbers, so targets are
    measured here and ONE corrective re-prompt carries the deltas back. A
    second retry is deliberately not attempted (costs a full director call,
    usually lands close enough; the report line surfaces the final state).
    """
    emit = emit or (lambda stage, detail="": None)
    blueprint = run_director(manifest, nodes, events, settings)
    emit(
        "pass3",
        f"{len(blueprint.cuts)} cuts, {len(blueprint.audio_cues)} audio cue(s)",
    )
    targets = effective_targets(settings, manifest.direction)
    if not targets.has_any:
        return blueprint
    length_s, coverage = measure_blueprint(blueprint)
    bad = violations(targets, length_s, coverage)
    if not bad:
        return blueprint
    note = correction_note(targets, bad)
    logger.warning(
        "[pass3] target miss (%s) - corrective re-prompt", "; ".join(bad)
    )
    emit("pass3", "target miss - corrective director re-prompt")
    blueprint = run_director(
        manifest, nodes, events, settings, correction=note
    )
    emit("pass3", f"{len(blueprint.cuts)} cuts after correction")
    return blueprint


def _load_word_timings(
    manifest: IngestManifest, output_dir: Path, settings: Settings
) -> dict[Path, list[tuple[float, float]]]:
    """Word-level (start, end) timings per narrative source, from STT cache.

    Keys are the cut-facing `source_file` values (the A-roll video path),
    matching `Cut.source_file` / `NarrativeNode.source_file`. Script-path
    projects and runs where STT was unavailable simply yield {} - edge
    padding is skipped then.
    """
    stt_cache = output_dir / _STT_CACHE_DIR
    timings: dict[Path, list[tuple[float, float]]] = {}
    for audio_path, source_file in _transcript_sources(manifest, output_dir):
        entries = load_cached_words(audio_path, settings, stt_cache)
        if not entries:
            continue
        words = words_from_scribe(entries)
        if words:
            timings[source_file] = words
    return timings


def _proxy_duration(proxy_path) -> float:
    probe = run_ffprobe([
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(proxy_path),
    ])
    return float(probe.strip())


def run_pipeline(
    settings: Settings,
    project_name: str,
    direction: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    fresh: bool = False,
    gate: Callable[[str], None] | None = None,
) -> dict:
    """Run the full pipeline from ingestion to FCPXML.

    Auto-resumes from output/<project>/ checkpoints when the ingest inputs
    are unchanged (content-hash fingerprint in run_state.json); `fresh=True`
    forces a full re-run, deleting every derived artifact.

    `gate`, when given, is invoked after each gated stage's checkpoint JSON
    lands ("pass1"/"pass2"/"pass3") - the caller may block inside it (the web
    API's review gates wait on a threading.Event). After it returns, the
    stage artifact is reloaded from disk so edits made while paused take
    effect for the rest of the run.

    Every metered API call inside lands in output/<project>/costs.json via
    the activated CostLedger (accumulates across resumes + `cut patch`
    calls). When DIRECTORCUT_COST_CAP_USD is set and recorded spend passes
    it, the run aborts with BudgetExceeded - checkpoints stay intact so a
    raised cap + re-run resumes where it stopped.
    """
    output_dir = settings.output_dir / project_name
    output_dir.mkdir(parents=True, exist_ok=True)
    with activate_cost_ledger(output_dir, cap_usd=settings.cost_cap_usd) as ledger:
        result = _run_pipeline_stages(
            settings, project_name, direction, on_progress, fresh, gate
        )
        totals = ledger.totals()
        result["costs"] = totals
        logger.info(
            "Run spend: $%.4f recorded in %s (%d metered call(s))",
            totals["cost_usd"],
            ledger.path.name,
            totals["gemini_calls"],
        )
        return result


def _run_pipeline_stages(
    settings: Settings,
    project_name: str,
    direction: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    fresh: bool = False,
    gate: Callable[[str], None] | None = None,
) -> dict:
    """Stage driver behind `run_pipeline` - assumes the caller already
    activated the cost ledger (direct calls simply record nothing)."""
    started = time.time()

    def emit(stage: str, detail: str = "") -> None:
        # Progress reporting must never break a run - swallow whatever the
        # callback hits (dead websocket, closed queue, ...).
        if on_progress is None:
            return
        try:
            on_progress(
                {
                    "status": "progress",
                    "stage": stage,
                    "detail": detail,
                    "elapsed_s": round(time.time() - started, 1),
                }
            )
        except Exception:  # noqa: BLE001
            logger.debug("progress callback failed", exc_info=True)

    output_dir = settings.output_dir / project_name
    output_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = _ingest_fingerprint(settings.ingest_dir, direction)
    state = _load_run_state(output_dir)
    stages: dict[str, str] = {}
    resume = False
    if fresh:
        logger.info("--fresh: wiping derived artifacts for %s", project_name)
        _invalidate_output(output_dir, project_name, changed_stems=set(), fresh=True)
    elif state is None:
        # Artifacts left by a pre-checkpoint run can't be tied back to their
        # inputs: drop the stage JSONs but keep proxies (exist-skip reuse)
        # and the content-addressed STT cache.
        if any(output_dir.iterdir()):
            logger.info(
                "No %s in %s - existing artifacts treated as untrusted",
                _RUN_STATE_FILE,
                output_dir,
            )
            _invalidate_output(output_dir, project_name, changed_stems=set(), fresh=False)
    elif state.get("fingerprint") == fingerprint:
        resume = True
        stages = dict(state.get("stages") or {})
        logger.info("Resuming %s - ingest inputs unchanged", project_name)
        emit("ingest", "inputs unchanged - resuming from checkpoints")
    else:
        old_files = (state.get("fingerprint") or {}).get("files") or {}
        new_files = fingerprint["files"]
        changed = {
            rel
            for rel in set(old_files) | set(new_files)
            if old_files.get(rel) != new_files.get(rel)
        }
        logger.info(
            "Ingest inputs changed (%d file(s)) - invalidating stale checkpoints",
            len(changed),
        )
        emit("ingest", f"inputs changed ({len(changed)} file(s)) - full re-run")
        _invalidate_output(
            output_dir,
            project_name,
            changed_stems={Path(rel).stem for rel in changed},
            fresh=False,
        )
    _save_run_state(output_dir, fingerprint, stages)

    # --- Ingest + interpretation ---------------------------------------------
    # On resume the post-interpretation manifest reloads from assets.json,
    # skipping ingest probing, stream extraction, and the classifier call.
    assets_path = output_dir / "assets.json"
    manifest: IngestManifest | None = None
    fresh_ingest = False
    if resume and assets_path.exists():
        try:
            manifest = IngestManifest.model_validate_json(
                assets_path.read_text(encoding="utf-8")
            ).model_copy(update={"project_name": project_name})
            emit(
                "ingest",
                f"resumed: {len(manifest.assets)} asset(s) from assets.json",
            )
            emit("interpret", "resumed: roles from checkpoint")
        except (OSError, ValidationError, ValueError) as exc:
            logger.warning("assets.json unreadable (%s) - re-ingesting", exc)
            manifest = None

    if manifest is None:
        fresh_ingest = True
        manifest = ingest(settings.ingest_dir, project_name, direction=direction)
        emit(
            "ingest",
            f"{len(manifest.assets)} asset(s), "
            f"{manifest.duration_seconds or 0:.0f}s source",
        )

        logger.info("[proxy] Extracting audio and visual proxies")
        # Multi-track videos (e.g. OBS keeps mic/game/desktop on separate
        # streams): pull each embedded stream out as its own asset BEFORE the
        # interpretation pass so the AI can classify mic vs. gameplay audio,
        # and Pass 3 can place the streams independently on the timeline.
        stream_assets: list[Asset] = []
        for asset in manifest.assets:
            if (
                asset.path.suffix.lower() in VIDEO_EXTS
                and asset.audio_stream_count > 1
            ):
                emit(
                    "proxy",
                    f"extracting {asset.audio_stream_count} audio streams "
                    f"from {asset.path.name}",
                )
                for i, stream_path in enumerate(
                    extract_audio_tracks(
                        asset.path, output_dir, asset.audio_stream_count
                    )
                ):
                    stream_assets.append(
                        Asset(
                            path=stream_path,
                            role=AssetRole.AUTO,
                            duration_seconds=asset.duration_seconds,
                            description=(
                                f"audio stream {i} embedded in {asset.path.name}"
                            ),
                        )
                    )
        if stream_assets:
            logger.info(
                "Extracted %d embedded audio stream(s)", len(stream_assets)
            )
            manifest = manifest.model_copy(
                update={"assets": [*manifest.assets, *stream_assets]}
            )

        emit("interpret", "classifying asset roles via model")
        manifest = interpret_assets(manifest, settings)
        emit(
            "interpret",
            f"roles: {', '.join(f'{a.path.name}={a.role.value}' for a in manifest.assets)}",
        )

    # Media prep runs AFTER role resolution (interpret) and on every path -
    # it's idempotent: an already-baked asset reuses its media/ file, and a
    # deleted one is re-rendered from `derived_from`. Downstream stages
    # (STT, proxies, blueprint, FCPXML) then only ever see the baked media -
    # Resolve never touches rotation side-data or unnormalized voice.
    emit("ingest", "media prep: reframe/voice-normalize checks")
    manifest, media_changed = prepare_aroll_media(
        manifest, output_dir / MEDIA_DIR_NAME, settings
    )
    if media_changed:
        emit("ingest", "media prep: baked reframed/normalized A-roll")

    if fresh_ingest or media_changed:
        # Checkpoint the post-interpretation manifest NOW - a crash further
        # down used to redo ingest, stream extraction, and this model call.
        _write_json(assets_path, manifest.model_dump_json(indent=2))
        stages["ingest"] = stages["interpret"] = _now()
        _save_run_state(output_dir, fingerprint, stages)

    video_assets = [
        a
        for a in manifest.assets_with_role(AssetRole.A_ROLL, AssetRole.B_ROLL)
        if a.path.suffix.lower() in VIDEO_EXTS
    ]

    # --- Pass 1: narrative ----------------------------------------------------
    # narrative.json is written BEFORE Pass 2 starts so a crash mid-Pass-2
    # never redoes transcription or tagging.
    narrative_path = output_dir / "narrative.json"
    nodes = _load_models(narrative_path, NarrativeNode) if resume else None
    if nodes is not None:
        emit("pass1", f"resumed: {len(nodes)} narrative node(s) from narrative.json")
    else:
        logger.info("[pass1] Narrative mapping")
        emit("pass1", "transcribing + tagging narrative")
        audio_proxy = _audio_proxy(manifest, output_dir)
        emit(
            "proxy",
            f"transcript source: {audio_proxy.name if audio_proxy else 'none'}",
        )
        script_paths = [a.path for a in manifest.assets_with_role(AssetRole.SCRIPT)]
        nar_input = load_narrative_input(script_paths, audio_proxy)
        if nar_input.has_script:
            nodes = transcribe(nar_input.text, nar_input.audio_path)
            nodes = tag_narrative(nodes, settings, direction=manifest.direction)
            emit("pass1", f"{len(nodes)} narrative node(s) tagged")
        else:
            # Unscripted footage: transcribe EVERY speech source (multiple
            # A-roll takes plus standalone voiceovers), then fall back to
            # full-duration segment markers when STT is unavailable. The STT
            # cache makes a re-run after a crash free for finished sources.
            nodes = None
            sources = _transcript_sources(manifest, output_dir)
            if sources:
                emit(
                    "pass1",
                    f"transcribing {len(sources)} source(s) via "
                    f"{settings.resolved_stt_backend} STT",
                )
                collected = []
                stt_cache = output_dir / _STT_CACHE_DIR
                for i, (audio_path, source_file) in enumerate(sources, start=1):
                    part = transcribe_audio(audio_path, settings, cache_dir=stt_cache)
                    if part is None:
                        continue
                    collected.extend(
                        n.model_copy(
                            update={
                                "node_id": f"S{i}_{n.node_id}",
                                "source_file": source_file,
                            }
                        )
                        for n in part
                    )
                nodes = collected or None
            if nodes:
                emit("pass1", f"STT produced {len(nodes)} node(s) - tagging")
                nodes = tag_narrative(nodes, settings, direction=manifest.direction)
                emit("pass1", f"{len(nodes)} narrative node(s) tagged")
            else:
                nodes = transcribe(
                    None,
                    nar_input.audio_path,
                    duration_seconds=manifest.duration_seconds,
                )
                emit("pass1", f"unscripted - {len(nodes)} segment node(s)")
        _write_json(
            narrative_path,
            json.dumps([n.model_dump(mode="json") for n in nodes], indent=2),
        )
        stages["pass1"] = _now()
        _save_run_state(output_dir, fingerprint, stages)

    # Review gate: narrative.json is committed; if the caller paused the run
    # and edited it, reload so Pass 3 sees the edits (Pass 2 doesn't consume
    # nodes, but the reload keeps the in-memory state honest).
    _run_gate(gate, "pass1")
    if gate is not None:
        reloaded = _load_models(narrative_path, NarrativeNode)
        if reloaded is not None:
            nodes = reloaded

    # --- Pass 2: visual -------------------------------------------------------
    # Per-chunk JSONL checkpoints under visual_chunks/ mean a crash mid-Pass-2
    # loses only the in-flight chunk; finished chunks (and whole finished
    # assets) are replayed from disk.
    visual_path = output_dir / "visual.json"
    events = _load_models(visual_path, VisualEvent) if resume else None
    if events is not None:
        emit("pass2", f"resumed: {len(events)} visual event(s) from visual.json")
    else:
        logger.info("[pass2] Visual logging")
        events = []
        chunk_dir = output_dir / _CHUNK_DIR
        # Assets registered under several paths but sharing one physical
        # recording (dual-role hardlink, or an ingest derivative) get ONE
        # visual analysis - the events are then cloned per registered path
        # so V1/V2 cuts still reference their own source_file.
        canon = _canonical_source_keys(manifest)
        analyzed: dict[str, list[VisualEvent]] = {}
        for i, asset in enumerate(video_assets, start=1):
            key = canon.get(asset.path.resolve(), str(asset.path))
            if key in analyzed:
                cloned = [
                    e.model_copy(update={"source_file": asset.path})
                    for e in analyzed[key]
                ]
                events.extend(cloned)
                emit(
                    "pass2",
                    f"{asset.path.name}: reused analysis of canonical twin "
                    f"({len(cloned)} event(s))",
                )
                continue
            proxy = generate_visual_proxy(asset.path, output_dir)
            duration = asset.duration_seconds or _proxy_duration(proxy)
            emit(
                "pass2",
                f"visual analysis {i}/{len(video_assets)}: {asset.path.name}",
            )

            def _on_chunk(ci: int, cn: int, _asset: Asset = asset) -> None:
                emit("pass2", f"{_asset.path.name}: chunk {ci}/{cn} via model")

            asset_events = analyze_visual_proxy(
                proxy,
                duration,
                settings,
                source_file=asset.path,
                on_chunk=_on_chunk,
                checkpoint_path=_chunk_checkpoint_path(chunk_dir, asset.path),
            )
            events.extend(asset_events)
            analyzed[key] = asset_events
        emit("pass2", f"{len(events)} visual event(s)")
        _write_json(
            visual_path,
            json.dumps([e.model_dump(mode="json") for e in events], indent=2),
        )
        stages["pass2"] = _now()
        _save_run_state(output_dir, fingerprint, stages)

    _run_gate(gate, "pass2")
    if gate is not None:
        reloaded = _load_models(visual_path, VisualEvent)
        if reloaded is not None:
            events = reloaded

    # --- Pass 3: assembly ------------------------------------------------------
    blueprint_path = output_dir / "blueprint.json"
    blueprint: AssemblyBlueprint | None = None
    if resume and blueprint_path.exists():
        try:
            loaded = AssemblyBlueprint.model_validate_json(
                blueprint_path.read_text(encoding="utf-8")
            )
            # Cuts pointing at generated audio are render_cues artifacts, not
            # director decisions - drop them so the render below re-derives
            # them from the reused files instead of appending duplicates.
            kept = [
                c for c in loaded.cuts if not _is_generated_cue_cut(c, output_dir)
            ]
            blueprint = loaded.model_copy(update={"cuts": kept})
            emit("pass3", f"resumed: {len(kept)} cut(s) from blueprint.json")
        except (OSError, ValidationError, ValueError) as exc:
            logger.warning(
                "blueprint.json unreadable (%s) - re-running director", exc
            )
    if blueprint is None:
        logger.info("[pass3] Assembly")
        emit("pass3", "director assembling blueprint")
        blueprint = _director_with_target_check(
            manifest, nodes, events, settings, emit
        )
        emit(
            "pass3",
            f"{len(blueprint.cuts)} cuts, {len(blueprint.audio_cues)} audio cue(s)",
        )
        # Checkpoint the director's plan BEFORE rendering cues: on resume the
        # cue render re-runs (reusing existing audio files) and re-appends.
        _write_json(blueprint_path, blueprint.model_dump_json(indent=2))
        stages["pass3"] = _now()
        _save_run_state(output_dir, fingerprint, stages)

    # Review gate BEFORE cue rendering: blueprint.json on disk is still the
    # director's plan (no generated cue cuts appended yet), so edited
    # audio_cues/cuts flow through render_cues below. Reload strips generated
    # cue cuts exactly like the resume path - they get re-derived from the
    # reused audio files.
    _run_gate(gate, "pass3")
    if gate is not None and blueprint_path.exists():
        try:
            loaded = AssemblyBlueprint.model_validate_json(
                blueprint_path.read_text(encoding="utf-8")
            )
            kept = [
                c for c in loaded.cuts if not _is_generated_cue_cut(c, output_dir)
            ]
            blueprint = loaded.model_copy(update={"cuts": kept})
        except (OSError, ValidationError, ValueError) as exc:
            logger.warning(
                "blueprint.json unreadable after gate (%s) - keeping "
                "in-memory blueprint",
                exc,
            )

    logger.info("[audio] Rendering generated music/SFX cues")
    emit("audio", f"rendering {len(blueprint.audio_cues)} cue(s)")
    cue_cuts = render_cues(blueprint.audio_cues, output_dir, settings)
    if cue_cuts:
        blueprint = blueprint.model_copy(
            update={"cuts": [*blueprint.cuts, *cue_cuts]}
        )

    # Pad V1 edges onto silence using STT word timing ("sentence boundaries
    # with ~0.2s air" - the director can't hear, this enforces it). The
    # content-addressed STT cache still holds the raw Scribe words[].
    words_map = _load_word_timings(manifest, output_dir, settings)
    if words_map:
        padded = pad_v1_cut_edges(list(blueprint.cuts), words_map)
        if padded != blueprint.cuts:
            blueprint = blueprint.model_copy(update={"cuts": padded})
            emit("pass3", "V1 edges padded onto word-boundary silence")
        # Edge padding can re-open source-range reuse the director pass
        # already cleaned up - repair trims (or drops) any residual overlap.
        repaired = _enforce_unique_source(
            list(blueprint.cuts), nodes, _canonical_source_keys(manifest)
        )
        if repaired != blueprint.cuts:
            blueprint = blueprint.model_copy(update={"cuts": repaired})

    # Final blueprint (with materialized cue cuts) - same shape as before.
    _write_json(blueprint_path, blueprint.model_dump_json(indent=2))

    # Always report measured length + coverage vs the targets (even on
    # resume) so a miss is visible in the run output, not silently shipped.
    targets = effective_targets(settings, manifest.direction)
    length_s, coverage = measure_blueprint(blueprint)
    logger.info(report_line(targets, length_s, coverage))
    emit("pass3", report_line(targets, length_s, coverage))

    logger.info("[conform] Finalizing FCPXML (target=%s)", settings.fcpxml_target)
    emit("conform", "building FCPXML")
    # finalize_blueprint runs the mixdown stage first for Resolve targets:
    # music_bed_ducked.wav / sfx_bed.wav are pre-rendered and attached as
    # single clips because Resolve ignores <param> volume keyframes.
    xml_path = finalize_blueprint(blueprint, output_dir, settings)
    stages["conform"] = _now()
    _save_run_state(output_dir, fingerprint, stages)

    logger.info("Done. FCPXML: %s", xml_path)
    return {
        "manifest": manifest,
        "nodes": nodes,
        "events": events,
        "blueprint": blueprint,
        "xml_path": xml_path,
        "output_dir": output_dir,
    }
