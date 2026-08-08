"""Tests for bar-boundary pad math."""

from __future__ import annotations

import pytest

from track_manager import pad as tm_pad


def _grid_at_120(*, n_beats: int = 32, first_beat: int = 1, first_time: float = 0.0):
    """Build a constant 120 BPM grid (0.5s/beat)."""
    beat_dur = 0.5
    times = []
    beats = []
    bpms = []
    beat = first_beat
    t = first_time
    for _ in range(n_beats):
        times.append(t)
        beats.append(beat)
        bpms.append(120.0)
        t += beat_dur
        beat = beat % 4 + 1
    return times, beats, bpms, beat_dur


def test_phase_on_the_one() -> None:
    times, beats, bpms, beat_dur = _grid_at_120()
    phase = tm_pad.phase_at_time(0.0, times=times, beats=beats, beat_duration=beat_dur)
    assert phase == pytest.approx(0.0)


def test_phase_mid_bar() -> None:
    # First tick is beat 1 at t=0; at 1.25s → 2.5 beats → on the "3.5"
    times, beats, bpms, beat_dur = _grid_at_120()
    phase = tm_pad.phase_at_time(1.25, times=times, beats=beats, beat_duration=beat_dur)
    assert phase == pytest.approx(2.5)


def test_compute_no_pad_when_aligned() -> None:
    times, beats, bpms, _ = _grid_at_120(n_beats=16)
    # 16 beats = exactly 4 bars at 120 BPM
    plan = tm_pad.compute_pad_plan(
        duration_seconds=8.0,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.pad_start_seconds == 0.0
    assert plan.pad_end_seconds == 0.0
    assert not plan.needs_pad


def test_compute_end_pad_past_the_three() -> None:
    times, beats, bpms, beat_dur = _grid_at_120(n_beats=20)
    # 8.0s = exactly on a 1; add 1.25s → end on beat phase 2.5 (the 3.5)
    duration = 8.0 + 1.25
    plan = tm_pad.compute_pad_plan(
        duration_seconds=duration,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.phase_end == pytest.approx(2.5)
    assert plan.pad_start_seconds == 0.0
    # Pad forward 1.5 beats to next 1
    assert plan.pad_end_seconds == pytest.approx(1.5 * beat_dur)


def test_compute_end_no_pad_before_threshold() -> None:
    times, beats, bpms, _ = _grid_at_120(n_beats=20)
    # End 0.75 beats after a 1 → phase 0.75 (still on the "1") — below 3
    duration = 8.0 + 0.75 * 0.5
    plan = tm_pad.compute_pad_plan(
        duration_seconds=duration,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.phase_end == pytest.approx(0.75)
    assert plan.pad_end_seconds == 0.0


def test_compute_start_pad_when_file_opens_on_three() -> None:
    # First grid tick is beat 3 at t=0 → phase 2.0 at start → pad 2 beats
    times, beats, bpms, beat_dur = _grid_at_120(first_beat=3, first_time=0.0)
    plan = tm_pad.compute_pad_plan(
        duration_seconds=10.0,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.phase_start == pytest.approx(2.0)
    assert plan.pad_start_seconds == pytest.approx(2.0 * beat_dur)


def test_compute_start_no_pad_when_opens_on_two() -> None:
    times, beats, bpms, _ = _grid_at_120(first_beat=2, first_time=0.0)
    plan = tm_pad.compute_pad_plan(
        duration_seconds=10.0,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.phase_start == pytest.approx(1.0)
    assert plan.pad_start_seconds == 0.0


def test_compute_start_no_pad_when_one_is_slightly_in() -> None:
    # First "1" at 0.1s with 0.5s beats → 0.2 beats before the 1 → phase 3.8
    # That's "almost on the 1", so we must NOT pad nearly a full empty bar.
    times, beats, bpms, _ = _grid_at_120(first_beat=1, first_time=0.1)
    plan = tm_pad.compute_pad_plan(
        duration_seconds=10.0,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.phase_start == pytest.approx(3.8)
    assert plan.pad_start_seconds == 0.0


def test_compute_start_pad_when_opens_on_four() -> None:
    times, beats, bpms, beat_dur = _grid_at_120(first_beat=4, first_time=0.0)
    plan = tm_pad.compute_pad_plan(
        duration_seconds=10.0,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=3,
    )
    assert plan.phase_start == pytest.approx(3.0)
    assert plan.pad_start_seconds == pytest.approx(3.0 * beat_dur)


def test_format_phase_wraps_near_one() -> None:
    assert tm_pad.format_phase(0.0) == "the 1"
    assert tm_pad.format_phase(2.0) == "the 3"
    assert tm_pad.format_phase(2.5) == "the 3.5"
    # 0.12 before next 1 — never show "4.88"
    assert tm_pad.format_phase(3.88) == "0.12 before the 1"


def test_pad_label_roundtrip() -> None:
    from track_manager import audio as tm_audio

    label = tm_audio.format_pad_label(pad_start_seconds=2.026, pad_end_seconds=0.0868)
    assert label == "start=2026.0ms end=86.8ms"
    parsed = tm_audio.parse_pad_label(label)
    assert parsed is not None
    assert parsed[0] == pytest.approx(2.026)
    assert parsed[1] == pytest.approx(0.0868)


def test_parse_pad_label_rejects_garbage() -> None:
    from track_manager import audio as tm_audio

    assert tm_audio.parse_pad_label("") is None
    assert tm_audio.parse_pad_label("tuned +2%") is None


def test_threshold_four_only_pads_on_four() -> None:
    times, beats, bpms, beat_dur = _grid_at_120(first_beat=3, first_time=0.0)
    plan = tm_pad.compute_pad_plan(
        duration_seconds=10.0,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=4,
    )
    # On the 3 → below threshold 4
    assert plan.pad_start_seconds == 0.0

    times4, beats4, bpms4, beat_dur4 = _grid_at_120(first_beat=4, first_time=0.0)
    plan4 = tm_pad.compute_pad_plan(
        duration_seconds=10.0,
        times=times4,
        beats=beats4,
        bpms=bpms4,
        threshold_beat=4,
    )
    assert plan4.pad_start_seconds == pytest.approx(3.0 * beat_dur4)


def test_build_pad_end_reverb_filter_complex_is_pad_only() -> None:
    from track_manager import audio as tm_audio

    fc = tm_audio.build_pad_end_reverb_filter_complex(
        orig_duration=10.0,
        pad_start_seconds=0.0,
        pad_end_seconds=0.5,
        channels=2,
    )
    assert "asplit=2" in fc
    assert "concat=n=2:v=0:a=1[out]" in fc
    assert "aecho=" in fc
    # Body path is dry (no afade/aecho on [body]).
    assert "[bodyin]anull[body]" in fc
    # Keep only the pad region after the source slice (default 0.40s).
    assert "atrim=start=0.4000000000:duration=0.5000000000" in fc
    assert "atrim=start=9.6000000000:end=10.0000000000" in fc


def test_build_pad_end_reverb_filter_complex_with_start_pad() -> None:
    from track_manager import audio as tm_audio

    fc = tm_audio.build_pad_end_reverb_filter_complex(
        orig_duration=8.0,
        pad_start_seconds=0.25,
        pad_end_seconds=0.4,
        channels=2,
    )
    assert "adelay=" in fc
    assert "[bodyin]adelay=" in fc
    assert "atrim=start=7.6000000000:end=8.0000000000" in fc
