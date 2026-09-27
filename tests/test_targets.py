"""Blueprint target validation: length + B-roll coverage (ticket 6).

All fabricated - no API, no media. The key behaviour: an out-of-target
blueprint triggers exactly ONE corrective director call carrying the
measured deltas; config values override direction-parsed targets.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import directorcut.pipeline as pipeline
from directorcut.assembly.targets import (
    LengthWindow,
    Targets,
    correction_note,
    effective_targets,
    measure_blueprint,
    parse_config_length,
    parse_coverage_min,
    parse_length_window,
    report_line,
    violations,
)
from directorcut.config import Settings
from directorcut.models import AssemblyBlueprint, Cut, TimelineMeta
from directorcut.utils import format_timecode

A = Path("/media/aroll.mp4")
BROLL = Path("/media/broll.mp4")

DIRECTION = (
    "HARD LENGTH TARGET: the finished timeline must be between 7:00 and "
    "9:00. The primary take is ~16.5 minutes; it contains re-takes.\n"
    "B-ROLL COVERAGE: at least 60% of the V1 runtime must have a V2 "
    "overlay. Use the gameplay clips heavily."
)


def v1(in_s: float, dur: float, tl_in: float, file: Path = A) -> Cut:
    return Cut(
        track="V1_A1_PRIMARY",
        source_file=file,
        timeline_in=format_timecode(tl_in),
        timeline_out=format_timecode(tl_in + dur),
        source_in=format_timecode(in_s),
        source_out=format_timecode(in_s + dur),
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


def bp(cuts: list[Cut]) -> AssemblyBlueprint:
    return AssemblyBlueprint(
        timeline_meta=TimelineMeta(project_name="t"), cuts=cuts
    )


# ---------- parsing ----------


def test_parses_between_mm_ss_from_direction() -> None:
    w = parse_length_window(DIRECTION)
    assert w == LengthWindow(420.0, 540.0)


def test_parses_bare_minutes_range() -> None:
    w = parse_length_window("keep it between 7 and 9 minutes please")
    assert w == LengthWindow(420.0, 540.0)


def test_parses_dash_range_and_one_sided() -> None:
    assert parse_length_window("aim for 7:00-9:00 total") == LengthWindow(
        420.0, 540.0
    )
    w = parse_length_window("the timeline must be at least 6:00")
    assert w.min_s == 360.0 and w.max_s is None
    w = parse_length_window("keep it under 10 minutes")
    assert w.min_s is None and w.max_s == 600.0


def test_no_length_info_yields_empty_window() -> None:
    assert parse_length_window("make it funny") == LengthWindow()
    assert parse_length_window(None) == LengthWindow()


def test_coverage_min_from_direction() -> None:
    assert parse_coverage_min(DIRECTION) == pytest.approx(0.6)
    assert parse_coverage_min("cover ~60-70% with B-roll") == pytest.approx(0.6)
    assert parse_coverage_min("no coverage talk") == 0.0


def test_config_length_range_and_single() -> None:
    assert parse_config_length("7:00-9:00") == LengthWindow(420.0, 540.0)
    assert parse_config_length("420-540") == LengthWindow(420.0, 540.0)
    w = parse_config_length("480")
    assert w.min_s == pytest.approx(432.0) and w.max_s == pytest.approx(528.0)
    w = parse_config_length(480)
    assert w.min_s == pytest.approx(432.0) and w.max_s == pytest.approx(528.0)


def test_config_overrides_direction() -> None:
    s = Settings(target_length_s="10:00-12:00", broll_coverage_min=0.5)
    t = effective_targets(s, DIRECTION)
    assert t.length == LengthWindow(600.0, 720.0)
    assert t.broll_coverage_min == pytest.approx(0.5)

    # Partial override: config coverage wins, direction length still used.
    s = Settings(broll_coverage_min=0.5)
    t = effective_targets(s, DIRECTION)
    assert t.length == LengthWindow(420.0, 540.0)
    assert t.broll_coverage_min == pytest.approx(0.5)

    # Percent-style coverage value is tolerated.
    s = Settings(broll_coverage_min=60)
    assert effective_targets(s, "").broll_coverage_min == pytest.approx(0.6)


# ---------- measuring ----------


def test_measure_blueprint_length_and_coverage_union() -> None:
    cuts = [
        v1(0, 60, 0),  # V1 0-60
        v1(100, 60, 60),  # V1 60-120 -> spine length 120
        ovl(10, 30),  # 20s
        ovl(25, 40),  # overlaps previous -> union 10-40 = 30s
        ovl(80, 100),  # 20s
    ]
    length_s, coverage = measure_blueprint(bp(cuts))
    assert length_s == pytest.approx(120.0)
    assert coverage == pytest.approx(50.0 / 120.0)


def test_measure_empty_spine() -> None:
    assert measure_blueprint(bp([])) == (0.0, 0.0)


# ---------- violations / notes / report ----------


def test_violations_report_measured_deltas() -> None:
    t = Targets(
        length=LengthWindow(420.0, 540.0), broll_coverage_min=0.6,
        source="direction",
    )
    bad = violations(t, 255.0, 0.34)
    assert len(bad) == 2
    assert "UNDER" in bad[0] and "07:00" in bad[0]
    assert "34%" in bad[1] and "60%" in bad[1]

    assert violations(t, 480.0, 0.6) == []


def test_correction_note_carries_deltas() -> None:
    t = Targets(
        length=LengthWindow(420.0, 540.0), broll_coverage_min=0.6,
        source="direction",
    )
    note = correction_note(t, violations(t, 255.0, 0.34))
    assert "MEASURED TARGET VIOLATIONS" in note
    assert "UNDER" in note
    assert "60%" in note


def test_report_line_marks_ok_and_miss() -> None:
    t = Targets(
        length=LengthWindow(420.0, 540.0), broll_coverage_min=0.6,
        source="direction",
    )
    ok = report_line(t, 480.0, 0.62)
    assert "OK" in ok and "MISS" not in ok
    miss = report_line(t, 255.0, 0.3)
    assert "MISS" in miss
    assert "00:04:15" in miss


# ---------- one corrective retry ----------


def test_one_corrective_retry_with_measured_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    short = bp([v1(0, 255.0, 0.0)])  # 4:15 - the real-world miss
    fixed = bp([v1(0, 480.0, 0.0)])  # 8:00

    def fake_director(manifest, nodes, events, settings, correction=None):
        calls.append({"correction": correction})
        return short if correction is None else fixed

    monkeypatch.setattr(pipeline, "run_director", fake_director)
    manifest = SimpleNamespace(direction=DIRECTION)
    settings = Settings()

    out = pipeline._director_with_target_check(manifest, [], [], settings)

    assert len(calls) == 2  # exactly one corrective retry
    note = calls[1]["correction"]
    assert note is not None
    assert "04:15" in note and "UNDER" in note
    assert "60%" in note
    assert out is fixed


def test_on_target_blueprint_gets_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_director(manifest, nodes, events, settings, correction=None):
        nonlocal calls
        calls += 1
        return bp([v1(0, 480.0, 0.0), ovl(0, 300.0)])

    monkeypatch.setattr(pipeline, "run_director", fake_director)
    manifest = SimpleNamespace(direction=DIRECTION)
    out = pipeline._director_with_target_check(manifest, [], [], Settings())
    assert calls == 1
    assert out is not None


def test_no_targets_means_no_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_director(manifest, nodes, events, settings, correction=None):
        nonlocal calls
        calls += 1
        assert correction is None
        return bp([v1(0, 10.0, 0.0)])

    monkeypatch.setattr(pipeline, "run_director", fake_director)
    manifest = SimpleNamespace(direction="make it funny")
    pipeline._director_with_target_check(manifest, [], [], Settings())
    assert calls == 1
