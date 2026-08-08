"""Tests for tuning estimation and shared library track resolution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from track_manager import check_tuning as tm_check
from track_manager import library as tm_library


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


def test_resolve_track_absolute(tmp_path: Path) -> None:
    path = tmp_path / "track.aiff"
    path.write_bytes(b"x")
    got = tm_library.resolve_track(str(path), absolute=True, library_dir=tmp_path)
    assert got == path.resolve()


def test_random_offset_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "track_manager.check_tuning.tm_audio.probe_audio",
        lambda _p: {"duration_seconds": 200.0},
    )
    monkeypatch.setattr(
        "track_manager.check_tuning.tm_deps.ensure_ffmpeg_available",
        lambda: ("ffmpeg", "ffprobe"),
    )

    captured: dict[str, float] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        # ffmpeg ... -ss OFFSET -t DUR -i ...
        ss_idx = cmd.index("-ss")
        captured["offset"] = float(cmd[ss_idx + 1])
        # 2 seconds of silence-free audio at 22050
        import numpy as np

        class _R:
            stdout = (np.ones(22050 * 2, dtype=np.float32) * 0.1).tobytes()

        return _R()

    monkeypatch.setattr("track_manager.check_tuning.subprocess.run", fake_run)
    monkeypatch.setattr("track_manager.check_tuning.random.uniform", lambda a, b: 77.5)

    y, sr, offset_used = tm_check.load_analysis_audio(
        Path("dummy.aiff"), duration=45.0, random_offset=True
    )
    assert sr == 22050
    assert offset_used == pytest.approx(77.5)
    assert captured["offset"] == pytest.approx(77.5)
    assert y.size == 22050 * 2


def test_cents_to_bpm_percent_roundtrip() -> None:
    from track_manager import audio as tm_audio

    for pct in (2.2, -2.2, 5.946309, -5.946309, 0.0):
        cents = tm_audio.bpm_percent_to_cents(pct)
        assert tm_audio.cents_to_bpm_percent(cents) == pytest.approx(pct, abs=1e-6)


def test_adjacent_key_cents() -> None:
    to_a440, to_other = tm_check.adjacent_key_cents(6.0)
    assert to_a440 == pytest.approx(-6.0)
    assert to_other == pytest.approx(94.0)

    to_a440, to_other = tm_check.adjacent_key_cents(-12.0)
    assert to_a440 == pytest.approx(12.0)
    assert to_other == pytest.approx(-88.0)


def test_parse_key_label_camelot_and_names() -> None:
    assert tm_check.parse_key_label("11B") == (9, "major")  # A major
    assert tm_check.parse_key_label("12A") == (1, "minor")  # C# minor
    assert tm_check.parse_key_label("Am") == (9, "minor")
    assert tm_check.parse_key_label("A minor") == (9, "minor")


def test_format_and_transpose_key() -> None:
    assert tm_check.format_key(9, "minor") == "Am (8A)"  # A=9
    assert tm_check.format_key(0, "major") == "C (8B)"
    assert tm_check.transpose_key(9, "minor", 1) == "A#m (3A)"
    assert tm_check.semitones_for_cents_delta(60.0) == 1
    assert tm_check.semitones_for_cents_delta(-40.0) == 0
    assert tm_check.key_after_cents_delta(9, "minor", 60.0) == "A#m (3A)"
    assert tm_check.key_after_cents_delta(9, "minor", -40.0) == "Am (8A)"


def test_report_tuning_includes_rekordbox_bpm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    est = tm_check.TuningEstimate(
        path=Path("/tmp/demo.aiff"),
        cents=6.0,
        sample_rate=22050,
        analysis_offset_seconds=30.0,
        analysis_duration_seconds=45.0,
        key_root=9,
        key_mode="minor",
        key_source="estimated",
        estimated_key_root=9,
        estimated_key_mode="minor",
        tagged_key_root=9,
        tagged_key_mode="major",
    )
    tm_check.report_tuning(est, threshold_cents=5.0)
    out = capsys.readouterr().out
    assert "Rekordbox" in out
    assert "decrease BPM/pitch" in out
    assert "Key now: Am (8A)  (estimated)" in out
    assert "Differs from tag: A (11B)" in out
    assert "→ Am (8A)" in out
    assert "→ A440" not in out
    assert "neighbouring key" not in out
    assert out.count("BPM/pitch") == 1


def test_report_tuning_both_when_over_30(
    capsys: pytest.CaptureFixture[str],
) -> None:
    est = tm_check.TuningEstimate(
        path=Path("/tmp/demo.aiff"),
        cents=40.0,
        sample_rate=22050,
        analysis_offset_seconds=0.0,
        analysis_duration_seconds=45.0,
        key_root=9,
        key_mode="minor",
        key_source="tag",
        tagged_key_root=9,
        tagged_key_mode="minor",
        estimated_key_root=9,
        estimated_key_mode="minor",
    )
    tm_check.report_tuning(est, threshold_cents=5.0)
    out = capsys.readouterr().out
    assert "both paths" not in out
    assert "→ A440" not in out
    assert "increase BPM/pitch 3.53%" in out
    assert "decrease BPM/pitch 2.34%" in out
    assert "→ A#m (3A)" in out  # +60¢ → +1 semitone
    assert "→ Am (8A)" in out  # -40¢ → same key
    assert "To correct toward a key:" in out
    assert "tm tune '/tmp/demo.aiff' 60.0 -c -a" in out
    assert "tm tune '/tmp/demo.aiff' -40.0 -c -a" in out
    # Keys stay on Rekordbox lines only — commands stay copy-paste clean
    assert "tm tune '/tmp/demo.aiff' 60.0 -c -a  →" not in out


def _write_sine_aiff(path: Path, *, freq_hz: float, duration: float = 3.0) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq_hz}:duration={duration}",
            "-c:a",
            "pcm_s16be",
            "-ar",
            "44100",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_estimate_tuning_cents_a440(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    src = tmp_path / "a440.aiff"
    _write_sine_aiff(src, freq_hz=440.0)
    est = tm_check.estimate_tuning_cents(src, duration=2.5, offset=0.0)
    assert est.abs_cents < 5.0


def test_estimate_tuning_cents_sharp(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    # +20 cents above A440
    freq = 440.0 * (2.0 ** (20.0 / 1200.0))
    src = tmp_path / "sharp.aiff"
    _write_sine_aiff(src, freq_hz=freq)
    est = tm_check.estimate_tuning_cents(src, duration=2.5, offset=0.0)
    assert est.cents == pytest.approx(20.0, abs=8.0)
    assert est.correction_cents == pytest.approx(-est.cents)


def test_key_scope_window_uses_analysis_slice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("librosa")
    src = tmp_path / "a440.aiff"
    _write_sine_aiff(src, freq_hz=440.0, duration=3.0)

    calls: list[str] = []

    real_estimate_key = tm_check.estimate_key
    real_estimate_key_stable = tm_check.estimate_key_stable

    def wrap_key(y, sr, *, tuning_cents=0.0):  # type: ignore[no-untyped-def]
        calls.append("window")
        return real_estimate_key(y, sr, tuning_cents=tuning_cents)

    def wrap_stable(path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("stable")
        return real_estimate_key_stable(path, **kwargs)

    monkeypatch.setattr(tm_check, "estimate_key", wrap_key)
    monkeypatch.setattr(tm_check, "estimate_key_stable", wrap_stable)

    est = tm_check.estimate_tuning_cents(
        src, duration=2.5, offset=0.0, key_scope="window", key_source="estimated"
    )
    assert calls == ["window"]
    assert est.estimated_key_scope == "window"

    calls.clear()
    est = tm_check.estimate_tuning_cents(
        src, duration=2.5, offset=0.0, key_scope="stable", key_source="estimated"
    )
    assert calls == ["stable"]
    assert est.estimated_key_scope == "stable"


def test_estimate_tuning_requires_librosa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "x.aiff"
    src.write_bytes(b"x")

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "librosa" or name.startswith("librosa."):
            raise ImportError("simulated missing librosa")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(tm_check.TuningAnalysisError, match="librosa"):
        tm_check.estimate_tuning_cents(src)
