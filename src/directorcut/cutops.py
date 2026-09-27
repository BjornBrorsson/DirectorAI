"""Post-assembly blueprint patch commands: list / drop / undo V1 cuts.

Productized from `tasks/multi-aroll-api-run/drop_cuts.py` (the script that
fixed the StS2 EA-review timeline after import). Same semantics:

- `list` numbers the V1 spine 1-based in timeline order; that number is
  the drop index.
- `drop` removes V1 cuts, recompacts the spine gaplessly via
  `compact_timeline` (connected overlays anchored to a dropped V1 go with
  it, everything else shifts), writes blueprint.json, then re-runs the
  finalize step - Resolve targets re-render the ducked music/SFX beds.
- Every mutation snapshots `blueprint.json` to `blueprint.prev.json`
  FIRST, so `undo` restores the pre-patch state. Snapshots swap, so undo
  is its own undo (toggle between the last two states).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from directorcut.assembly.director import compact_timeline
from directorcut.config import Settings
from directorcut.finalize import finalize_project
from directorcut.ingest import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
from directorcut.log import get_logger
from directorcut.models import (
    AssemblyBlueprint,
    Asset,
    Cut,
    IngestManifest,
    NarrativeNode,
)
from directorcut.utils import format_timecode, parse_timecode

logger = get_logger(__name__)

BLUEPRINT_NAME = "blueprint.json"
SNAPSHOT_NAME = "blueprint.prev.json"
NARRATIVE_NAME = "narrative.json"
ASSETS_NAME = "assets.json"


class CutOpsError(RuntimeError):
    """Raised for bad cut-op input (bad indices, missing snapshot)."""


@dataclass(frozen=True)
class CutRow:
    """One row of `cut list` output."""

    index: int | None  # 1-based V1 spine index; None for non-V1 tracks
    track: str
    timeline_in: str
    timeline_out: str
    source_in: str
    source_out: str
    source_file: Path
    text: str = ""  # spoken text covering the range (when narrative.json exists)


def _blueprint_path(project_dir: Path) -> Path:
    return project_dir / BLUEPRINT_NAME


def _load_blueprint(project_dir: Path) -> AssemblyBlueprint:
    path = _blueprint_path(project_dir)
    if not path.exists():
        raise CutOpsError(f"no blueprint.json in {project_dir} - run the pipeline first")
    try:
        return AssemblyBlueprint.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CutOpsError(f"unreadable blueprint.json in {project_dir}: {exc}") from exc


def _write_blueprint(project_dir: Path, blueprint: AssemblyBlueprint) -> None:
    _blueprint_path(project_dir).write_text(
        blueprint.model_dump_json(indent=2), encoding="utf-8"
    )


def _load_nodes(project_dir: Path) -> list[NarrativeNode]:
    """narrative.json if present - powers the spoken-text preview column."""
    path = project_dir / NARRATIVE_NAME
    if not path.exists():
        return []
    try:
        return [
            NarrativeNode.model_validate(n)
            for n in json.loads(path.read_text(encoding="utf-8"))
        ]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Ignoring unreadable %s: %s", path, exc)
        return []


def _spoken_for(cut, nodes: list[NarrativeNode]) -> str:
    """Spoken text whose node range overlaps the cut's source range."""
    if not nodes:
        return ""
    in_s = parse_timecode(cut.source_in)
    out_s = parse_timecode(cut.source_out)
    # Small tolerance: word-air padding may extend the cut slightly past a
    # node's nominal end.
    tol = 0.15
    parts = [
        n.spoken_text
        for n in nodes
        if n.source_file == cut.source_file
        and parse_timecode(n.time_start) >= in_s - tol
        and parse_timecode(n.time_end) <= out_s + tol
    ]
    return " ".join(parts)


def _v1_sorted(blueprint: AssemblyBlueprint) -> list:
    return sorted(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )


def list_cuts(project_dir: Path) -> list[CutRow]:
    """All blueprint cuts; V1 rows carry the 1-based index used by `drop`."""
    blueprint = _load_blueprint(project_dir)
    nodes = _load_nodes(project_dir)
    v1_index = {id(c): i + 1 for i, c in enumerate(_v1_sorted(blueprint))}
    rows: list[CutRow] = []
    for c in sorted(
        blueprint.cuts,
        key=lambda c: (c.track != "V1_A1_PRIMARY", parse_timecode(c.timeline_in)),
    ):
        rows.append(
            CutRow(
                index=v1_index.get(id(c)),
                track=c.track,
                timeline_in=c.timeline_in,
                timeline_out=c.timeline_out,
                source_in=c.source_in,
                source_out=c.source_out,
                source_file=c.source_file,
                text=_spoken_for(c, nodes) if c.track == "V1_A1_PRIMARY" else "",
            )
        )
    return rows


def _snapshot(project_dir: Path) -> None:
    """Copy blueprint.json -> blueprint.prev.json (pre-mutation state)."""
    src = _blueprint_path(project_dir)
    (project_dir / SNAPSHOT_NAME).write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )


def drop_cuts(
    project_dir: Path,
    indices: set[int] | list[int],
    settings: Settings | None = None,
) -> tuple[AssemblyBlueprint, list[Cut]]:
    """Drop V1 cuts by 1-based spine index, recompact, re-finalize.

    Returns (new blueprint, dropped cuts). Raises CutOpsError on bad input.
    """
    blueprint = _load_blueprint(project_dir)
    v1 = _v1_sorted(blueprint)
    idx = sorted({int(i) for i in indices})
    if not idx:
        raise CutOpsError("no indices given")
    bad = [i for i in idx if i < 1 or i > len(v1)]
    if bad:
        raise CutOpsError(
            f"index out of range: {bad} (spine has {len(v1)} V1 cut(s))"
        )
    dropped = [v1[i - 1] for i in idx]

    _snapshot(project_dir)
    new_cuts = compact_timeline(list(blueprint.cuts), dropped)
    blueprint = blueprint.model_copy(update={"cuts": new_cuts})
    _write_blueprint(project_dir, blueprint)
    logger.info(
        "Dropped %d V1 cut(s); spine now %d cut(s)", len(dropped),
        sum(1 for c in new_cuts if c.track == "V1_A1_PRIMARY"),
    )
    finalize_project(project_dir, settings)
    return blueprint, dropped


def undo(project_dir: Path, settings: Settings | None = None) -> AssemblyBlueprint:
    """Restore the pre-mutation snapshot. Swap semantics: undoing twice
    toggles back to the dropped state, so a mistaken undo is recoverable."""
    snap = project_dir / SNAPSHOT_NAME
    bp_path = _blueprint_path(project_dir)
    if not snap.exists():
        raise CutOpsError(
            f"nothing to undo - no {SNAPSHOT_NAME} in {project_dir}"
        )
    tmp = project_dir / "blueprint.undo-tmp.json"
    bp_path.replace(tmp)
    snap.replace(bp_path)
    tmp.replace(snap)
    blueprint = _load_blueprint(project_dir)
    logger.info("Restored %s", snap.name)
    finalize_project(project_dir, settings)
    return blueprint


def format_rows(rows: list[CutRow]) -> list[str]:
    """Human-readable `cut list` lines."""
    lines = []
    for r in rows:
        tag = f"#{r.index:<3}" if r.index is not None else "    "
        text = f"  {r.text[:70]}" if r.text else ""
        lines.append(
            f"{tag} {r.track:<16} tl {r.timeline_in}-{r.timeline_out}  "
            f"src {r.source_in}-{r.source_out}  {r.source_file.name}{text}"
        )
    return lines


# ---------------------------------------------------------------------------
# Natural-language patch ops (interactive re-cut)
#
# Five constrained ops, all validated against the CURRENT blueprint state:
# indices always refer to `cut list` numbering at the moment the op applies
# (V1 spine index for drop_cut/move_cut/swap_source, V2 overlay order for
# move_overlay/drop_overlay), so ops inside one plan compose sequentially.
# Every apply snapshots blueprint.json -> blueprint.prev.json first, so the
# existing swap-semantics `undo` covers patches for free.
# ---------------------------------------------------------------------------

# V1-compatible file kinds - mirrors director._TRACK_EXTS["V1_A1_PRIMARY"].
_V1_EXTS = VIDEO_EXTS | IMAGE_EXTS | AUDIO_EXTS

PATCH_PROMPT = """You translate an editor's natural-language instruction into
blueprint patch ops for a video rough-cut timeline.

Allowed ops (emit a JSON object {"ops": [...], "rationale": "<one sentence>"}):
- {"op": "drop_cut", "index": N} - remove V1 spine cut #N entirely.
- {"op": "move_cut", "index": N, "to_index": M} - move V1 cut #N so it
  becomes the M-th spine cut.
- {"op": "swap_source", "index": N, "source_file": "<path>"} - replace the
  source file of V1 cut #N with a file from the inventory, keeping the
  same source_in/source_out window (use for "take B instead" requests).
- {"op": "move_overlay", "index": N, "to_timecode": "HH:MM:SS.mmm"} - move
  V2 overlay cut #N (overlays numbered 1-based in timeline order) to start
  at the given timeline position, preserving its duration.
- {"op": "drop_overlay", "index": N} - remove V2 overlay #N.

Indices are 1-based and refer to the #N labels on V1 rows below / overlay
numbering. If the instruction is ambiguous, emit the smallest reasonable
op set - never invent indices beyond the listing or files beyond the
inventory. Reply with ONLY the JSON object.

Current timeline:
{listing}

Available source files:
{sources}

Instruction: {instruction}
"""


class PatchOp(BaseModel):
    """One constrained blueprint edit produced by the interpret call."""

    op: Literal["drop_cut", "move_cut", "swap_source", "move_overlay", "drop_overlay"]
    # 1-based index: V1 spine number for drop/move/swap, V2 overlay number
    # (timeline order) for move_overlay/drop_overlay.
    index: int = Field(..., ge=1)
    to_index: int | None = Field(default=None, ge=1)
    source_file: str | None = None
    to_timecode: str | None = None


class PatchPlan(BaseModel):
    """Model output: the op list plus a one-line rationale for the preview."""

    ops: list[PatchOp] = []
    rationale: str = ""


def _load_assets(project_dir: Path) -> list[Asset]:
    """assets.json (the post-interpretation manifest) - the legal swap pool."""
    path = project_dir / ASSETS_NAME
    if not path.exists():
        return []
    try:
        return IngestManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        ).assets
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable %s: %s", path, exc)
        return []


def _v2_sorted(blueprint_or_cuts) -> list[Cut]:
    cuts = (
        blueprint_or_cuts.cuts
        if isinstance(blueprint_or_cuts, AssemblyBlueprint)
        else blueprint_or_cuts
    )
    return sorted(
        (c for c in cuts if c.track == "V2_OVERLAY_BROLL"),
        key=lambda c: parse_timecode(c.timeline_in),
    )


def _relayout(cuts: list[Cut], v1_ordered: list[Cut]) -> list[Cut]:
    """Re-lay the spine in `v1_ordered` order, contiguous from zero.

    Permutation sibling of `compact_timeline`: nothing is deleted, so every
    non-V1 cut keeps its anchor (the V1 cut whose ORIGINAL range contained
    its timeline_in) and is re-expressed at that anchor's NEW position plus
    its old local offset. Cuts anchored to nothing keep their position -
    the conformer gap-anchors them, same as always.
    """
    old_v1 = sorted(
        (c for c in cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    old_pos = {
        id(c): (parse_timecode(c.timeline_in), parse_timecode(c.timeline_out))
        for c in old_v1
    }

    new_pos: dict[int, tuple[float, float]] = {}
    cursor = 0.0
    for cut in v1_ordered:
        span = parse_timecode(cut.source_out) - parse_timecode(cut.source_in)
        new_pos[id(cut)] = (cursor, cursor + span)
        cursor += span
    timeline_end = cursor

    out: list[Cut] = []
    for cut in v1_ordered:
        ni, no = new_pos[id(cut)]
        out.append(
            cut.model_copy(
                update={
                    "timeline_in": format_timecode(ni),
                    "timeline_out": format_timecode(no),
                }
            )
        )
    for cut in cuts:
        if cut.track == "V1_A1_PRIMARY":
            continue
        tl_in = parse_timecode(cut.timeline_in)
        tl_out = parse_timecode(cut.timeline_out)
        anchor = next(
            (c for c in old_v1 if old_pos[id(c)][0] <= tl_in < old_pos[id(c)][1]),
            None,
        )
        if anchor is None:
            out.append(cut)
            continue
        ni = new_pos[id(anchor)][0] + (tl_in - old_pos[id(anchor)][0])
        no = min(ni + (tl_out - tl_in), timeline_end)
        if ni >= timeline_end or no - ni <= 0:
            logger.warning(
                "Dropping %s cut on %s - fell past the relaid timeline end",
                cut.track,
                cut.source_file.name,
            )
            continue
        out.append(
            cut.model_copy(
                update={
                    "timeline_in": format_timecode(ni),
                    "timeline_out": format_timecode(no),
                }
            )
        )
    return out


def _resolve_asset(raw: str, assets: list[Asset]) -> Asset | None:
    """Match a model-supplied source_file onto the asset inventory."""
    src = Path(raw)
    for asset in assets:
        if asset.path == src or asset.path.name == src.name:
            return asset
    try:
        resolved = src.resolve()
    except OSError:
        return None
    for asset in assets:
        if asset.path == resolved:
            return asset
    return None


def _describe(cut: Cut) -> str:
    return (
        f"tl {cut.timeline_in}-{cut.timeline_out} "
        f"src {cut.source_in}-{cut.source_out} {cut.source_file.name}"
    )


def _apply_op(
    cuts: list[Cut], op: PatchOp, assets: list[Asset] | None
) -> tuple[list[Cut], str]:
    """Apply one op to the current cut list; returns (cuts, diff line)."""
    if op.op == "drop_cut":
        v1 = sorted(
            (c for c in cuts if c.track == "V1_A1_PRIMARY"),
            key=lambda c: parse_timecode(c.timeline_in),
        )
        if op.index > len(v1):
            raise CutOpsError(
                f"drop_cut: index {op.index} out of range ({len(v1)} V1 cut(s))"
            )
        target = v1[op.index - 1]
        return compact_timeline(cuts, [target]), f"drop cut #{op.index} ({_describe(target)})"

    if op.op == "move_cut":
        if op.to_index is None:
            raise CutOpsError("move_cut requires to_index")
        v1 = sorted(
            (c for c in cuts if c.track == "V1_A1_PRIMARY"),
            key=lambda c: parse_timecode(c.timeline_in),
        )
        if op.index > len(v1) or op.to_index > len(v1):
            raise CutOpsError(
                f"move_cut: index {op.index} -> {op.to_index} out of range "
                f"({len(v1)} V1 cut(s))"
            )
        ordered = list(v1)
        moved = ordered.pop(op.index - 1)
        ordered.insert(op.to_index - 1, moved)
        return _relayout(cuts, ordered), (
            f"move cut #{op.index} -> position {op.to_index} ({_describe(moved)})"
        )

    if op.op == "swap_source":
        if not op.source_file:
            raise CutOpsError("swap_source requires source_file")
        v1 = sorted(
            (c for c in cuts if c.track == "V1_A1_PRIMARY"),
            key=lambda c: parse_timecode(c.timeline_in),
        )
        if op.index > len(v1):
            raise CutOpsError(
                f"swap_source: index {op.index} out of range ({len(v1)} V1 cut(s))"
            )
        target = v1[op.index - 1]
        if assets:
            asset = _resolve_asset(op.source_file, assets)
            if asset is None:
                raise CutOpsError(
                    f"swap_source: {op.source_file!r} is not in the project's "
                    "asset inventory (assets.json)"
                )
            new_src = asset.path
            need = parse_timecode(target.source_out) - parse_timecode(target.source_in)
            if asset.duration_seconds is not None and asset.duration_seconds < parse_timecode(
                target.source_out
            ):
                raise CutOpsError(
                    f"swap_source: {new_src.name} is {asset.duration_seconds:.1f}s - "
                    f"too short for the {need:.1f}s window ending at {target.source_out}"
                )
        else:
            new_src = Path(op.source_file)
        if new_src.suffix.lower() not in _V1_EXTS:
            raise CutOpsError(
                f"swap_source: {new_src.name} can't sit on V1 (extension not in "
                f"{sorted(_V1_EXTS)})"
            )
        out = [
            target.model_copy(update={"source_file": new_src}) if c is target else c
            for c in cuts
        ]
        return out, (
            f"swap source on #{op.index}: {target.source_file.name} -> {new_src.name}"
        )

    if op.op == "move_overlay":
        if not op.to_timecode:
            raise CutOpsError("move_overlay requires to_timecode")
        try:
            new_in = parse_timecode(op.to_timecode)
        except (ValueError, AttributeError) as exc:
            raise CutOpsError(
                f"move_overlay: bad timecode {op.to_timecode!r} (want HH:MM:SS.mmm)"
            ) from exc
        v2 = _v2_sorted(cuts)
        if op.index > len(v2):
            raise CutOpsError(
                f"move_overlay: index {op.index} out of range ({len(v2)} overlay(s))"
            )
        target = v2[op.index - 1]
        dur = parse_timecode(target.timeline_out) - parse_timecode(target.timeline_in)
        out = [
            target.model_copy(
                update={
                    "timeline_in": format_timecode(new_in),
                    "timeline_out": format_timecode(new_in + dur),
                }
            )
            if c is target
            else c
            for c in cuts
        ]
        return out, (
            f"move overlay #{op.index} ({target.source_file.name}) -> "
            f"{format_timecode(new_in)}"
        )

    if op.op == "drop_overlay":
        v2 = _v2_sorted(cuts)
        if op.index > len(v2):
            raise CutOpsError(
                f"drop_overlay: index {op.index} out of range ({len(v2)} overlay(s))"
            )
        target = v2[op.index - 1]
        return [c for c in cuts if c is not target], (
            f"drop overlay #{op.index} ({_describe(target)})"
        )

    raise CutOpsError(f"unknown patch op: {op.op!r}")


def apply_ops(
    blueprint: AssemblyBlueprint,
    ops: list[PatchOp],
    assets: list[Asset] | None = None,
) -> tuple[AssemblyBlueprint, list[str]]:
    """Validate + apply a patch plan; returns (new blueprint, diff lines).

    Ops run sequentially against the evolving cut list, so later op indices
    see the post-op numbering. Nothing is written - preview_patch and
    apply_patch decide what to do with the result.
    """
    cuts = list(blueprint.cuts)
    lines: list[str] = []
    for op in ops:
        cuts, line = _apply_op(cuts, op, assets)
        lines.append(line)
    return blueprint.model_copy(update={"cuts": cuts}), lines


def preview_patch(
    project_dir: Path, ops: list[PatchOp]
) -> tuple[AssemblyBlueprint, list[str]]:
    """Dry-run a patch plan: returns the resulting blueprint + diff lines
    without touching disk."""
    blueprint = _load_blueprint(project_dir)
    return apply_ops(blueprint, ops, _load_assets(project_dir))


def apply_patch(
    project_dir: Path,
    ops: list[PatchOp],
    settings: Settings | None = None,
) -> tuple[AssemblyBlueprint, list[str]]:
    """Apply a patch plan for real: snapshot -> write -> re-finalize."""
    blueprint = _load_blueprint(project_dir)
    new_blueprint, lines = apply_ops(blueprint, ops, _load_assets(project_dir))
    _snapshot(project_dir)
    _write_blueprint(project_dir, new_blueprint)
    finalize_project(project_dir, settings)
    return new_blueprint, lines


def _fake_patch(instruction: str) -> dict:
    """Dev-mode stub: parse the simplest phrasings so dev runs exercise the
    full patch path without a model call."""
    ops: list[dict] = []
    for m in re.finditer(r"drop\s+(?:cut\s+)?#?(\d+)", instruction, re.IGNORECASE):
        ops.append({"op": "drop_cut", "index": int(m.group(1))})
    for m in re.finditer(
        r"move\s+(?:cut\s+)?#?(\d+)\s+to\s+(?:position\s+)?#?(\d+)",
        instruction,
        re.IGNORECASE,
    ):
        ops.append(
            {"op": "move_cut", "index": int(m.group(1)), "to_index": int(m.group(2))}
        )
    return {"ops": ops, "rationale": "dev stub parse"}


def interpret_patch(
    instruction: str, project_dir: Path, settings: Settings
) -> PatchPlan:
    """One small model call: natural language -> validated PatchPlan.

    Uses the 'interpret' task routing so it lands on the fast/cheap model
    chain. In dev/test mode the client's stub path returns `_fake_patch`'s
    regex parse instead.
    """
    from directorcut.gemini_client import GeminiClient

    listing = "\n".join(format_rows(list_cuts(project_dir))) or "(empty timeline)"
    sources = "\n".join(str(a.path) for a in _load_assets(project_dir)) or "(none)"
    # The template carries literal JSON braces, so placeholders are
    # substituted by replace() rather than str.format.
    prompt = (
        PATCH_PROMPT.replace("{listing}", listing)
        .replace("{sources}", sources)
        .replace("{instruction}", instruction)
    )
    plan = GeminiClient(settings).call(
        prompt=prompt,
        media=None,
        response_model=PatchPlan,
        dev_mode_override=_fake_patch(instruction),
        task="interpret",
    )
    if not plan.ops:
        raise CutOpsError(
            f"Could not map the instruction to any patch ops: {instruction!r}"
        )
    return plan
