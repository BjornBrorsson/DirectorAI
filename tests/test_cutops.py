"""Post-assembly `directorcut cut` patch commands (ticket 7).

Fabricated project dirs only - blueprint.json + narrative.json fixtures,
fcpxml_target=fcpx so finalize never shells out to the mixdown ffmpeg.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from directorcut import cutops
from directorcut.cli import cli
from directorcut.cutops import CutOpsError
from directorcut.models import AssemblyBlueprint, Cut, TimelineMeta
from directorcut.utils import format_timecode, parse_timecode

# Absolute paths - the conformer refuses relative source_files.
A = Path("C:/media/aroll.mp4")
BROLL = Path("C:/media/broll.mp4")
MUSIC = Path("C:/media/track.mp3")


def v1(src_in: float, dur: float, tl_in: float) -> Cut:
    return Cut(
        track="V1_A1_PRIMARY",
        source_file=A,
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


def music(tl_in: float, tl_out: float) -> Cut:
    return Cut(
        track="A2_MUSIC",
        source_file=MUSIC,
        timeline_in=format_timecode(tl_in),
        timeline_out=format_timecode(tl_out),
        source_in=format_timecode(0.0),
        source_out=format_timecode(tl_out - tl_in),
    )


def make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    cuts = [
        v1(0.0, 10.0, 0.0),  # V1 #1  tl 0-10
        v1(20.0, 10.0, 10.0),  # V1 #2  tl 10-20
        v1(40.0, 10.0, 20.0),  # V1 #3  tl 20-30
        ovl(2.0, 8.0),  # anchored to V1 #1 - survives a #2 drop
        ovl(12.0, 18.0),  # anchored to V1 #2 - dropped with it
        music(0.0, 30.0),
    ]
    bp = AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="proj"), cuts=cuts
    )
    (proj / "blueprint.json").write_text(
        bp.model_dump_json(indent=2), encoding="utf-8"
    )
    nodes = [
        {
            "node_id": "n1",
            "time_start": "00:00:00.000",
            "time_end": "00:00:10.000",
            "spoken_text": "hello world",
            "thematic_tag": "EXPOSITION",
            "energy_level": "LOW",
            "source_file": str(A),
        },
        {
            "node_id": "n2",
            "time_start": "00:00:20.000",
            "time_end": "00:00:30.000",
            "spoken_text": "second take",
            "thematic_tag": "EXPOSITION",
            "energy_level": "LOW",
            "source_file": str(A),
        },
    ]
    (proj / "narrative.json").write_text(json.dumps(nodes), encoding="utf-8")
    return proj


def _fcpx_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # fcpx target -> no mixdown subprocess; OUTPUT_DIR points at tmp_path.
    monkeypatch.setenv("DIRECTORCUT_FCPXML_TARGET", "fcpx")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))


def _load_bp(proj: Path) -> AssemblyBlueprint:
    return AssemblyBlueprint.model_validate(
        json.loads((proj / "blueprint.json").read_text(encoding="utf-8"))
    )


def test_list_numbers_only_v1(tmp_path: Path) -> None:
    proj = make_project(tmp_path)
    rows = cutops.list_cuts(proj)
    v1_rows = [r for r in rows if r.index is not None]
    assert [r.index for r in v1_rows] == [1, 2, 3]
    assert v1_rows[1].text == "second take"
    others = [r for r in rows if r.index is None]
    assert len(others) == 3  # 2 overlays + music


def test_drop_recompacts_and_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fcpx_settings(monkeypatch, tmp_path)
    proj = make_project(tmp_path)
    blueprint, dropped = cutops.drop_cuts(proj, {2})

    assert len(dropped) == 1
    assert dropped[0].source_in == "00:00:20.000"
    # Snapshot holds the pre-drop blueprint.
    snap = AssemblyBlueprint.model_validate(
        json.loads((proj / "blueprint.prev.json").read_text(encoding="utf-8"))
    )
    assert len([c for c in snap.cuts if c.track == "V1_A1_PRIMARY"]) == 3

    v1s = sorted(
        (c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"),
        key=lambda c: parse_timecode(c.timeline_in),
    )
    assert len(v1s) == 2
    # Gapless re-lay: cut 3 takes the dropped cut's slot.
    assert v1s[1].timeline_in == "00:00:10.000"
    assert v1s[1].source_in == "00:00:40.000"
    # Overlay anchored to the dropped cut is gone; the other survives.
    assert sum(c.track == "V2_OVERLAY_BROLL" for c in blueprint.cuts) == 1
    # FCPXML was regenerated.
    assert (proj / "proj.fcpxml").exists()
    # blueprint.json matches the returned model.
    assert _load_bp(proj) == blueprint


def test_undo_restores_and_toggles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fcpx_settings(monkeypatch, tmp_path)
    proj = make_project(tmp_path)
    original = _load_bp(proj)

    cutops.drop_cuts(proj, {2})
    cutops.undo(proj)
    assert _load_bp(proj) == original

    # Undo again -> back to the dropped state (swap semantics).
    cutops.undo(proj)
    assert len(
        [c for c in _load_bp(proj).cuts if c.track == "V1_A1_PRIMARY"]
    ) == 2


def test_drop_rejects_bad_indices(tmp_path: Path) -> None:
    proj = make_project(tmp_path)
    with pytest.raises(CutOpsError, match="out of range"):
        cutops.drop_cuts(proj, {9})
    with pytest.raises(CutOpsError, match="no indices"):
        cutops.drop_cuts(proj, set())


def test_undo_without_snapshot_errors(tmp_path: Path) -> None:
    proj = make_project(tmp_path)
    with pytest.raises(CutOpsError, match="nothing to undo"):
        cutops.undo(proj)


def test_cli_drop_list_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fcpx_settings(monkeypatch, tmp_path)
    make_project(tmp_path)
    runner = CliRunner()

    res = runner.invoke(cli, ["cut", "list", "proj"])
    assert res.exit_code == 0, res.output
    assert "#1" in res.output and "#3" in res.output
    assert "second take" in res.output

    res = runner.invoke(cli, ["cut", "drop", "proj", "2"])
    assert res.exit_code == 0, res.output
    assert "Undo:" in res.output

    res = runner.invoke(cli, ["cut", "undo", "proj"])
    assert res.exit_code == 0, res.output
    assert "restored" in res.output


def test_cli_drop_missing_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fcpx_settings(monkeypatch, tmp_path)
    runner = CliRunner()
    res = runner.invoke(cli, ["cut", "drop", "nope", "1"])
    assert res.exit_code != 0
    assert "run the pipeline first" in res.output
