"""Post-director blueprint target validation: finished length + B-roll coverage.

Production finding (StS2 EA-review, 2026-09-22): the director ignored the
"7-9 minutes / 60% B-roll" direction entirely until an addendum spelled out
the numbers. So targets are enforced deterministically here, not wished for:

- Targets are PARSED out of the free-text direction ("HARD LENGTH TARGET:
  between 7:00 and 9:00", "at least 60% ... V2 overlay"), or taken from
  config (`target_length_s`, `broll_coverage_min`) - config always wins.
- After the director returns a blueprint we measure spine length and the
  union of V2 overlays over V1 runtime.
- On any violation the director is re-prompted EXACTLY ONCE with the
  measured deltas appended to the direction (no loop - a second call costs
  the same again and usually lands close enough).
- A report line is logged either way so the run output surfaces the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from directorcut.utils import format_timecode, parse_timecode

if TYPE_CHECKING:
    from directorcut.config import Settings
    from directorcut.models import AssemblyBlueprint

# Small slack so ms-rounding in timecodes doesn't trip an exact bound.
_LEN_TOL_S = 0.5
_COV_TOL = 0.005

# "between 7:00 and 9:00", "between 7 and 9 minutes", "between 420 and 540"
_LEN_BETWEEN = re.compile(
    r"between\s+(\d[\d:.]*)\s+and\s+(\d[\d:.]*)\s*(minutes?|mins?|seconds?|secs?)?",
    re.IGNORECASE,
)
# "7-9 minutes", "7:00-9:00" on a length-ish line
_LEN_DASH_MIN = re.compile(
    r"(\d[\d:.]*)\s*[-–]\s*(\d[\d:.]*)\s*(minutes?|mins?)", re.IGNORECASE
)
_LEN_DASH_COLON = re.compile(r"(\d+:\d{2})\s*[-–]\s*(\d+:\d{2})")
# "at least 7:00" / "no longer than 9:00" / "under 9 minutes"
_LEN_AT_LEAST = re.compile(
    r"at least\s+(\d[\d:.]*)\s*(minutes?|mins?|seconds?|secs?)?", re.IGNORECASE
)
_LEN_AT_MOST = re.compile(
    r"(?:at most|no longer than|under|max(?:imum)?(?: of)?)\s+"
    r"(\d[\d:.]*)\s*(minutes?|mins?|seconds?|secs?)?",
    re.IGNORECASE,
)
# Length patterns only count on lines that are actually about the cut's
# length - the same line in direction.txt also mentions the source's
# ~16.5 minutes.
_LEN_CONTEXT = re.compile(
    r"length|timeline|minutes?|mins?|target|runtime", re.IGNORECASE
)
# B-roll coverage: percentages mentioned on coverage/B-roll/overlay lines.
_COV_CONTEXT = re.compile(r"cover|overlay|b-?roll|v1 runtime|v2", re.IGNORECASE)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# "60-70%" - the low end carries no '%' of its own, so catch ranges too.
_PCT_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*\d+(?:\.\d+)?\s*%")


@dataclass(frozen=True)
class LengthWindow:
    """Acceptable finished-length window in seconds (None = unbounded)."""

    min_s: float | None = None
    max_s: float | None = None


@dataclass(frozen=True)
class Targets:
    """Effective hard targets for one run."""

    length: LengthWindow = field(default_factory=LengthWindow)
    broll_coverage_min: float = 0.0  # fraction 0..1
    source: str = "none"  # where the values came from: direction|config|both

    @property
    def has_any(self) -> bool:
        return (
            self.length.min_s is not None
            or self.length.max_s is not None
            or self.broll_coverage_min > 0
        )


def _duration_s(token: str, unit_hint: str | None) -> float:
    """Parse '7:00' (mm:ss / hh:mm:ss) or a bare number.

    Bare numbers take the unit hint when one follows the pair ("7 and 9
    minutes"); without a hint they are seconds - `target_length_s` is a
    seconds field and direction authors write prose durations as m:ss.
    """
    token = token.strip().rstrip("~")
    if ":" in token:
        parts = [float(p) for p in token.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    value = float(token)
    if unit_hint and unit_hint.lower().startswith(("min", "m")):
        return value * 60
    return value


def parse_length_window(text: str | None) -> LengthWindow:
    """Extract a hard length window from a config string or direction text.

    Recognizes "A-B"/"A..B" ranges, "between A and B", "at least A",
    "at most/under A" - with m:ss or plain numbers (unit hint or seconds).
    A single bare value from config is treated as an aim with a +-10%
    window - "target_length_s=480" means roughly eight minutes, not a hard
    floor at zero.
    """
    if not text:
        return LengthWindow()
    lo: float | None = None
    hi: float | None = None
    # "m:ss-m:ss" dash ranges are unambiguous durations - no context needed.
    for line in text.splitlines():
        if m := _LEN_DASH_COLON.search(line):
            lo = _duration_s(m.group(1), None)
            hi = _duration_s(m.group(2), None)
            break
    if lo is not None and hi is not None:
        return LengthWindow(lo, hi)
    for line in text.splitlines():
        if not _LEN_CONTEXT.search(line):
            continue
        if m := _LEN_BETWEEN.search(line):
            lo = _duration_s(m.group(1), m.group(3))
            hi = _duration_s(m.group(2), m.group(3))
            break
        if m := _LEN_DASH_MIN.search(line):
            lo = _duration_s(m.group(1), m.group(3))
            hi = _duration_s(m.group(2), m.group(3))
            break
    for line in text.splitlines():
        if not _LEN_CONTEXT.search(line):
            continue
        if lo is None and (m := _LEN_AT_LEAST.search(line)):
            lo = _duration_s(m.group(1), m.group(2))
        if hi is None and (m := _LEN_AT_MOST.search(line)):
            hi = _duration_s(m.group(1), m.group(2))
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return LengthWindow(lo, hi)


def parse_coverage_min(text: str | None) -> float:
    """Lowest B-roll/coverage percentage mentioned on coverage-ish lines.

    "at least 60%" and "~60-70%" both yield 0.6 - taking the MINIMUM of the
    percentages on those lines is the conservative floor the editor meant.
    """
    if not text:
        return 0.0
    pcts: list[float] = []
    for line in text.splitlines():
        if _COV_CONTEXT.search(line):
            pcts.extend(float(m.group(1)) for m in _PCT.finditer(line))
            pcts.extend(float(m.group(1)) for m in _PCT_RANGE.finditer(line))
    return min(pcts) / 100.0 if pcts else 0.0


# Config-string forms: "420-540", "7:00-9:00", "480 to 540", or a bare
# single duration. These match the WHOLE value - no prose context needed.
_CFG_RANGE = re.compile(
    r"^\s*(\d[\d:.]*)\s*(?:[-–]|to)\s*(\d[\d:.]*)"
    r"\s*(minutes?|mins?|seconds?|secs?)?\s*$",
    re.IGNORECASE,
)
_CFG_SINGLE = re.compile(
    r"^\s*(\d[\d:.]*)\s*(minutes?|mins?|seconds?|secs?)?\s*$", re.IGNORECASE
)


def parse_config_length(value: str | float | int | None) -> LengthWindow:
    """Parse the `target_length_s` setting: "A-B"/"A to B" range or a single
    aim (which becomes a +-10% window - "480" means roughly eight minutes,
    not a hard floor at zero). Prose forms fall back to the direction parser.
    """
    if value is None or value == "":
        return LengthWindow()
    if isinstance(value, (int, float)):
        v = float(value)
        return LengthWindow(v * 0.9, v * 1.1)
    text = str(value).strip()
    if m := _CFG_RANGE.match(text):
        lo = _duration_s(m.group(1), m.group(3))
        hi = _duration_s(m.group(2), m.group(3))
        return LengthWindow(min(lo, hi), max(lo, hi))
    if m := _CFG_SINGLE.match(text):
        v = _duration_s(m.group(1), m.group(2))
        return LengthWindow(v * 0.9, v * 1.1)
    return parse_length_window(text)


def effective_targets(settings: Settings, direction: str | None) -> Targets:
    """Config values win; anything unset falls back to direction parsing."""
    cfg_len = parse_config_length(getattr(settings, "target_length_s", None))
    cfg_cov = getattr(settings, "broll_coverage_min", 0.0) or 0.0
    if cfg_cov > 1.0:
        cfg_cov = cfg_cov / 100.0  # tolerate "60" meaning 60%

    dir_len = parse_length_window(direction)
    dir_cov = parse_coverage_min(direction)

    cfg_has_len = cfg_len.min_s is not None or cfg_len.max_s is not None
    length = cfg_len if cfg_has_len else dir_len
    coverage = cfg_cov if cfg_cov > 0 else dir_cov
    sources = []
    if cfg_has_len:
        sources.append("config-length")
    elif dir_len.min_s is not None or dir_len.max_s is not None:
        sources.append("direction-length")
    if cfg_cov > 0:
        sources.append("config-coverage")
    elif dir_cov > 0:
        sources.append("direction-coverage")
    return Targets(
        length=length,
        broll_coverage_min=coverage,
        source="+".join(sources) if sources else "none",
    )


def measure_blueprint(blueprint: AssemblyBlueprint) -> tuple[float, float]:
    """(finished length s, V2 coverage fraction over V1 runtime)."""
    v1 = [c for c in blueprint.cuts if c.track == "V1_A1_PRIMARY"]
    if not v1:
        return 0.0, 0.0
    v1_runtime = sum(
        parse_timecode(c.timeline_out) - parse_timecode(c.timeline_in)
        for c in v1
    )
    spine_end = max(parse_timecode(c.timeline_out) for c in v1)
    # Union of V2 intervals over the spine (scratch recipe: union / V1
    # runtime) - overlapping overlays count once.
    intervals = sorted(
        (parse_timecode(c.timeline_in), parse_timecode(c.timeline_out))
        for c in blueprint.cuts
        if c.track == "V2_OVERLAY_BROLL"
    )
    covered = 0.0
    cur_in: float | None = None
    cur_out = 0.0
    for i_in, i_out in intervals:
        if cur_in is None or i_in > cur_out:
            if cur_in is not None:
                covered += cur_out - cur_in
            cur_in, cur_out = i_in, i_out
        else:
            cur_out = max(cur_out, i_out)
    if cur_in is not None:
        covered += cur_out - cur_in
    coverage = covered / v1_runtime if v1_runtime > 0 else 0.0
    return spine_end, coverage


def violations(
    targets: Targets, length_s: float, coverage: float
) -> list[str]:
    """Human-readable violations with measured deltas (empty = on target)."""
    msgs: list[str] = []
    lo, hi = targets.length.min_s, targets.length.max_s
    if lo is not None and length_s < lo - _LEN_TOL_S:
        msgs.append(
            f"finished length {format_timecode(length_s)} is "
            f"{format_timecode(lo - length_s)} UNDER the "
            f"{format_timecode(lo)} minimum"
        )
    if hi is not None and length_s > hi + _LEN_TOL_S:
        msgs.append(
            f"finished length {format_timecode(length_s)} is "
            f"{format_timecode(length_s - hi)} OVER the "
            f"{format_timecode(hi)} maximum"
        )
    if coverage < targets.broll_coverage_min - _COV_TOL:
        msgs.append(
            f"B-roll coverage is {coverage * 100:.0f}% of V1 runtime - "
            f"{(targets.broll_coverage_min - coverage) * 100:.0f} points "
            f"under the {targets.broll_coverage_min * 100:.0f}% minimum"
        )
    return msgs


def correction_note(targets: Targets, bad: list[str]) -> str:
    """Text appended to the direction for the single corrective re-prompt."""
    bounds = ""
    lo, hi = targets.length.min_s, targets.length.max_s
    if lo is not None and hi is not None:
        bounds = f" between {format_timecode(lo)} and {format_timecode(hi)}"
    elif lo is not None:
        bounds = f" at least {format_timecode(lo)}"
    elif hi is not None:
        bounds = f" at most {format_timecode(hi)}"
    lines = [
        "MEASURED TARGET VIOLATIONS in your previous draft - fix them:",
        *[f"- {m}." for m in bad],
        f"The finished timeline must be{bounds}."
        if bounds
        else "Meet the stated length target.",
    ]
    if targets.broll_coverage_min > 0:
        lines.append(
            f"V2 overlay coverage of V1 runtime must be at least "
            f"{targets.broll_coverage_min * 100:.0f}%."
        )
    lines.append("Adjust the cut list; keep everything else the same.")
    return "\n".join(lines)


def report_line(
    targets: Targets, length_s: float, coverage: float
) -> str:
    """One-line run report: measured vs target for length and coverage."""
    lo, hi = targets.length.min_s, targets.length.max_s
    if lo is not None and hi is not None:
        window = f"{format_timecode(lo)}-{format_timecode(hi)}"
    elif lo is not None:
        window = f">={format_timecode(lo)}"
    elif hi is not None:
        window = f"<={format_timecode(hi)}"
    else:
        window = "none"
    len_ok = (
        (lo is None or length_s >= lo - _LEN_TOL_S)
        and (hi is None or length_s <= hi + _LEN_TOL_S)
    )
    cov_ok = coverage >= targets.broll_coverage_min - _COV_TOL
    return (
        f"[targets] length {format_timecode(length_s)} vs target {window} "
        f"[{'OK' if len_ok else 'MISS'}]; "
        f"B-roll coverage {coverage * 100:.0f}% vs min "
        f"{targets.broll_coverage_min * 100:.0f}% "
        f"[{'OK' if cov_ok else 'MISS'}] (source: {targets.source})"
    )
