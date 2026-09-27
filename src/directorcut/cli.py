"""Command-line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from directorcut.config import Settings
from directorcut.log import setup_logging
from directorcut.pipeline import run_pipeline
from directorcut.safety.cost import estimate_cost

console = Console()


@click.group()
@click.option("--config", "-c", type=click.Path(exists=True), help="Path to .env file.")
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def cli(ctx, config, verbose):
    """DirectorCut-AI: automated rough-cut editor.

    Settings load with precedence: process env > --config/.env > the
    per-user config written by `directorcut setup`.
    """
    settings = Settings(_env_file=config) if config else Settings()
    level = "DEBUG" if verbose else settings.log_level
    setup_logging(level, secrets=settings.configured_secrets())
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings


@cli.command()
@click.pass_context
def ingest(ctx):
    """Validate the ingest directory."""
    from directorcut.ingest import ingest as do_ingest

    settings = ctx.obj["settings"]
    manifest = do_ingest(settings.ingest_dir)
    console.print(manifest.model_dump())


@cli.command()
@click.pass_context
def proxy(ctx):
    """Generate audio and visual proxies."""
    from directorcut.ingest import ingest as do_ingest
    from directorcut.proxy.audio import extract_audio
    from directorcut.proxy.visual import generate_visual_proxy

    settings = ctx.obj["settings"]
    manifest = do_ingest(settings.ingest_dir)
    out = settings.output_dir / manifest.project_name
    if manifest.audio_path:
        extract_audio(manifest.audio_path, out)
    if manifest.video_path:
        generate_visual_proxy(manifest.video_path, out)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Estimate only; do not call APIs.")
@click.option(
    "-p", "--project", "project_name", default=None, help="Project name (default: ingest dir name)."
)
@click.option("-y", "--yes", is_flag=True, help="Skip the cost confirmation prompt.")
@click.option(
    "--direction",
    "-d",
    default=None,
    help="Free-text creative direction for the AI director (e.g. 'fast and snappy editing').",
)
@click.option(
    "--fresh",
    is_flag=True,
    help="Ignore output checkpoints; force a full re-run (wipes cached transcripts too).",
)
@click.pass_context
def run(ctx, dry_run, project_name, yes, direction, fresh):
    """Run the full pipeline.

    Auto-resumes from output/<project>/ checkpoints when the ingest inputs
    are unchanged; pass --fresh to discard them and redo every stage.
    """
    from directorcut.ingest import ingest as do_ingest

    settings = ctx.obj["settings"]
    manifest = do_ingest(
        settings.ingest_dir,
        project_name or settings.ingest_dir.name or "project",
        direction=direction,
    )
    # Cost scales with total footage: every video asset gets its own proxy
    # and Pass 2 analysis, regardless of the role it ends up with.
    from directorcut.ingest import VIDEO_EXTS

    video_seconds = sum(
        a.duration_seconds or 0.0
        for a in manifest.assets
        if a.path.suffix.lower() in VIDEO_EXTS
    )
    duration = video_seconds or manifest.duration_seconds or 0.0
    cost_estimate = estimate_cost(duration)
    console.print(
        f"Source duration: {duration:.1f}s  |  Estimated API cost: ${cost_estimate:.4f}"
    )
    if dry_run:
        return
    if cost_estimate > 1.50 and not yes:
        click.confirm(
            f"Estimated cost (${cost_estimate:.4f}) exceeds the $1.50 target. Continue?",
            abort=True,
        )
    from directorcut.exceptions import BudgetExceeded

    try:
        result = run_pipeline(
            settings, manifest.project_name, direction=direction, fresh=fresh
        )
    except BudgetExceeded as exc:
        # The ledger message already explains the cap + resume story - show
        # it verbatim instead of a bare traceback.
        raise click.ClickException(str(exc)) from exc
    console.print(f"FCPXML written to: {result['xml_path']}")
    spent = (result.get("costs") or {}).get("cost_usd")
    if spent is not None:
        console.print(
            f"Recorded spend: ${spent:.4f} (dry-run estimate: ${cost_estimate:.4f})"
            f" - details in costs.json"
        )


@cli.command()
@click.pass_context
def cost(ctx):
    """Show estimated token count and API cost for the current ingest dir."""
    from directorcut.ingest import ingest as do_ingest

    settings = ctx.obj["settings"]
    manifest = do_ingest(settings.ingest_dir)
    estimate = estimate_cost(manifest.duration_seconds or 0.0)
    console.print(f"Estimated cost: ${estimate:.4f}")


@cli.group()
def cut():
    """Patch a finished blueprint: list/drop/patch/undo cuts, re-finalize.

    Operates on output/<project>/blueprint.json; every mutation snapshots
    it to blueprint.prev.json first and re-runs the conform (+ audio bed
    mixdown on Resolve targets). `patch` takes a natural-language
    instruction (e.g. "drop cut 54") via one small model call.
    """


def _project_dir(ctx, project: str) -> Path:
    settings = ctx.obj["settings"]
    project_dir = settings.output_dir / project
    if not (project_dir / "blueprint.json").exists():
        raise click.ClickException(
            f"no blueprint.json at {project_dir} - run the pipeline first"
        )
    return project_dir


@cut.command("list")
@click.argument("project")
@click.pass_context
def cut_list(ctx, project):
    """List cuts on the timeline; V1 rows are numbered for `cut drop`."""
    from directorcut.cutops import CutOpsError, format_rows, list_cuts

    project_dir = _project_dir(ctx, project)
    try:
        rows = list_cuts(project_dir)
    except CutOpsError as exc:
        raise click.ClickException(str(exc)) from exc
    for line in format_rows(rows):
        console.print(line)


@cut.command("drop")
@click.argument("project")
@click.argument("indices", nargs=-1, type=int, required=True)
@click.pass_context
def cut_drop(ctx, project, indices):
    """Drop V1 cuts by their `cut list` numbers, recompact, re-finalize.

    Example: directorcut cut drop myproject 3 7
    """
    from directorcut.cutops import CutOpsError, drop_cuts

    settings = ctx.obj["settings"]
    project_dir = _project_dir(ctx, project)
    try:
        blueprint, dropped = drop_cuts(project_dir, set(indices), settings)
    except CutOpsError as exc:
        raise click.ClickException(str(exc)) from exc
    for c in dropped:
        console.print(
            f"dropped V1 {c.timeline_in}-{c.timeline_out} "
            f"({c.source_file.name} src {c.source_in}-{c.source_out})"
        )
    v1n = sum(1 for c in blueprint.cuts if c.track == "V1_A1_PRIMARY")
    console.print(
        f"blueprint now has {v1n} V1 cut(s); "
        f"FCPXML + audio beds regenerated. Undo: `directorcut cut undo {project}`"
    )


@cut.command("patch")
@click.argument("project")
@click.argument("instruction")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the interpreted ops + resulting diff without applying.",
)
@click.pass_context
def cut_patch(ctx, project, instruction, dry_run):
    """Patch the blueprint from a natural-language instruction.

    Example: directorcut cut patch myproject "drop cut 54 and move the
    trailer overlay to 00:01:12.000"

    One small model call (task='interpret') maps the instruction onto the
    constrained op schema; the planned diff is printed before applying.
    Every apply snapshots blueprint.json first - `cut undo` reverts it.
    """
    from directorcut.cutops import (
        CutOpsError,
        apply_patch,
        interpret_patch,
        preview_patch,
    )
    from directorcut.safety.ledger import activate_cost_ledger

    settings = ctx.obj["settings"]
    project_dir = _project_dir(ctx, project)
    from directorcut.exceptions import BudgetExceeded

    try:
        # The interpret call is a metered Gemini call - ledger it onto the
        # project's costs.json like any other spend.
        with activate_cost_ledger(project_dir, cap_usd=settings.cost_cap_usd):
            plan = interpret_patch(instruction, project_dir, settings)
        blueprint, lines = preview_patch(project_dir, plan.ops)
    except (CutOpsError, BudgetExceeded) as exc:
        raise click.ClickException(str(exc)) from exc

    if plan.rationale:
        console.print(f"plan: {plan.rationale}")
    for line in lines:
        console.print(f"  {line}")
    v1n = sum(1 for c in blueprint.cuts if c.track == "V1_A1_PRIMARY")
    console.print(f"resulting spine: {v1n} V1 cut(s)")
    if dry_run:
        console.print("dry-run - nothing applied")
        return

    try:
        apply_patch(project_dir, plan.ops, settings)
    except CutOpsError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(
        f"applied; FCPXML + audio beds regenerated. "
        f"Undo: `directorcut cut undo {project}`"
    )


@cut.command("undo")
@click.argument("project")
@click.pass_context
def cut_undo(ctx, project):
    """Restore the blueprint snapshot taken before the last `cut drop`."""
    from directorcut.cutops import CutOpsError
    from directorcut.cutops import undo as do_undo

    settings = ctx.obj["settings"]
    project_dir = _project_dir(ctx, project)
    try:
        blueprint = do_undo(project_dir, settings)
    except CutOpsError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"restored {len(blueprint.cuts)} cut(s); FCPXML regenerated")


@cli.command("conform")
@click.argument("project")
@click.pass_context
def conform(ctx, project):
    """Write output/<project>/build_timeline.py - a Resolve-native build script.

    The generated Python script talks to a RUNNING DaVinci Resolve via its
    scripting API: imports the cut sources into the Media Pool, creates a
    timeline named after the project, and places V1/V2/audio cuts at exact
    record frames - no FCPXML interpretation layer. Run it from Workspace >
    Scripts (free Resolve) or with external scripting enabled (Studio).
    """
    from directorcut.conform_resolve import emit_resolve_script
    from directorcut.cutops import CutOpsError, _load_blueprint
    from directorcut.models import IngestManifest

    settings = ctx.obj["settings"]
    project_dir = settings.output_dir / project
    try:
        blueprint = _load_blueprint(project_dir)
    except CutOpsError as exc:
        raise click.ClickException(str(exc)) from exc
    assets = []
    assets_path = project_dir / "assets.json"
    if assets_path.exists():
        try:
            assets = [
                a.path
                for a in IngestManifest.model_validate_json(
                    assets_path.read_text(encoding="utf-8")
                ).assets
            ]
        except (OSError, ValueError) as exc:
            console.print(f"warning: ignoring unreadable assets.json ({exc})")
    out = emit_resolve_script(
        blueprint, assets, project_dir / "build_timeline.py"
    )
    console.print(
        f"Resolve build script: {out}\n"
        "Open Resolve, then run it via Workspace > Scripts (free) or "
        "`python build_timeline.py` with external scripting enabled (Studio)."
    )


@cli.command()
@click.argument("text")
@click.option(
    "-o",
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output audio file (default: ingest/voiceover/pickup-<n>.mp3).",
)
@click.option("--voice", "voice_id", default=None, help="Override ELEVENLABS_VOICE_ID.")
@click.pass_context
def pickup(ctx, text, out_path, voice_id):
    """Generate a spoken pickup line via ElevenLabs TTS.

    For patching a flubbed or missing line without a re-shoot: render the
    line, drop the file in ingest/voiceover/, re-run - the director treats
    it as narrative-carrying audio. Keep it rare; synthesized voice never
    matches room tone perfectly.
    """
    from directorcut.elevenlabs_client import ElevenLabsClient

    settings = ctx.obj["settings"]
    if out_path is None:
        vdir = settings.ingest_dir / "voiceover"
        vdir.mkdir(parents=True, exist_ok=True)
        n = len(list(vdir.glob("pickup-*"))) + 1
        out_path = vdir / f"pickup-{n:02d}.mp3"
    try:
        blob, ext = ElevenLabsClient(settings).text_to_speech(
            text, voice_id=voice_id
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    out_path = out_path.with_suffix(f".{ext}")
    out_path.write_bytes(blob)
    console.print(f"wrote {out_path} ({len(blob)/1024:.0f} KiB)")


@cli.command()
def setup():
    """First-run wizard: collect keys + backend/model prefs.

    Writes the per-user config (`%APPDATA%/DirectorCut/config.env` on
    Windows, `~/.config/directorcut/config.env` elsewhere) - NEVER the repo
    .env. Values already in that file are kept when a prompt is left blank;
    a repo .env or exported env var always overrides them.
    """
    from directorcut.config import (
        read_user_config,
        user_config_path,
        write_user_config,
    )

    path = user_config_path()
    existing = read_user_config(path)
    defaults = Settings(_env_file=None)  # field defaults + process env only

    console.print(f"DirectorCut setup - writing keys to {path}")
    console.print("Nothing is sent anywhere; blank input keeps/skips a value.")

    values: dict[str, str] = {}
    # hide_input only on a real TTY: getpass' Windows path (msvcrt.getwch)
    # reads the console and ignores piped stdin, which would hang scripts.
    hide_keys = sys.stdin.isatty()
    for var, label in (
        ("GEMINI_FREE_KEY_MAIN", "Gemini free-tier API key (used first)"),
        ("GEMINI_FREE_KEY", "Gemini free-tier API key (backup)"),
        ("GEMINI_PAID_KEY_MAIN", "Gemini paid API key (main)"),
        ("GEMINI_PAID_KEY", "Gemini paid API key (secondary/failover)"),
        ("ELEVENLABS_API_KEY", "ElevenLabs API key (Scribe STT, music/SFX)"),
        ("ELEVENLABS_VOICE_ID", "ElevenLabs voice ID for spoken pickup lines"),
    ):
        # hide_input + no default echo: existing keys must never be printed.
        answer = click.prompt(
            label, default="", show_default=False, hide_input=hide_keys
        ).strip()
        values[var] = answer or existing.get(var, "")

    backend_default = existing.get("GEMINI_BACKEND") or defaults.gemini_backend
    values["GEMINI_BACKEND"] = click.prompt(
        "Gemini backend",
        type=click.Choice(["api", "cli", "antigravity"]),
        default=backend_default
        if backend_default in ("api", "cli", "antigravity")
        else "api",
    )
    values["GEMINI_MODEL"] = click.prompt(
        "Gemini model",
        default=existing.get("GEMINI_MODEL") or defaults.gemini_model,
    )
    values["GEMINI_MODEL_FAST"] = (
        click.prompt(
            "Gemini fast model for high-volume calls (blank = same as main)",
            default=existing.get("GEMINI_MODEL_FAST") or "",
            show_default=False,
        ).strip()
        or existing.get("GEMINI_MODEL_FAST", "")
    )
    stt_default = existing.get("DIRECTORCUT_STT_BACKEND") or "auto"
    values["DIRECTORCUT_STT_BACKEND"] = click.prompt(
        "Speech-to-text backend",
        type=click.Choice(["auto", "elevenlabs", "whisper"]),
        default=stt_default
        if stt_default in ("auto", "elevenlabs", "whisper")
        else "auto",
    )
    if values["DIRECTORCUT_STT_BACKEND"] != "elevenlabs":
        values["DIRECTORCUT_WHISPER_MODEL"] = click.prompt(
            "Whisper model (local STT; needs `pip install faster-whisper`)",
            default=existing.get("DIRECTORCUT_WHISPER_MODEL")
            or defaults.whisper_model,
        )
    # Keys are only honored outside dev/test (model calls stub otherwise) -
    # default to prod when a Gemini key is configured so `run` uses them.
    has_gemini = any(
        values[k]
        for k in (
            "GEMINI_PAID_KEY_MAIN",
            "GEMINI_PAID_KEY",
            "GEMINI_FREE_KEY",
            "GEMINI_FREE_KEY_MAIN",
        )
    )
    env_default = existing.get("DIRECTORCUT_ENV") or ("prod" if has_gemini else "dev")
    values["DIRECTORCUT_ENV"] = click.prompt(
        "Environment (dev = offline stubs, prod = real API calls)",
        type=click.Choice(["dev", "prod"]),
        default=env_default if env_default in ("dev", "prod") else "prod",
    )

    written = write_user_config(values, path)
    console.print(f"Wrote {written} ({sum(1 for v in values.values() if v)} value(s)).")
    console.print(
        "ffmpeg + ffprobe must be on PATH - they are never bundled. "
        "Try `directorcut demo` for a fully offline ingest->FCPXML run."
    )


@cli.command()
@click.option(
    "--workdir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for the demo's ingest + output (default: a temp dir).",
)
@click.option(
    "-p", "--project", "project_name", default="demo", help="Project name."
)
@click.pass_context
def demo(ctx, workdir, project_name):
    """Run a fully offline demo: synthesize a ~5s clip, produce an FCPXML.

    Needs no keys and no network - media comes from `ffmpeg -f lavfi` and
    every model/API call is stubbed (dev mode). ffmpeg/ffprobe must be on
    PATH.
    """
    from directorcut.demo import run_demo
    from directorcut.exceptions import FFmpegError

    try:
        result = run_demo(workdir, project_name)
    except FFmpegError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"Demo workspace: {result['workdir']}")
    console.print(f"FCPXML written to: {result['xml_path']}")


@cli.command("web")
@click.pass_context
def web(ctx):
    """Start the web UI server."""
    settings = ctx.obj["settings"]
    import uvicorn

    uvicorn.run(
        "directorcut.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.dev_mode,
    )


def main():
    cli()


if __name__ == "__main__":
    main()
