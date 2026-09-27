"""Natural-language blueprint patches: `directorcut cut patch` (ticket 3).

Fabricated project dirs only; the model call is dev-stubbed or the
GeminiClient patched outright - no outbound traffic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from directorcut import cutops
from directorcut.cli import cli
from directorcut.cutops import (
    CutOpsError,
    PatchOp,
    PatchPlan,
    apply_ops,
    apply_patch,
    interpret_patch,
    preview_patch,
)
from directorcut.models import (
    AssemblyBlueprint,
    Asset,
    AssetRole,
    Cut,
    IngestManifest,
    TimelineMeta,
)
from directorcut.utils import format_timecode, parse_timecode

A = Path("C:/media/aroll.mp4")
TAKE_B = Path("C:/media/take_b.mp4")
BROLL = Path("C:/media/broll.mp4")


def v1(src: Path, src_in: float, dur: float, tl_in: float) -> Cut:
    return Cut(
        track="V1_A1_PRIMARY",
        source_file=src,
        timeline_in=format_timecode(tl_in),
        timeline_out=format_timecode(tl_in + dur),
        source_in=format_timecode(src_in),
        source_out=format_timecode(src_in + dur),
    )


def ovl(tl_in: float, tl_out: float) -> Cut:
    return Cut(
        track="V2_OVERLAY_BROLL",
        source_file=BROLL,
        timeline_in=format_timecode(tl_in),
        timeline_out=format_timecode(tl_out),
        source_in=format_timecode(0.0),
        source_out=format_timecode(tl_out - tl_in),
    )


def make_project(tmp_path: Path, with_assets: bool = True) -> Path:
    """Spine A[0-10], A[20-30], A[40-50]; overlay anchored to V1 #1."""
    proj = tmp_path / "proj"
    proj.mkdir()
    bp = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="proj"),
        cuts=[
            v1(A, 0.0, 10.0, 0.0),
            v1(A, 20.0, 10.0, 10.0),
            v1(A, 40.0, 10.0, 20.0),
            ovl(2.0, 8.0),
        ],
    )
    (proj / "blueprint.json").write_text(
        bp.model_dump_json(indent=2), encoding="utf-8"
    )
    if with_assets:
        manifest = IngestManifest(
            project_name="proj",
            assets=[
                Asset(path=A, role=AssetRole.A_ROLL, duration_seconds=120.0),
                Asset(path=TAKE_B, role=AssetRole.A_ROLL, duration_seconds=90.0),
                Asset(path=BROLL, role=AssetRole.B_ROLL, duration_seconds=30.0),
                # A non-media inventory entry for the "can't sit on V1" path.
                Asset(path=Path("C:/media/notes.txt"), role=AssetRole.SCRIPT),
            ],
        )
        (proj / "assets.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
    return proj


def _fcpx_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DIRECTORCUT_ENV", "dev")
    monkeypatch.setenv("DIRECTORCUT_FCPXML_TARGET", "fcpx")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))


def _v1s(bp: AssemblyBlueprint) -> list[Cut]:
    return sorted(
        (c for c in bp.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )


def _load_bp(proj: Path) -> AssemblyBlueprint:
    return AssemblyBlueprint.model_validate(
        json.loads((proj / "blueprint.json").read_text(encoding="utf-8"))
    )


# --- schema + validation ----------------------------------------------------


def test_patchop_schema_rejects_bad_shapes():
    with pytest.raises(ValidationError):
        PatchOp(op="drop_cut", index=0)  # 1-based only
    with pytest.raises(ValidationError):
        PatchOp(op="nuke_everything", index=1)  # not an allowed op
    with pytest.raises(ValidationError):
        PatchOp(op="move_cut", index=1, to_index=0)


def test_apply_ops_validates_against_current_state(tmp_path):
    proj = make_project(tmp_path)
    bp = _load_bp(proj)
    with pytest.raises(CutOpsError, match="out of range"):
        apply_ops(bp, [PatchOp(op="drop_cut", index=9)])
    with pytest.raises(CutOpsError, match="requires to_index"):
        apply_ops(bp, [PatchOp(op="move_cut", index=1)])
    with pytest.raises(CutOpsError, match="requires source_file"):
        apply_ops(bp, [PatchOp(op="swap_source", index=1)])
    with pytest.raises(CutOpsError, match="bad timecode"):
        apply_ops(bp, [PatchOp(op="move_overlay", index=1, to_timecode="soon")])


def test_swap_source_rejects_unknown_and_too_short(tmp_path):
    proj = make_project(tmp_path)
    bp = _load_bp(proj)
    assets = cutops._load_assets(proj)
    with pytest.raises(CutOpsError, match="asset inventory"):
        apply_ops(
            bp,
            [PatchOp(op="swap_source", index=1, source_file="C:/media/nope.mp4")],
            assets,
        )
    with pytest.raises(CutOpsError, match="too short"):
        apply_ops(
            bp,
            # window ends at src 50s; BROLL is only 30s long
            [PatchOp(op="swap_source", index=3, source_file=str(BROLL))],
            assets,
        )
    with pytest.raises(CutOpsError, match="can't sit on V1"):
        apply_ops(
            bp,
            # In the inventory, but a .txt can't take a V1 slot.
            [PatchOp(op="swap_source", index=1, source_file="C:/media/notes.txt")],
            assets,
        )


# --- op semantics -----------------------------------------------------------


def test_move_cut_relays_spine_and_shifts_anchored_overlays(tmp_path):
    proj = make_project(tmp_path)
    bp = _load_bp(proj)
    new_bp, lines = apply_ops(
        bp, [PatchOp(op="move_cut", index=1, to_index=3)], cutops._load_assets(proj)
    )
    assert lines and "move cut #1" in lines[0]
    spine = _v1s(new_bp)
    # New order: A[20-30], A[40-50], A[0-10] - gapless from zero.
    assert [c.source_in for c in spine] == [
        "00:00:20.000",
        "00:00:40.000",
        "00:00:00.000",
    ]
    assert spine[0].timeline_in == "00:00:00.000"
    assert spine[2].timeline_in == "00:00:20.000"
    # The overlay was anchored to the moved cut (its tl_in 2s sat inside
    # cut1's 0-10); it rides along to the cut's new 20-30 slot.
    overlay = next(c for c in new_bp.cuts if c.track == "V2_OVERLAY_BROLL")
    assert overlay.timeline_in == "00:00:22.000"
    assert overlay.timeline_out == "00:00:28.000"


def test_swap_source_keeps_window(tmp_path):
    proj = make_project(tmp_path)
    bp = _load_bp(proj)
    new_bp, _ = apply_ops(
        bp,
        [PatchOp(op="swap_source", index=2, source_file=str(TAKE_B))],
        cutops._load_assets(proj),
    )
    target = _v1s(new_bp)[1]
    assert target.source_file == TAKE_B
    assert target.source_in == "00:00:20.000"
    assert target.source_out == "00:00:30.000"


def test_move_and_drop_overlay(tmp_path):
    proj = make_project(tmp_path)
    bp = _load_bp(proj)
    moved, _ = apply_ops(
        bp, [PatchOp(op="move_overlay", index=1, to_timecode="00:00:15.000")]
    )
    overlay = next(c for c in moved.cuts if c.track == "V2_OVERLAY_BROLL")
    assert overlay.timeline_in == "00:00:15.000"
    assert overlay.timeline_out == "00:00:21.000"  # duration preserved

    dropped, _ = apply_ops(bp, [PatchOp(op="drop_overlay", index=1)])
    assert not [c for c in dropped.cuts if c.track == "V2_OVERLAY_BROLL"]


def test_preview_writes_nothing_apply_snapshots_and_undo(tmp_path, monkeypatch):
    _fcpx_settings(monkeypatch, tmp_path)
    proj = make_project(tmp_path)
    original = _load_bp(proj)
    ops = [PatchOp(op="drop_cut", index=2)]

    preview_bp, lines = preview_patch(proj, ops)
    assert len(_v1s(preview_bp)) == 2
    assert lines
    # Preview touches nothing.
    assert _load_bp(proj) == original
    assert not (proj / "blueprint.prev.json").exists()

    applied, _ = apply_patch(proj, ops)
    assert len(_v1s(applied)) == 2
    assert (proj / "blueprint.prev.json").exists()
    assert (proj / "proj.fcpxml").exists()

    cutops.undo(proj)
    assert _load_bp(proj) == original


# --- natural-language interpretation ---------------------------------------


def test_interpret_patch_dev_stub_parses_simple_ops(tmp_path, monkeypatch):
    _fcpx_settings(monkeypatch, tmp_path)
    proj = make_project(tmp_path)
    from directorcut.config import Settings

    settings = Settings(_env_file=None, env="dev")
    plan = interpret_patch("drop cut 2 and move cut 1 to 3", proj, settings)
    kinds = [(o.op, o.index, o.to_index) for o in plan.ops]
    assert ("drop_cut", 2, None) in kinds
    assert ("move_cut", 1, 3) in kinds


def test_interpret_patch_uses_fake_client_and_fast_task(tmp_path, monkeypatch):
    """NL->ops mapping goes through GeminiClient.call(task='interpret')."""
    proj = make_project(tmp_path)
    seen = {}

    def fake_call(self, prompt, media, response_model, **kw):
        seen["task"] = kw.get("task")
        seen["prompt"] = prompt
        return PatchPlan(
            ops=[PatchOp(op="drop_cut", index=3)],
            rationale="test",
        )

    monkeypatch.setattr(
        "directorcut.gemini_client.GeminiClient.call", fake_call
    )
    from directorcut.config import Settings

    plan = interpret_patch(
        "nuke the last cut",
        proj,
        Settings(_env_file=None, env="prod", gemini_backend="api"),
    )
    assert seen["task"] == "interpret"
    # The prompt carries the numbered listing + asset inventory.
    assert "#1" in seen["prompt"] and str(A) in seen["prompt"]
    assert plan.ops == [PatchOp(op="drop_cut", index=3)]


def test_interpret_patch_empty_ops_errors(tmp_path, monkeypatch):
    proj = make_project(tmp_path)
    monkeypatch.setattr(
        "directorcut.gemini_client.GeminiClient.call",
        lambda self, *a, **k: PatchPlan(ops=[], rationale="huh?"),
    )
    from directorcut.config import Settings

    with pytest.raises(CutOpsError, match="Could not map"):
        interpret_patch("asdfgh", proj, Settings(_env_file=None, env="dev"))


# --- CLI ---------------------------------------------------------------------


def test_cli_patch_dry_run_apply_and_undo(tmp_path, monkeypatch):
    _fcpx_settings(monkeypatch, tmp_path)
    make_project(tmp_path)
    runner = CliRunner()

    res = runner.invoke(cli, ["cut", "patch", "proj", "drop cut 2", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "drop cut #2" in res.output
    assert "dry-run" in res.output
    # Nothing applied, no snapshot.
    assert len(_v1s(_load_bp(tmp_path / "proj"))) == 3
    assert not (tmp_path / "proj" / "blueprint.prev.json").exists()

    res = runner.invoke(cli, ["cut", "patch", "proj", "drop cut 2"])
    assert res.exit_code == 0, res.output
    assert "Undo:" in res.output
    assert len(_v1s(_load_bp(tmp_path / "proj"))) == 2

    res = runner.invoke(cli, ["cut", "undo", "proj"])
    assert res.exit_code == 0, res.output
    assert len(_v1s(_load_bp(tmp_path / "proj"))) == 3


def test_cli_patch_bad_instruction_surfaces_error(tmp_path, monkeypatch):
    _fcpx_settings(monkeypatch, tmp_path)
    make_project(tmp_path)
    # Dev stub can't parse this -> empty plan -> clean error, not a traceback.
    res = CliRunner().invoke(cli, ["cut", "patch", "proj", "zzz nothing"])
    assert res.exit_code != 0
    assert "Could not map" in res.output


def test_cli_patch_out_of_range_op_surfaces_error(tmp_path, monkeypatch):
    _fcpx_settings(monkeypatch, tmp_path)
    make_project(tmp_path)
    res = CliRunner().invoke(cli, ["cut", "patch", "proj", "drop cut 99"])
    assert res.exit_code != 0
    assert "out of range" in res.output
