"""Unit tests for quality bitrate provenance handling."""

from pathlib import Path

from track_manager import quality


class _FakeTextFrame:
    def __init__(self, value: str):
        self.text = [value]


class _FakeAudio:
    def __init__(self, tags=None):
        self.tags = tags or {}


def test_bitrate_value_to_bps_kbps_and_bps():
    assert quality._bitrate_value_to_bps(160) == 160000
    assert quality._bitrate_value_to_bps("129.86") == 129860
    assert quality._bitrate_value_to_bps(1411200) == 1411200


def test_extract_original_bitrate_from_blob(monkeypatch):
    def _fake_read_blob(_path: Path):
        return {"provenance": {"original_bitrate": 192}}

    monkeypatch.setattr(quality.tm_blob, "read_blob", _fake_read_blob)
    audio = _FakeAudio(tags={})

    got = quality._extract_original_bitrate_bps(Path("dummy.aiff"), audio)
    assert got == 192000


def test_extract_original_bitrate_from_legacy_txxx(monkeypatch):
    monkeypatch.setattr(quality.tm_blob, "read_blob", lambda _path: None)
    audio = _FakeAudio(tags={"TXXX:ORIGINAL_BITRATE": _FakeTextFrame("128")})

    got = quality._extract_original_bitrate_bps(Path("legacy.mp3"), audio)
    assert got == 128000
