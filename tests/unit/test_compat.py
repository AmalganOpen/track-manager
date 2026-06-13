"""Unit tests for CDJ-2000NXS compatibility classification."""

from pathlib import Path

import pytest

from track_manager import compat, rekordbox_db
from track_manager.migrate import BACKUP_DIRNAME


def _classify(monkeypatch, path: str, stream: dict | None) -> compat.CompatResult:
    """Classify ``path`` with ffprobe stubbed to return ``stream``."""
    monkeypatch.setattr(compat.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(compat, "_probe", lambda _p: stream)
    return compat.classify(Path(path))


@pytest.mark.parametrize(
    "name, stream, expect_compatible, expect_unknown",
    [
        # Standard AIFF the migrator produces — the happy path.
        ("a.aiff", {"codec_name": "pcm_s16be", "sample_rate": "44100"}, True, False),
        # 24-bit / 48 kHz AIFF is within spec.
        ("b.aiff", {"codec_name": "pcm_s24be", "sample_rate": "48000"}, True, False),
        # Hi-res AIFF over the 48 kHz ceiling.
        ("c.aiff", {"codec_name": "pcm_s16be", "sample_rate": "96000"}, False, False),
        # 32-bit float PCM is rejected by the hardware decoder.
        ("d.wav", {"codec_name": "pcm_f32le", "sample_rate": "44100"}, False, False),
        # WAVE_FORMAT_EXTENSIBLE header (codec_tag 0xfffe) even at valid specs.
        (
            "e.wav",
            {"codec_name": "pcm_s24le", "sample_rate": "44100", "codec_tag": "0xfffe"},
            False,
            False,
        ),
        # Plain PCM WAV with a normal tag is fine.
        (
            "f.wav",
            {"codec_name": "pcm_s16le", "sample_rate": "48000", "codec_tag": "0x0001"},
            True,
            False,
        ),
        # ALAC and FLAC are unsupported on the original NXS.
        ("g.m4a", {"codec_name": "alac", "sample_rate": "44100"}, False, False),
        ("h.flac", {"codec_name": "flac", "sample_rate": "44100"}, False, False),
        # AAC and MP3 within the 48 kHz cap are fine.
        ("i.m4a", {"codec_name": "aac", "sample_rate": "44100"}, True, False),
        ("j.mp3", {"codec_name": "mp3", "sample_rate": "44100"}, True, False),
        # AAC above 48 kHz is rejected.
        ("k.m4a", {"codec_name": "aac", "sample_rate": "96000"}, False, False),
    ],
)
def test_classify_cases(monkeypatch, name, stream, expect_compatible, expect_unknown):
    result = _classify(monkeypatch, name, stream)
    assert result.compatible is expect_compatible
    assert result.unknown is expect_unknown


def test_classify_unreadable_is_unknown(monkeypatch):
    result = _classify(monkeypatch, "broken.aiff", None)
    assert result.compatible is False
    assert result.unknown is True


def test_classify_missing_ffprobe_is_unknown(monkeypatch):
    monkeypatch.setattr(compat.shutil, "which", lambda _name: None)
    result = compat.classify(Path("x.aiff"))
    assert result.compatible is False
    assert result.unknown is True


def test_target_aiff_path_backup_resolves_to_library_root():
    library = Path("/lib")
    backup_track = library / BACKUP_DIRNAME / "Artist - Title.m4a"
    assert (
        rekordbox_db._target_aiff_path(backup_track, library)
        == library / "Artist - Title.aiff"
    )


def test_target_aiff_path_normal_uses_suffix_swap():
    library = Path("/lib")
    track = library / "Artist - Title.m4a"
    assert (
        rekordbox_db._target_aiff_path(track, library)
        == library / "Artist - Title.aiff"
    )
