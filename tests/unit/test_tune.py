"""Tests for pitch-tune math, track matching, and output naming."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from track_manager import audio as tm_audio
from track_manager import library as tm_library
from track_manager import tune as tm_tune


def test_bpm_percent_to_cents_positive_semitone() -> None:
    # +5.946309% ≈ +100 cents (one semitone up)
    cents = tm_audio.bpm_percent_to_cents(5.946309)
    assert cents == pytest.approx(100.0, abs=0.01)


def test_bpm_percent_to_cents_negative_is_inverse() -> None:
    # Asymmetric formula: -p is the reciprocal of +p, so magnitudes match.
    up = tm_audio.bpm_percent_to_cents(5.946309)
    down = tm_audio.bpm_percent_to_cents(-5.946309)
    assert down == pytest.approx(-up, abs=1e-9)
    assert down == pytest.approx(-100.0, abs=0.01)


def test_bpm_percent_to_cents_two_percent() -> None:
    assert tm_audio.bpm_percent_to_cents(2.0) == pytest.approx(
        1200 * math.log2(1.02), abs=1e-9
    )
    assert tm_audio.bpm_percent_to_cents(-2.0) == pytest.approx(
        1200 * math.log2(1 / 1.02), abs=1e-9
    )


def test_cents_to_ratio_roundtrip() -> None:
    for cents in (0.0, 100.0, -50.0, 35.0):
        ratio = tm_audio.cents_to_ratio(cents)
        assert 1200 * math.log2(ratio) == pytest.approx(cents, abs=1e-9)


def test_find_matching_tracks_partial_title(tmp_path: Path) -> None:
    (tmp_path / "Artist - Midnight City.aiff").write_bytes(b"x")
    (tmp_path / "Artist - Daylight.aiff").write_bytes(b"x")
    (tmp_path / "Other - Midnight Run.m4a").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("nope")

    matches = tm_library.find_matching_tracks("midnight", tmp_path)
    names = {p.name for p in matches}
    assert names == {
        "Artist - Midnight City.aiff",
        "Other - Midnight Run.m4a",
    }


def test_find_matching_tracks_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "Foo - Bar.aiff").write_bytes(b"x")
    matches = tm_library.find_matching_tracks("BAR", tmp_path)
    assert len(matches) == 1


def test_pick_track_single_auto_selects(tmp_path: Path) -> None:
    path = tmp_path / "only.aiff"
    path.write_bytes(b"x")
    assert tm_library.pick_track([path]) == path


def test_pick_track_interactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = tmp_path / "a.aiff"
    b = tmp_path / "b.aiff"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert tm_library.pick_track([a, b]) == b


def test_resolve_track_absolute(tmp_path: Path) -> None:
    path = tmp_path / "track.aiff"
    path.write_bytes(b"x")
    got = tm_library.resolve_track(str(path), absolute=True, library_dir=tmp_path)
    assert got == path.resolve()


def test_resolve_track_absolute_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tm_library.resolve_track(
            str(tmp_path / "nope.aiff"), absolute=True, library_dir=tmp_path
        )


def test_format_tune_label() -> None:
    assert tm_tune.format_tune_label(cents=34.28, bpm_percent=2.0) == "+2%"
    assert tm_tune.format_tune_label(cents=50.0, bpm_percent=None) == "+50c"
    assert tm_tune.format_tune_label(cents=-34.28, bpm_percent=-2.0) == "-2%"


def test_format_from_path() -> None:
    assert tm_audio.format_from_path(Path("x.aiff")) == "aiff"
    assert tm_audio.format_from_path(Path("x.m4a")) == "m4a"
    assert tm_audio.format_from_path(Path("x.mp3")) == "mp3"
    with pytest.raises(ValueError):
        tm_audio.format_from_path(Path("x.flac"))


def test_pcm_codec_for_aiff_preserves_source() -> None:
    assert tm_audio._pcm_codec_for_aiff({"codec": "pcm_s24be"}) == "pcm_s24be"
    assert tm_audio._pcm_codec_for_aiff({"codec": None, "bit_depth": 24}) == "pcm_s24be"
    assert (
        tm_audio._pcm_codec_for_aiff({"codec": "aac", "bit_depth": 16}) == "pcm_s16be"
    )


def test_copy_all_tags_aiff(tmp_path: Path) -> None:
    from mutagen.aiff import AIFF
    from mutagen.id3 import TIT2, TPE1

    from track_manager import blob as tm_blob

    src = tmp_path / "src.aiff"
    dst = tmp_path / "dst.aiff"
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")

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
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.2",
            "-c:a",
            "pcm_s16be",
            "-ar",
            "44100",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )

    audio = AIFF(str(src))
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=["Keep Me"]))
    audio.tags.add(TPE1(encoding=3, text=["Artist"]))
    audio.save()
    doc = tm_blob.empty_document()
    doc["track"]["title"] = "Keep Me"
    tm_blob.write_blob(src, doc)

    tm_audio.copy_all_tags(src, dst)
    dst_tags = AIFF(str(dst)).tags
    assert dst_tags is not None
    assert dst_tags.getall("TIT2")[0].text == ["Keep Me"]
    assert tm_blob.read_blob(dst)["track"]["title"] == "Keep Me"


def test_tune_track_in_place(tmp_path: Path) -> None:
    """Tune must overwrite the original path — no sibling (+N%) file."""
    import shutil
    import subprocess

    from mutagen.aiff import AIFF
    from mutagen.id3 import TALB, TIT2, TPE1

    from track_manager import blob as tm_blob

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")

    src = tmp_path / "Artist - Song.aiff"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-c:a",
            "pcm_s24be",
            "-ar",
            "48000",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    audio = AIFF(str(src))
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=["Song"]))
    audio.tags.add(TPE1(encoding=3, text=["Artist"]))
    audio.tags.add(TALB(encoding=3, text=["Album"]))
    audio.save()
    doc = tm_blob.empty_document()
    doc["track"]["title"] = "Song"
    doc["track"]["album"] = "Album"
    tm_blob.write_blob(src, doc)

    before = tm_audio.probe_audio(src)
    result = tm_tune.tune_track(
        src, cents=tm_audio.bpm_percent_to_cents(2.0), bpm_percent=2.0
    )

    assert result == src
    assert src.name == "Artist - Song.aiff"
    assert not list(tmp_path.glob("*(+2%)*"))
    assert not list(tmp_path.glob(".tm_tune_*"))

    after = tm_audio.probe_audio(src)
    assert after["codec"] == before["codec"]
    assert after["sample_rate"] == before["sample_rate"]
    assert after["duration_seconds"] == pytest.approx(
        before["duration_seconds"], rel=0.02, abs=0.05
    )

    tags = AIFF(str(src)).tags
    assert tags is not None
    assert tags.getall("TALB")[0].text == ["Album"]
    assert "Song (+2%)" in tags.getall("TIT2")[0].text[0]
    tuning = [f for f in tags.getall("TXXX") if f.desc == "TM_TUNING"]
    assert tuning
    assert "+2%" in tuning[0].text[0]

    blob = tm_blob.read_blob(src)
    assert blob is not None
    assert blob["track"]["title"] == "Song (+2%)"
    assert blob["track"]["album"] == "Album"


def test_pitch_filter_keeps_tempo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tm_audio, "_HAS_RUBBERBAND", True)
    af = tm_audio._pitch_filter(100.0, 44100)
    assert af.startswith("rubberband=pitch=")
    assert "tempo=1" in af
    assert "pitchq=quality" in af
    assert "transients=crisp" in af
    assert "window=standard" in af
    assert "channels=together" in af

    monkeypatch.setattr(tm_audio, "_HAS_RUBBERBAND", False)
    af = tm_audio._pitch_filter(100.0, 44100)
    assert "asetrate=" in af
    assert "atempo=" in af


def test_pitch_shift_preserves_duration(tmp_path: Path) -> None:
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")

    src = tmp_path / "src.aiff"
    dst = tmp_path / "dst.aiff"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "pcm_s16be",
            "-ar",
            "44100",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    before = tm_audio.probe_audio(src)["duration_seconds"]
    tm_audio.pitch_shift_to(src, dst, tm_audio.bpm_percent_to_cents(2.0), "aiff")
    after = tm_audio.probe_audio(dst)["duration_seconds"]
    assert before is not None and after is not None
    # Pitch-only: duration must stay (~vinyl-style would shrink by ~2%).
    assert after == pytest.approx(before, rel=0.02, abs=0.05)


def test_cli_accepts_negative_bpm_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Click normally treats ``-3`` as an option; tune must accept it as AMOUNT."""
    from click.testing import CliRunner

    from track_manager.cli import cli

    calls: list[dict] = []

    def fake_resolve(track: str, *, absolute: bool, library_dir: Path) -> Path:
        return library_dir / "Artist - Song.aiff"

    def fake_tune(src: Path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append({"src": src, **kwargs})
        return src

    monkeypatch.setattr("track_manager.library.resolve_track", fake_resolve)
    monkeypatch.setattr("track_manager.tune.tune_track", fake_tune)

    class _Cfg:
        output_dir = Path("/tmp/tm-lib")

    monkeypatch.setattr("track_manager.cli.Config", lambda: _Cfg())

    result = CliRunner().invoke(cli, ["tune", "song", "-3", "-n"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["bpm_percent"] == -3.0
    assert calls[0]["cents"] == pytest.approx(tm_audio.bpm_percent_to_cents(-3.0))
    assert calls[0]["dry_run"] is True
    assert "output_dir" not in calls[0]
