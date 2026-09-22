"""Unit tests for Pioneer USB-player compatibility classification."""

from pathlib import Path

import pytest

from track_manager import compat, rekordbox_db
from track_manager.migrate import BACKUP_DIRNAME


def _classify(
    monkeypatch, path: str, stream: dict | None, *, gear=None
) -> compat.CompatResult:
    """Classify ``path`` with ffprobe stubbed to return ``stream``."""
    monkeypatch.setattr(compat.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(compat, "_probe", lambda _p: stream)
    return compat.classify(Path(path), gear=gear)


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


@pytest.mark.parametrize(
    "name, expect_issue",
    [
        ("Artist - Title.aiff", False),
        ("周杰伦 - 晴天.aiff", False),
        ("Artist - Song: Remix.aiff", True),
        ("AC/DC - Thunderstruck.aiff", True),
        ('Track "Live".aiff', True),
        ("Song*.aiff", True),
        ("bad\x00name.aiff", True),
        ("trailing.aiff.", True),
        ("trailing.aiff ", True),
        ("a" * 256 + ".aiff", True),
        ("a" * 250 + ".aiff", False),
    ],
)
def test_classify_filename(name, expect_issue):
    issue = compat.classify_filename(name)
    assert (issue is not None) is expect_issue


def test_classify_rejects_fat_illegal_name_even_when_format_ok(monkeypatch):
    result = _classify(
        monkeypatch,
        "Song: Remix.aiff",
        {"codec_name": "pcm_s16be", "sample_rate": "44100"},
    )
    assert result.compatible is False
    assert result.unknown is False
    assert "filename:" in result.reason
    assert ":" in result.reason


def test_classify_combines_format_and_filename_issues(monkeypatch):
    result = _classify(
        monkeypatch,
        "Song: Remix.flac",
        {"codec_name": "flac", "sample_rate": "44100"},
    )
    assert result.compatible is False
    assert result.unknown is False
    assert "FLAC" in result.reason
    assert "filename:" in result.reason


def _stub_cover(monkeypatch, info) -> None:
    """Skip reading a real file; inject ``info`` (None = no cover)."""
    monkeypatch.setattr(
        "track_manager.cover.extract_embedded_cover",
        lambda _p: b"jpeg" if info is not None else None,
    )
    monkeypatch.setattr(
        "track_manager.cover.jpeg_info",
        lambda _data: info,
    )


def test_classify_ok_cover_under_cap(monkeypatch):
    from track_manager.cover import CoverInfo

    _stub_cover(monkeypatch, CoverInfo(640, 640, False, 1000))
    result = _classify(
        monkeypatch,
        "ok.aiff",
        {"codec_name": "pcm_s16be", "sample_rate": "44100"},
    )
    assert result.compatible is True


def test_classify_rejects_oversized_cover(monkeypatch):
    from track_manager.cover import CoverInfo

    _stub_cover(monkeypatch, CoverInfo(3000, 3000, False, 3_000_000))
    result = _classify(
        monkeypatch,
        "huge.aiff",
        {"codec_name": "pcm_s16be", "sample_rate": "44100"},
    )
    assert result.compatible is False
    assert result.unknown is False
    assert "cover:" in result.reason
    assert "3000" in result.reason
    assert "scale-covers" in result.reason


def test_classify_rejects_progressive_cover(monkeypatch):
    from track_manager.cover import CoverInfo

    _stub_cover(monkeypatch, CoverInfo(500, 500, True, 20_000))
    result = _classify(
        monkeypatch,
        "prog.aiff",
        {"codec_name": "pcm_s16be", "sample_rate": "44100"},
    )
    assert result.compatible is False
    assert "progressive JPEG" in result.reason


def test_classify_combines_cover_and_filename(monkeypatch):
    from track_manager.cover import CoverInfo

    _stub_cover(monkeypatch, CoverInfo(1280, 1280, False, 100_000))
    result = _classify(
        monkeypatch,
        "Song: Remix.aiff",
        {"codec_name": "pcm_s16be", "sample_rate": "44100"},
    )
    assert result.compatible is False
    assert "filename:" in result.reason
    assert "cover:" in result.reason


def test_flac_fails_only_nxs(monkeypatch):
    result = _classify(
        monkeypatch, "a.flac", {"codec_name": "flac", "sample_rate": "44100"}
    )
    assert result.compatible is False
    names = {name for name, _reason in result.failures}
    assert names == {"CDJ-2000NXS"}
    assert "XDJ-1000MK2" not in result.reason


def test_flac_ok_on_xdj_1000mk2(monkeypatch):
    result = _classify(
        monkeypatch,
        "a.flac",
        {"codec_name": "flac", "sample_rate": "44100"},
        gear=["xdj-1000mk2"],
    )
    assert result.compatible is True


def test_96k_pcm_fails_nxs_and_xdj_ok_on_3000(monkeypatch):
    stream = {"codec_name": "pcm_s16be", "sample_rate": "96000"}
    default = _classify(monkeypatch, "hi.aiff", stream)
    assert default.compatible is False
    names = {name for name, _reason in default.failures}
    assert names == {"CDJ-2000NXS", "XDJ-1000MK2"}

    on_3000 = _classify(monkeypatch, "hi.aiff", stream, gear=["cdj-3000"])
    assert on_3000.compatible is True

    on_nxs2 = _classify(monkeypatch, "hi.aiff", stream, gear=["nxs2"])
    assert on_nxs2.compatible is True


def test_cover_fails_only_xdj_1000mk2(monkeypatch):
    from track_manager.cover import CoverInfo

    _stub_cover(monkeypatch, CoverInfo(3000, 3000, False, 3_000_000))
    result = _classify(
        monkeypatch,
        "huge.aiff",
        {"codec_name": "pcm_s16be", "sample_rate": "44100"},
    )
    names = {name for name, _reason in result.failures}
    assert names == {"XDJ-1000MK2"}
    assert "XDJ-1000MK2" in result.reason


def test_resolve_devices_aliases_and_unknown():
    specs = compat.resolve_devices(["nxs", "xdj-1000 mk2"])
    assert [s.id for s in specs] == ["cdj-2000nxs", "xdj-1000mk2"]
    with pytest.raises(ValueError, match="unknown player"):
        compat.resolve_devices(["cdj-4000"])


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
