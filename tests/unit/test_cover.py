"""Scale embedded cover art down to COVER_MAX_SIDE."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from track_manager import cover as tm_cover
from track_manager.library import list_library_tracks


def _jpeg(tmp_path: Path, w: int, h: int) -> bytes:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    dest = tmp_path / f"{w}x{h}.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:s={w}x{h}:d=0.1",
            "-frames:v",
            "1",
            str(dest),
        ],
        check=True,
        timeout=30,
    )
    return dest.read_bytes()


def _aiff_with_cover(tmp_path: Path, jpeg: bytes, name: str = "track.aiff") -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    from mutagen.aiff import AIFF
    from mutagen.id3 import APIC, TIT2

    from track_manager import blob as tm_blob

    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            "pcm_s16be",
            "-ar",
            "44100",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    audio = AIFF(str(path))
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=["Keep Me"]))
    audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=jpeg))
    audio.save()
    doc = tm_blob.empty_document()
    doc["track"]["title"] = "Keep Me"
    tm_blob.write_blob(path, doc)
    return path


def test_jpeg_info_reads_dimensions(tmp_path: Path) -> None:
    data = _jpeg(tmp_path, 320, 240)
    info = tm_cover.jpeg_info(data)
    assert info is not None
    assert info.width == 320
    assert info.height == 240
    assert info.progressive is False


def test_jpeg_info_rejects_invalid_segment_length() -> None:
    # SOF0 with seglen 0 must not hang or invent dimensions.
    data = b"\xff\xd8" + b"\xff\xc0\x00\x00" * 8
    assert tm_cover.jpeg_info(data) is None


def test_prepare_leaves_small_jpeg_unchanged(tmp_path: Path) -> None:
    data = _jpeg(tmp_path, 400, 400)
    out = tm_cover.prepare_cover_jpeg(data, max_side=640)
    assert out == data


def test_prepare_scales_large_jpeg(tmp_path: Path) -> None:
    data = _jpeg(tmp_path, 1200, 800)
    out = tm_cover.prepare_cover_jpeg(data, max_side=640)
    info = tm_cover.jpeg_info(out)
    assert info is not None
    assert max(info.width, info.height) == 640
    assert info.width == 640
    assert info.height == 427 or info.height == 426  # AR 3:2
    assert info.progressive is False
    assert len(out) < len(data)


def test_prepare_rejects_out_of_range_max_side(tmp_path: Path) -> None:
    data = _jpeg(tmp_path, 800, 800)
    assert tm_cover.prepare_cover_jpeg(data, max_side=0) == data
    assert tm_cover.prepare_cover_jpeg(data, max_side=99_999) == data


def test_list_library_tracks_skips_hidden(tmp_path: Path) -> None:
    visible = tmp_path / "Song.aiff"
    hidden = tmp_path / ".tm_cover_1.aiff"
    visible.write_bytes(b"x")
    hidden.write_bytes(b"x")
    names = {p.name for p in list_library_tracks(tmp_path)}
    assert names == {"Song.aiff"}


def test_scale_skips_when_already_small(tmp_path: Path) -> None:
    jpeg = _jpeg(tmp_path, 400, 400)
    path = _aiff_with_cover(tmp_path, jpeg)
    before = path.read_bytes()
    result = tm_cover.scale_cover_in_file(path, max_side=640)
    assert result.action == "skipped"
    assert path.read_bytes() == before


def test_scale_dry_run_does_not_write(tmp_path: Path) -> None:
    jpeg = _jpeg(tmp_path, 1200, 800)
    path = _aiff_with_cover(tmp_path, jpeg)
    before = path.read_bytes()
    result = tm_cover.scale_cover_in_file(path, max_side=640, dry_run=True)
    assert result.action == "scaled"
    assert "dry-run" in result.detail
    assert path.read_bytes() == before


def test_scale_cover_preserves_blob_and_title(tmp_path: Path) -> None:
    from mutagen.aiff import AIFF

    from track_manager import audio as tm_audio
    from track_manager import blob as tm_blob

    jpeg = _jpeg(tmp_path, 1200, 800)
    path = _aiff_with_cover(tmp_path, jpeg)
    duration_before = tm_audio.probe_audio(path).get("duration_seconds")
    result = tm_cover.scale_cover_in_file(path, max_side=640)
    assert result.action == "scaled", result.detail
    assert result.new is not None
    assert max(result.new.width, result.new.height) == 640

    tags = AIFF(str(path)).tags
    assert tags is not None
    assert tags.getall("TIT2")[0].text == ["Keep Me"]
    assert tm_blob.read_blob(path)["track"]["title"] == "Keep Me"
    cover = tm_cover.extract_embedded_cover(path)
    assert cover is not None
    info = tm_cover.jpeg_info(cover)
    assert info is not None
    assert max(info.width, info.height) == 640
    duration_after = tm_audio.probe_audio(path).get("duration_seconds")
    assert duration_before == pytest.approx(duration_after, abs=0.05)
    leftover = list(tmp_path.glob(".tm_cover_*"))
    assert leftover == []


def test_failed_write_leaves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jpeg = _jpeg(tmp_path, 1200, 800)
    path = _aiff_with_cover(tmp_path, jpeg)
    original = path.read_bytes()

    def boom(_path: Path, _jpeg: bytes) -> None:
        raise RuntimeError("save exploded")

    monkeypatch.setattr(tm_cover, "replace_embedded_cover", boom)
    result = tm_cover.scale_cover_in_file(path, max_side=640)
    assert result.action == "failed"
    assert path.read_bytes() == original
    leftover = list(tmp_path.glob(".tm_cover_*"))
    assert leftover == []


def test_invalid_max_side_fails_without_writing(tmp_path: Path) -> None:
    jpeg = _jpeg(tmp_path, 400, 400)
    path = _aiff_with_cover(tmp_path, jpeg)
    before = path.read_bytes()
    result = tm_cover.scale_cover_in_file(path, max_side=0)
    assert result.action == "failed"
    assert path.read_bytes() == before


def test_cli_whole_library_aborts_without_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from track_manager.cli import cli

    jpeg = _jpeg(tmp_path, 1200, 800)
    path = _aiff_with_cover(tmp_path, jpeg, name="Song.aiff")
    before = path.read_bytes()

    class _Cfg:
        output_dir = tmp_path

    monkeypatch.setattr("track_manager.cli.Config", lambda: _Cfg())
    result = CliRunner().invoke(cli, ["scale-covers"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    assert path.read_bytes() == before


def test_cli_dry_run_skips_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from track_manager.cli import cli

    jpeg = _jpeg(tmp_path, 1200, 800)
    path = _aiff_with_cover(tmp_path, jpeg, name="Song.aiff")
    before = path.read_bytes()

    class _Cfg:
        output_dir = tmp_path

    monkeypatch.setattr("track_manager.cli.Config", lambda: _Cfg())
    result = CliRunner().invoke(cli, ["scale-covers", "-n"])
    assert result.exit_code == 0, result.output
    assert "Aborted" not in result.output
    assert "dry-run" in result.output.lower() or "Dry-run" in result.output
    assert path.read_bytes() == before


def test_cli_rejects_file_as_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from track_manager.cli import cli

    not_a_dir = tmp_path / "file.aiff"
    not_a_dir.write_bytes(b"x")

    class _Cfg:
        output_dir = not_a_dir

    monkeypatch.setattr("track_manager.cli.Config", lambda: _Cfg())
    result = CliRunner().invoke(cli, ["scale-covers", "-y"])
    assert result.exit_code == 1
    assert "Library directory not found" in result.output


# ---------------------------------------------------------------------------
# Keeping Rekordbox's cache honest after an in-place rewrite
# ---------------------------------------------------------------------------


def _library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    jpeg = _jpeg(tmp_path, 1200, 800)
    path = _aiff_with_cover(tmp_path, jpeg, name="Song.aiff")

    class _Cfg:
        output_dir = tmp_path

    monkeypatch.setattr("track_manager.cli.Config", lambda: _Cfg())
    return path


def test_cli_refuses_to_rewrite_while_rekordbox_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check must land before any file is touched, not after."""
    from track_manager import rekordbox_db as tm_rb
    from track_manager.cli import cli

    path = _library(tmp_path, monkeypatch)
    before = path.read_bytes()
    monkeypatch.setattr(
        tm_rb,
        "running_rekordbox_processes",
        lambda: [tm_rb.RekordboxProcess(pid=42, command="/Applications/rekordbox")],
    )

    result = CliRunner().invoke(cli, ["scale-covers", "-y"])

    assert result.exit_code == 1
    assert "Rekordbox" in result.output
    assert path.read_bytes() == before


def test_cli_resyncs_after_scaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from track_manager.cli import cli

    _library(tmp_path, monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(
        "track_manager.cli._resync_rekordbox_cache", lambda: calls.append(1)
    )

    result = CliRunner().invoke(cli, ["scale-covers", "-y"])

    assert result.exit_code == 0, result.output
    assert "1200×800 → 640×427" in result.output
    assert calls == [1]


def test_cli_dry_run_neither_checks_nor_resyncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from track_manager import rekordbox_db as tm_rb
    from track_manager.cli import cli

    _library(tmp_path, monkeypatch)

    def _unexpected():
        raise AssertionError("a dry run changes nothing, so it needs no check")

    monkeypatch.setattr(tm_rb, "running_rekordbox_processes", _unexpected)
    monkeypatch.setattr("track_manager.cli._resync_rekordbox_cache", _unexpected)

    result = CliRunner().invoke(cli, ["scale-covers", "-n"])

    assert result.exit_code == 0, result.output


def test_cli_no_resync_warns_instead_of_going_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opting out is allowed, but must not look like nothing is owed."""
    from track_manager.cli import cli

    _library(tmp_path, monkeypatch)

    def _unexpected():
        raise AssertionError("--no-resync must not resync")

    monkeypatch.setattr("track_manager.cli._resync_rekordbox_cache", _unexpected)

    result = CliRunner().invoke(cli, ["scale-covers", "-y", "--no-resync"])

    assert result.exit_code == 0, result.output
    assert "tm rekordbox-resync" in result.output


def test_cli_skips_resync_when_nothing_was_scaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from track_manager.cli import cli

    jpeg = _jpeg(tmp_path, 100, 100)  # already under the cap
    _aiff_with_cover(tmp_path, jpeg, name="Small.aiff")

    class _Cfg:
        output_dir = tmp_path

    monkeypatch.setattr("track_manager.cli.Config", lambda: _Cfg())

    def _unexpected():
        raise AssertionError("nothing was rewritten, so nothing is out of sync")

    monkeypatch.setattr("track_manager.cli._resync_rekordbox_cache", _unexpected)

    result = CliRunner().invoke(cli, ["scale-covers", "-y"])

    assert result.exit_code == 0, result.output
