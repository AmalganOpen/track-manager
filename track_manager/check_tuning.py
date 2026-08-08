"""Estimate global pitch tuning offset for a single track vs A440."""

from __future__ import annotations

import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from . import audio as tm_audio
from . import deps as tm_deps

# Pitch-class index 0 = C … 11 = B (librosa chroma convention).
_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Alternate spellings accepted when parsing tags.
_PITCH_CLASS_ALIASES = {
    "DB": 1,
    "EB": 3,
    "GB": 6,
    "AB": 8,
    "BB": 10,
    "C#": 1,
    "D#": 3,
    "F#": 6,
    "G#": 8,
    "A#": 10,
}

# Camelot codes for major (B) / minor (A), indexed by pitch class.
_CAMELOT_MAJOR = [
    "8B",
    "3B",
    "10B",
    "5B",
    "12B",
    "7B",
    "2B",
    "9B",
    "4B",
    "11B",
    "6B",
    "1B",
]
_CAMELOT_MINOR = [
    "5A",
    "12A",
    "7A",
    "2A",
    "9A",
    "4A",
    "11A",
    "6A",
    "1A",
    "8A",
    "3A",
    "10A",
]
_CAMELOT_TO_KEY = {
    **{code: (i, "major") for i, code in enumerate(_CAMELOT_MAJOR)},
    **{code: (i, "minor") for i, code in enumerate(_CAMELOT_MINOR)},
}

# Krumhansl-Schmuckler key profiles (C-rooted).
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


class TuningAnalysisError(RuntimeError):
    """Raised when tuning cannot be estimated."""


@dataclass(frozen=True)
class TuningEstimate:
    """Result of a global tuning analysis relative to A440 equal temperament."""

    path: Path
    cents: float
    sample_rate: int
    analysis_offset_seconds: float
    analysis_duration_seconds: float
    key_root: Optional[int] = None  # primary key used for projections
    key_mode: Optional[str] = None  # "major" | "minor"
    key_source: Optional[str] = None  # "tag" | "estimated"
    tagged_key_root: Optional[int] = None
    tagged_key_mode: Optional[str] = None
    estimated_key_root: Optional[int] = None
    estimated_key_mode: Optional[str] = None
    # How the estimated key was derived: "stable" (multi-window) or "window"
    # (same slice as cents analysis). None when no estimate was produced.
    estimated_key_scope: Optional[str] = None

    @property
    def abs_cents(self) -> float:
        return abs(self.cents)

    @property
    def key_label(self) -> Optional[str]:
        if self.key_root is None or self.key_mode is None:
            return None
        return format_key(self.key_root, self.key_mode)

    @property
    def tagged_key_label(self) -> Optional[str]:
        if self.tagged_key_root is None or self.tagged_key_mode is None:
            return None
        return format_key(self.tagged_key_root, self.tagged_key_mode)

    @property
    def estimated_key_label(self) -> Optional[str]:
        if self.estimated_key_root is None or self.estimated_key_mode is None:
            return None
        return format_key(self.estimated_key_root, self.estimated_key_mode)

    def verdict(self, *, threshold_cents: float = 5.0) -> str:
        """Human label: in tune / slightly off / off."""
        mag = self.abs_cents
        if mag < threshold_cents:
            return "in tune"
        if mag < threshold_cents * 3:
            return "slightly off"
        return "off"

    @property
    def correction_cents(self) -> float:
        """Cents to apply with ``tm tune … -c`` to pull toward A440."""
        return -self.cents


def format_key(root: int, mode: str) -> str:
    """Format a key as ``Am`` / ``C`` plus Camelot, e.g. ``Am (8A)``."""
    pc = _PITCH_CLASSES[root % 12]
    if mode == "minor":
        label = f"{pc}m"
        camelot = _CAMELOT_MINOR[root % 12]
    else:
        label = pc
        camelot = _CAMELOT_MAJOR[root % 12]
    return f"{label} ({camelot})"


def parse_key_label(text: str) -> Optional[tuple[int, str]]:
    """Parse Camelot (``11B``) or note names (``Am``, ``A minor``, ``C#``) → (root, mode)."""
    raw = text.strip()
    if not raw:
        return None

    camelot = re.fullmatch(r"\s*(\d{1,2})\s*([ABab])\s*", raw)
    if camelot:
        code = f"{int(camelot.group(1))}{camelot.group(2).upper()}"
        return _CAMELOT_TO_KEY.get(code)

    cleaned = re.sub(r"\s+", " ", raw).strip()
    lower = cleaned.lower()
    mode = "major"
    note = cleaned
    if lower.endswith(" minor") or lower.endswith(" min"):
        mode = "minor"
        note = cleaned.rsplit(" ", 1)[0]
    elif lower.endswith(" major") or lower.endswith(" maj"):
        mode = "major"
        note = cleaned.rsplit(" ", 1)[0]
    elif cleaned.endswith("m") and not cleaned.endswith("M") and len(cleaned) <= 3:
        # Am, C#m, Bbm
        mode = "minor"
        note = cleaned[:-1]

    note_u = note.strip().upper().replace("♯", "#").replace("♭", "B")
    if len(note_u) >= 2 and note_u.endswith("B") and note_u[0] in "ACDFG":
        aliased = _PITCH_CLASS_ALIASES.get(note_u)
        if aliased is not None:
            return aliased, mode
    if note_u in _PITCH_CLASS_ALIASES:
        return _PITCH_CLASS_ALIASES[note_u], mode
    for i, name in enumerate(_PITCH_CLASSES):
        if note_u == name.upper():
            return i, mode
    return None


def transpose_key(root: int, mode: str, semitones: int) -> str:
    """Return formatted key after a pitch shift of `semitones`."""
    return format_key((root + semitones) % 12, mode)


def semitones_for_cents_delta(cents_delta: float) -> int:
    """Nearest whole-semitone key change implied by a cents pitch shift."""
    return int(round(cents_delta / 100.0))


def key_after_cents_delta(root: int, mode: str, cents_delta: float) -> str:
    """Formatted key after applying `cents_delta` of pitch shift."""
    return transpose_key(root, mode, semitones_for_cents_delta(cents_delta))


def estimate_key(
    y: np.ndarray,
    sr: int,
    *,
    tuning_cents: float = 0.0,
) -> tuple[int, str]:
    """Estimate musical key via chroma + Krumhansl-Schmuckler profiles.

    Returns ``(root_index, mode)`` with root 0=C … 11=B and mode
    ``\"major\"`` or ``\"minor\"``.
    """
    chroma_mean = _chroma_mean(y, sr, tuning_cents=tuning_cents)
    return _key_from_chroma_mean(chroma_mean)


def _chroma_mean(y: np.ndarray, sr: int, *, tuning_cents: float = 0.0) -> np.ndarray:
    librosa = _ensure_librosa()
    tuning = tuning_cents / 100.0
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, tuning=tuning)
    chroma_mean = np.mean(chroma, axis=1)
    norm = float(np.linalg.norm(chroma_mean))
    if norm < 1e-9:
        raise TuningAnalysisError("Chroma too weak for key estimation")
    return chroma_mean / norm


def _key_from_chroma_mean(chroma_mean: np.ndarray) -> tuple[int, str]:
    best_score = -np.inf
    best_root = 0
    best_mode = "major"
    for mode, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
        profile_n = profile / float(np.linalg.norm(profile))
        for root in range(12):
            score = float(np.dot(chroma_mean, np.roll(profile_n, root)))
            if score > best_score:
                best_score = score
                best_root = root
                best_mode = mode
    return best_root, best_mode


def estimate_key_stable(
    path: Path,
    *,
    sr: int = 22050,
    tuning_cents: float = 0.0,
    window_duration: float = 30.0,
    fractions: tuple[float, ...] = (0.15, 0.40, 0.65),
) -> tuple[int, str]:
    """Estimate key from several fixed windows averaged together.

    Single short slices often flip between related keys (relative/fifth) when
    the arrangement emphasises different harmony. Averaging chroma across the
    track is much more stable and independent of ``-r`` / ``--offset``.
    """
    probed = tm_audio.probe_audio(path)
    try:
        total = float(probed.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        total = 0.0

    max_start = max(0.0, total - window_duration) if total > 0 else 0.0
    offsets: list[float] = []
    for frac in fractions:
        off = min(max(0.0, total * frac), max_start)
        # Dedupe near-identical offsets on short tracks.
        if not offsets or abs(off - offsets[-1]) > 1.0:
            offsets.append(off)
    if not offsets:
        offsets = [0.0]

    chromas: list[np.ndarray] = []
    for off in offsets:
        try:
            y, sample_rate, _ = load_analysis_audio(
                path, sr=sr, duration=window_duration, offset=off
            )
            chromas.append(_chroma_mean(y, sample_rate, tuning_cents=tuning_cents))
        except TuningAnalysisError:
            continue

    if not chromas:
        raise TuningAnalysisError("Could not estimate musical key from audio")

    mean = np.mean(np.stack(chromas, axis=0), axis=0)
    norm = float(np.linalg.norm(mean))
    if norm < 1e-9:
        raise TuningAnalysisError("Chroma too weak for key estimation")
    return _key_from_chroma_mean(mean / norm)


def _ensure_librosa():
    try:
        import librosa  # noqa: F401
    except ImportError as e:
        raise TuningAnalysisError(
            "librosa is required for check-tuning. "
            "Reinstall track-manager (or: pip install librosa)"
        ) from e
    import librosa

    return librosa


def load_analysis_audio(
    path: Path,
    *,
    sr: int = 22050,
    duration: float = 45.0,
    offset: Optional[float] = None,
    random_offset: bool = False,
) -> tuple[np.ndarray, int, float]:
    """Decode a mono float32 window via ffmpeg for tuning analysis.

    Returns ``(samples, sample_rate, offset_used)``.

    Offset selection when `offset` is None:
    - ``random_offset=True``: uniform random start in ``[0, max(0, total-duration)]``
    - otherwise: past typical intros (≈15% in, capped at 30s)
    """
    tm_deps.ensure_ffmpeg_available()

    probed = tm_audio.probe_audio(path)
    total = probed.get("duration_seconds")
    try:
        total_f = float(total) if total is not None else 0.0
    except (TypeError, ValueError):
        total_f = 0.0

    max_start = max(0.0, total_f - duration) if total_f > 0 else 0.0

    if offset is not None:
        offset_used = max(0.0, float(offset))
        if max_start > 0:
            offset_used = min(offset_used, max_start)
    elif random_offset:
        offset_used = random.uniform(0.0, max_start) if max_start > 0 else 0.0
    elif total_f > duration + 10:
        offset_used = min(30.0, total_f * 0.15)
    else:
        offset_used = 0.0

    cmd = [
        "ffmpeg",
        "-ss",
        f"{offset_used:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        tm_audio.ffmpeg_arg_path(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-v",
        "error",
        "-",
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        raise TuningAnalysisError(
            f"Failed to decode audio for tuning analysis: {err[-300:]}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise TuningAnalysisError("Timed out decoding audio for tuning analysis") from e

    if not out.stdout:
        raise TuningAnalysisError("No audio decoded for tuning analysis")

    y = np.frombuffer(out.stdout, dtype=np.float32).copy()
    if y.size < sr:  # less than ~1s
        raise TuningAnalysisError(
            "Decoded audio too short for reliable tuning estimation"
        )
    # Guard against clipping / silence dominating estimate_tuning
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak < 1e-6:
        raise TuningAnalysisError("Decoded audio is silent")

    return y, sr, offset_used


def estimate_tuning_cents(
    path: Path,
    *,
    sr: int = 22050,
    duration: float = 45.0,
    offset: Optional[float] = None,
    random_offset: bool = False,
    bins_per_octave: int = 12,
    key_source: str = "auto",
    key_scope: str = "stable",
) -> TuningEstimate:
    """Estimate global tuning offset of `path` in cents vs A440.

    Positive = sharp, negative = flat. Uses librosa's ``estimate_tuning``.

    `key_source` selects which key drives target-key projections:
    - ``auto``: prefer file tag (Rekordbox TKEY) when present, else estimate
    - ``tag``: require/use the file tag
    - ``estimated``: always use chroma estimate (ignore tag for projections)

    `key_scope` selects where estimated chroma/key comes from:
    - ``stable``: average several fixed windows across the track (default)
    - ``window``: use the same ``--offset`` / ``--duration`` / ``-r`` slice
      as the cents analysis (useful for checking a breakdown/drop alone)
    """
    if key_source not in ("auto", "tag", "estimated"):
        raise ValueError("key_source must be 'auto', 'tag', or 'estimated'")
    if key_scope not in ("stable", "window"):
        raise ValueError("key_scope must be 'stable' or 'window'")

    librosa = _ensure_librosa()
    y, sample_rate, offset_used = load_analysis_audio(
        path,
        sr=sr,
        duration=duration,
        offset=offset,
        random_offset=random_offset,
    )

    try:
        tuning = librosa.estimate_tuning(
            y=y, sr=sample_rate, bins_per_octave=bins_per_octave
        )
    except Exception as e:
        raise TuningAnalysisError(f"Tuning estimation failed: {e}") from e

    cents = float(tuning) * (1200.0 / float(bins_per_octave))

    tagged = read_key_tag(path)
    estimated: Optional[tuple[int, str]] = None
    estimated_scope: Optional[str] = None
    try:
        if key_scope == "window":
            estimated = estimate_key(y, sample_rate, tuning_cents=cents)
            estimated_scope = "window"
        else:
            # Multi-window average — independent of the cents/-r slice — so
            # arrangement changes don't look like key changes.
            estimated = estimate_key_stable(path, sr=sample_rate, tuning_cents=cents)
            estimated_scope = "stable"
    except TuningAnalysisError:
        pass
    except Exception:
        pass

    tagged_root = tagged[0] if tagged else None
    tagged_mode = tagged[1] if tagged else None
    estimated_root = estimated[0] if estimated else None
    estimated_mode = estimated[1] if estimated else None

    key_root: Optional[int] = None
    key_mode: Optional[str] = None
    chosen_source: Optional[str] = None

    if key_source == "tag":
        if tagged is None:
            raise TuningAnalysisError(
                "No key tag (TKEY) found on file; "
                "omit --key-source tag or use --key-source estimated"
            )
        key_root, key_mode = tagged
        chosen_source = "tag"
    elif key_source == "estimated":
        if estimated is None:
            raise TuningAnalysisError("Could not estimate musical key from audio")
        key_root, key_mode = estimated
        chosen_source = "estimated"
    else:  # auto
        if tagged is not None:
            key_root, key_mode = tagged
            chosen_source = "tag"
        elif estimated is not None:
            key_root, key_mode = estimated
            chosen_source = "estimated"

    return TuningEstimate(
        path=path,
        cents=cents,
        sample_rate=sample_rate,
        analysis_offset_seconds=offset_used,
        analysis_duration_seconds=min(duration, len(y) / float(sample_rate)),
        key_root=key_root,
        key_mode=key_mode,
        key_source=chosen_source,
        tagged_key_root=tagged_root,
        tagged_key_mode=tagged_mode,
        estimated_key_root=estimated_root,
        estimated_key_mode=estimated_mode,
        estimated_key_scope=estimated_scope,
    )


def read_key_tag(path: Path) -> Optional[tuple[int, str]]:
    """Read Rekordbox/player key from TKEY / ©key if present."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".aiff", ".aif", ".mp3"):
            from mutagen.aiff import AIFF
            from mutagen.mp3 import MP3

            audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
            if not audio.tags:
                return None
            frames = audio.tags.getall("TKEY")
            if frames and frames[0].text:
                return parse_key_label(str(frames[0].text[0]))
            return None
        if suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            tags = MP4(str(path)).tags
            if not tags:
                return None
            # iTunes / some DJ tools
            for atom in (
                "\xa9key",
                "----:com.apple.iTunes:initialkey",
                "----:com.apple.iTunes:KEY",
            ):
                raw = tags.get(atom)
                if not raw:
                    continue
                payload = raw[0] if isinstance(raw, list) else raw
                if isinstance(payload, bytes):
                    text = payload.decode("utf-8", errors="replace")
                else:
                    text = str(payload)
                parsed = parse_key_label(text)
                if parsed:
                    return parsed
            return None
    except Exception:
        return None
    return None


def read_recorded_tuning_label(path: Path) -> Optional[str]:
    """Return prior ``TM_TUNING`` tag text if present."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".aiff", ".aif", ".mp3"):
            from mutagen.aiff import AIFF
            from mutagen.mp3 import MP3

            audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
            if not audio.tags:
                return None
            for frame in audio.tags.getall("TXXX"):
                if getattr(frame, "desc", None) == "TM_TUNING" and frame.text:
                    return str(frame.text[0])
            return None
        if suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            tags = MP4(str(path)).tags
            if not tags:
                return None
            raw = tags.get("----:com.tm:tuning")
            if not raw:
                return None
            payload = raw[0] if isinstance(raw, list) else raw
            if isinstance(payload, bytes):
                return payload.decode("utf-8", errors="replace")
            return str(payload)
    except Exception:
        return None
    return None


def adjacent_key_cents(offset_cents: float) -> tuple[float, float]:
    """Cents deltas from current pitch to the two adjacent ET pitch classes.

    `offset_cents` is the measured offset vs A440's nearest pitch class
    (typically in ~[-50, 50]). Returns ``(to_reference, to_other)``:

    - ``to_reference``: land on the ET pitch librosa referenced (``-offset``)
    - ``to_other``: land on the neighbouring semitone the other way
    """
    to_reference = -offset_cents
    if offset_cents >= 0:
        to_other = 100.0 - offset_cents
    else:
        to_other = -100.0 - offset_cents
    return to_reference, to_other


def _format_rekordbox_bpm_line(
    cents_delta: float, *, target_key: Optional[str] = None
) -> str:
    """One Rekordbox trial line: increase/decrease BPM% for a cents delta."""
    bpm_pct = tm_audio.cents_to_bpm_percent(cents_delta)
    if bpm_pct >= 0:
        action = f"increase BPM/pitch {bpm_pct:.2f}%"
    else:
        action = f"decrease BPM/pitch {abs(bpm_pct):.2f}%"
    line = f"{action}  ({cents_delta:+.1f}¢)"
    if target_key:
        line = f"{line}  → {target_key}"
    return line


def _target_key_for_delta(
    estimate: TuningEstimate, cents_delta: float
) -> Optional[str]:
    if estimate.key_root is None or estimate.key_mode is None:
        return None
    return key_after_cents_delta(estimate.key_root, estimate.key_mode, cents_delta)


def report_tuning(estimate: TuningEstimate, *, threshold_cents: float = 5.0) -> None:
    """Print a CLI-style tuning report."""
    path = estimate.path
    print(f"🎵 Track: {path.name}")
    recorded = read_recorded_tuning_label(path)
    if recorded:
        print(f"ℹ️ Recorded TM_TUNING: {recorded}")

    print(
        f"🔍 Analysed {estimate.analysis_duration_seconds:.0f}s "
        f"from {estimate.analysis_offset_seconds:.1f}s "
        f"@ {estimate.sample_rate} Hz"
    )

    cents = estimate.cents
    verdict = estimate.verdict(threshold_cents=threshold_cents)
    direction = "sharp" if cents > 0 else "flat" if cents < 0 else "on pitch"
    print(f"🎚️ Tuning: {cents:+.1f} cents ({direction})")
    if estimate.key_label:
        src_note = estimate.key_source or "estimated"
        if (
            estimate.key_source == "estimated"
            and estimate.estimated_key_scope == "window"
        ):
            src_note = "estimated/window"
        print(f"🎹 Key now: {estimate.key_label}  ({src_note})")
        # Surface disagreement so a wrong Rekordbox tag isn't silently trusted.
        if (
            estimate.tagged_key_label
            and estimate.estimated_key_label
            and estimate.tagged_key_label != estimate.estimated_key_label
        ):
            other = (
                estimate.estimated_key_label
                if estimate.key_source == "tag"
                else estimate.tagged_key_label
            )
            if estimate.key_source == "tag" and estimate.estimated_key_scope:
                other_src = f"estimated/{estimate.estimated_key_scope}"
            elif estimate.key_source == "tag":
                other_src = "estimated"
            else:
                other_src = "tag"
            print(f"⚠️ Differs from {other_src}: {other}")
            if estimate.key_source == "tag":
                print("   (use --key-source estimated if you trust analysis more)")
            else:
                print("   (use --key-source tag to follow Rekordbox/TKEY)")
    print(f"📊 Verdict: {verdict} (threshold ±{threshold_cents:g}¢ vs A440)")

    # Rekordbox pitch fader ≈ BPM% — try before committing to tm tune.
    to_a440, to_other = adjacent_key_cents(cents)
    show_both = abs(to_a440) > 30.0 and abs(to_other) > 30.0
    print("🎛️ Rekordbox (test before tm tune):")
    if show_both:
        deltas = sorted((to_a440, to_other), reverse=True)
        for delta in deltas:
            print(
                f"  {_format_rekordbox_bpm_line(delta, target_key=_target_key_for_delta(estimate, delta))}"
            )
    else:
        print(
            f"  {_format_rekordbox_bpm_line(to_a440, target_key=_target_key_for_delta(estimate, to_a440))}"
        )

    if show_both:
        print("💡 To correct toward a key:")
        for delta in sorted((to_a440, to_other), reverse=True):
            print(f"  tm tune {str(path)!r} {delta:.1f} -c -a")
    elif estimate.abs_cents >= threshold_cents:
        corr = estimate.correction_cents
        print(f"💡 To correct toward A440: " f"tm tune {str(path)!r} {corr:.1f} -c -a")
    else:
        print("✅ No correction needed")
    print()
