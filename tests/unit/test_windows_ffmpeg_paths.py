"""Windows-safe ffmpeg path handling for non-ASCII filenames."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from track_manager import audio as tm_audio


def test_is_ascii_path() -> None:
    assert tm_audio._is_ascii_path(Path("/tmp/foo.aiff"))
    assert not tm_audio._is_ascii_path(Path("/tmp/周杰伦.aiff"))


def test_ascii_staging_path_is_ascii() -> None:
    final = Path("/music/周杰伦 - 晴天.aiff")
    staging = tm_audio.ascii_staging_path(final)
    assert tm_audio._is_ascii_path(staging)
    assert staging.suffix == ".aiff"
    assert staging.parent == final.parent


def test_encode_to_stages_non_ascii_dst_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows, encode into an ASCII sibling then rename to the real name."""
    src = tmp_path / "src.flac"
    src.write_bytes(b"fLaCfake")
    dst = tmp_path / "周杰伦 - 晴天.aiff"

    monkeypatch.setattr(tm_audio.sys, "platform", "win32")

    encoded_dsts: list[Path] = []

    def fake_aiff(s: Path, d: Path) -> Path:
        encoded_dsts.append(d)
        assert tm_audio._is_ascii_path(d)
        d.write_bytes(b"AIFF")
        return d

    monkeypatch.setattr(tm_audio, "encode_to_aiff", fake_aiff)

    result = tm_audio.encode_to("aiff", src, dst)
    assert result == dst
    assert dst.exists()
    assert dst.read_bytes() == b"AIFF"
    assert encoded_dsts
    assert encoded_dsts[0] != dst
    assert not encoded_dsts[0].exists()  # renamed away


def test_encode_to_keeps_direct_path_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "win32":
        pytest.skip("posix-only assertion")

    src = tmp_path / "src.flac"
    src.write_bytes(b"fLaCfake")
    dst = tmp_path / "周杰伦 - 晴天.aiff"

    seen: list[Path] = []

    def fake_aiff(s: Path, d: Path) -> Path:
        seen.append(d)
        d.write_bytes(b"AIFF")
        return d

    monkeypatch.setattr(tm_audio, "encode_to_aiff", fake_aiff)
    tm_audio.encode_to("aiff", src, dst)
    assert seen == [dst]


def test_ffmpeg_arg_path_uses_short_path_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "歌.aiff"
    path.write_bytes(b"x")
    monkeypatch.setattr(tm_audio.sys, "platform", "win32")
    monkeypatch.setattr(tm_audio, "_windows_short_path", lambda p: r"C:\TEMP\GE~1.AIF")
    assert tm_audio.ffmpeg_arg_path(path) == r"C:\TEMP\GE~1.AIF"


def test_ffmpeg_arg_path_passthrough_ascii() -> None:
    p = Path("/tmp/foo.aiff")
    assert tm_audio.ffmpeg_arg_path(p) == str(p)
