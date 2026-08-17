"""Command-line interface."""

from __future__ import annotations

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
    """DirectorCut-AI: automated rough-cut editor."""
    settings = Settings(_env_file=config if config else ".env")
    level = "DEBUG" if verbose else settings.log_level
    setup_logging(level)
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
@click.pass_context
def run(ctx, dry_run, project_name, yes):
    """Run the full pipeline."""
    from directorcut.ingest import ingest as do_ingest

    settings = ctx.obj["settings"]
    manifest = do_ingest(settings.ingest_dir, project_name or settings.ingest_dir.name or "project")
    duration = manifest.duration_seconds or 0.0
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
    result = run_pipeline(settings, manifest.project_name)
    console.print(f"FCPXML written to: {result['xml_path']}")


@cli.command()
@click.pass_context
def cost(ctx):
    """Show estimated token count and API cost for the current ingest dir."""
    from directorcut.ingest import ingest as do_ingest

    settings = ctx.obj["settings"]
    manifest = do_ingest(settings.ingest_dir)
    estimate = estimate_cost(manifest.duration_seconds or 0.0)
    console.print(f"Estimated cost: ${estimate:.4f}")


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
