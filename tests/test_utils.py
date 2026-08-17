from directorcut.utils import format_timecode, human_duration, parse_timecode


def test_timecode_roundtrip():
    assert parse_timecode("00:01:12.500") == 72.5
    assert format_timecode(72.5) == "00:01:12.500"


def test_human_duration():
    assert human_duration(3661.0) == "01:01:01.000"
