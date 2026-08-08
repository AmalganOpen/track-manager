"""Audio encoding, probing, and basic-tag projection.

This module owns the ffmpeg/ffprobe pipeline and the mapping from the
canonical metadata document (see `track_manager.blob`) into player-visible
tags. The blob is the source of truth; `apply_basic_tags` writes a derived
projection that Music.app, DJ software, phones, and car stereos can read.

The functions here are stateless and have no Downloader/Config dependency, so
they can be reused by migration tools, tests, and one-off scripts.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from mutagen.aiff import AIFF
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TPUB,
    TRCK,
    TSRC,
    TXXX,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

SUPPORTED_FORMATS = ("aiff", "m4a", "mp3")
DEFAULT_FORMAT = "aiff"


def _is_ascii_path(path: Path) -> bool:
    """True if `path`'s string form is pure ASCII (safe for Windows ffmpeg)."""
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _windows_short_path(path: Path) -> Optional[str]:
    """Return the 8.3 short path for an *existing* Windows file, or None.

    Many Windows ffmpeg builds choke on non-ASCII argv paths. The Win32
    short-path form is ASCII-only and works as an ffmpeg input. Output
    paths that do not exist yet cannot be shortened — callers must stage
    those under an ASCII name and rename afterward.
    """
    if sys.platform != "win32" or not path.exists():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        long_name = str(path.resolve())
        needed = get_short(long_name, None, 0)
        if needed == 0:
            return None
        buf = ctypes.create_unicode_buffer(needed)
        if get_short(long_name, buf, needed) == 0:
            return None
        short = buf.value
        short.encode("ascii")  # reject if still non-ASCII
        return short
    except (AttributeError, OSError, UnicodeEncodeError):
        return None


def ffmpeg_arg_path(path: Path) -> str:
    """Path string safe to pass to ffmpeg/ffprobe on this platform.

    On Windows, prefer the 8.3 short path when the real path has non-ASCII
    characters (Chinese filenames, etc.). Falls back to ``str(path)`` when
    shortening is unavailable — callers writing *new* files should stage to
    an ASCII name first via ``ascii_staging_path``.
    """
    if sys.platform == "win32" and not _is_ascii_path(path):
        short = _windows_short_path(path)
        if short:
            return short
    return str(path)


def ascii_staging_path(final_path: Path, *, prefix: str = ".tm_enc") -> Path:
    """ASCII-only sibling of `final_path` for ffmpeg output on Windows."""
    import os

    return final_path.with_name(f"{prefix}_{os.getpid()}{final_path.suffix}")


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def probe_audio(path: Path) -> dict[str, Any]:
    """Return audio properties of `path` using ffprobe.

    Always returns a dict; missing fields are None. Never raises.

    Keys: codec, bitrate_kbps, sample_rate, bit_depth, channels,
          duration_seconds, size_bytes.
    """
    info: dict[str, Any] = {
        "codec": None,
        "bitrate_kbps": None,
        "sample_rate": None,
        "bit_depth": None,
        "channels": None,
        "duration_seconds": None,
        "size_bytes": path.stat().st_size if path.exists() else None,
    }

    if not shutil.which("ffprobe"):
        return info

    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,bits_per_raw_sample,bits_per_sample,channels,bit_rate,duration:format=bit_rate,duration",
            "-of",
            "json",
            ffmpeg_arg_path(path),
        ]
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
        data = json.loads(out.stdout)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return info

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    if not streams:
        return info

    s = streams[0]
    info["codec"] = s.get("codec_name")
    if s.get("sample_rate"):
        try:
            info["sample_rate"] = int(s["sample_rate"])
        except (TypeError, ValueError):
            pass
    if s.get("channels") is not None:
        info["channels"] = s["channels"]

    # bit depth: bits_per_raw_sample preferred (e.g. 24 for 24-bit FLAC), else bits_per_sample
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        v = s.get(key)
        if v is not None:
            try:
                bd = int(v)
                if bd > 0:
                    info["bit_depth"] = bd
                    break
            except (TypeError, ValueError):
                pass

    # bitrate: prefer per-stream, fall back to format-level
    for v in (s.get("bit_rate"), fmt.get("bit_rate")):
        if v is None:
            continue
        try:
            info["bitrate_kbps"] = round(int(v) / 1000)
            break
        except (TypeError, ValueError):
            pass

    for v in (s.get("duration"), fmt.get("duration")):
        if v is None:
            continue
        try:
            info["duration_seconds"] = float(v)
            break
        except (TypeError, ValueError):
            pass

    return info


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


class EncodeError(RuntimeError):
    """Raised when an ffmpeg encode fails."""


def encode_to_aiff(src: Path, dst: Path) -> Path:
    """Encode to AIFF (PCM_S16BE, 44.1 kHz stereo)."""
    cmd = [
        "ffmpeg",
        "-i",
        ffmpeg_arg_path(src),
        "-vn",
        "-c:a",
        "pcm_s16be",
        "-ar",
        "44100",
        # Explicit muxer: migration writes to ``*.aiff.tmp`` so the suffix is
        # not ``.aiff`` and ffmpeg cannot infer the output format from the path.
        "-f",
        "aiff",
        "-y",
        ffmpeg_arg_path(dst),
    ]
    _run_ffmpeg(cmd)
    return dst


def encode_to_m4a(src: Path, dst: Path, bitrate_kbps: int = 256) -> Path:
    """Encode to M4A (AAC, default 256 kbps, 48 kHz)."""
    cmd = [
        "ffmpeg",
        "-i",
        ffmpeg_arg_path(src),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        f"{bitrate_kbps}k",
        "-movflags",
        "+faststart",
        "-y",
        ffmpeg_arg_path(dst),
    ]
    _run_ffmpeg(cmd)
    return dst


def encode_to_mp3(src: Path, dst: Path, bitrate_kbps: int = 320) -> Path:
    """Encode to MP3 (libmp3lame CBR, default 320 kbps)."""
    cmd = [
        "ffmpeg",
        "-i",
        ffmpeg_arg_path(src),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{bitrate_kbps}k",
        "-y",
        ffmpeg_arg_path(dst),
    ]
    _run_ffmpeg(cmd)
    return dst


def encode_to(target_format: str, src: Path, dst: Path, **kwargs: Any) -> Path:
    """Dispatch to the right encoder based on `target_format`.

    On Windows, when `dst` contains non-ASCII characters (Chinese titles,
    etc.), encode into an ASCII staging file first and rename — many
    Windows ffmpeg builds reject non-ASCII output paths even though
    pathlib/NTFS handle them fine.
    """
    staging: Optional[Path] = None
    encode_dst = dst
    if sys.platform == "win32" and not _is_ascii_path(dst):
        staging = ascii_staging_path(dst)
        encode_dst = staging

    try:
        if target_format == "aiff":
            encode_to_aiff(src, encode_dst)
        elif target_format == "m4a":
            encode_to_m4a(src, encode_dst, **kwargs)
        elif target_format == "mp3":
            encode_to_mp3(src, encode_dst, **kwargs)
        else:
            raise ValueError(
                f"Unsupported target format: {target_format!r}. "
                f"Expected one of: {', '.join(SUPPORTED_FORMATS)}"
            )

        if staging is not None:
            if dst.exists():
                dst.unlink()
            staging.rename(dst)
        return dst
    except Exception:
        if staging is not None and staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass
        raise


def is_mp4_container(path: Path) -> bool:
    """Return True if `path` parses as an MP4/M4A container.

    Mutagen reads the ftyp atom directly, so the result is independent of
    the file's extension — useful when a source has been written with a
    misleading extension (e.g. AAC bytes saved as `.flac`).
    """
    try:
        from mutagen.mp4 import MP4

        MP4(str(path))
        return True
    except Exception:
        return False


def encode_or_passthrough(target_format: str, src: Path, dst: Path) -> Path:
    """Produce `dst` in `target_format`, avoiding a re-encode when the source
    already carries the target's codec.

    Passthrough cases (lossless, byte-identical when stream copy isn't
    needed):
      - `m4a`: source is AAC or ALAC inside an MP4 container → rename.
      - `mp3`: source codec is mp3 and source extension is `.mp3` → rename.
      - `aiff`: source codec is PCM and source extension is `.aiff`/`.aif`
        → rename. WAV (RIFF) shares PCM codec but a different container,
        so it is re-muxed via ffmpeg even though no re-encode happens.

    Everything else goes through `encode_to`. The source file is removed
    on success in the encode path, and replaced by the rename in the
    passthrough path. Returns `dst`.
    """
    probed = probe_audio(src)
    codec = (probed.get("codec") or "").lower()
    src_suffix = src.suffix.lower()

    can_passthrough = (
        (target_format == "m4a" and codec in ("aac", "alac") and is_mp4_container(src))
        or (target_format == "mp3" and codec == "mp3" and src_suffix == ".mp3")
        or (
            target_format == "aiff"
            and codec.startswith("pcm_s")
            and src_suffix in (".aiff", ".aif")
        )
    )

    if can_passthrough:
        if dst.exists():
            dst.unlink()
        src.rename(dst)
        return dst

    encode_to(target_format, src, dst)
    if src.exists() and src.resolve() != dst.resolve():
        try:
            src.unlink()
        except OSError:
            pass
    return dst


def resolve_format(requested: Optional[str]) -> str:
    """Resolve `requested` to a concrete target format.

    None or 'auto' resolves to the default (AIFF). Anything in
    `SUPPORTED_FORMATS` passes through. Source format does not influence
    this decision: gear/hardware compatibility is the priority and AIFF is
    the only format that satisfies that universally.
    """
    if requested is None or requested == "auto":
        return DEFAULT_FORMAT
    if requested in SUPPORTED_FORMATS:
        return requested
    raise ValueError(
        f"Unsupported format: {requested!r}. "
        f"Expected one of: auto, {', '.join(SUPPORTED_FORMATS)}"
    )


def format_from_path(path: Path) -> str:
    """Map a file suffix to a supported target format name."""
    suffix = path.suffix.lower()
    if suffix in (".aiff", ".aif"):
        return "aiff"
    if suffix in (".m4a", ".mp4"):
        return "m4a"
    if suffix == ".mp3":
        return "mp3"
    raise ValueError(
        f"Unsupported audio format: {suffix!r}. "
        f"Expected one of: {', '.join('.' + f for f in SUPPORTED_FORMATS)}"
    )


# ---------------------------------------------------------------------------
# Pitch / tune helpers
# ---------------------------------------------------------------------------


def bpm_percent_to_cents(percent: float) -> float:
    """Convert a BPM pitch percentage to cents (asymmetric / musical).

    Positive and negative percentages use inverse ratios so that ``+p`` and
    ``-p`` cancel out (e.g. ±5.946309% ≈ ±100 cents / one semitone):

    - positive: ``ratio = 1 + p/100``
    - negative: ``ratio = 1 / (1 + |p|/100)``

    Cents are ``1200 * log2(ratio)``.
    """
    if percent >= 0:
        ratio = 1.0 + percent / 100.0
    else:
        ratio = 1.0 / (1.0 + abs(percent) / 100.0)
    if ratio <= 0:
        raise ValueError(f"BPM percent {percent} yields a non-positive rate ratio")
    return 1200.0 * math.log2(ratio)


def cents_to_bpm_percent(cents: float) -> float:
    """Inverse of ``bpm_percent_to_cents`` (Rekordbox-style pitch %)."""
    ratio = cents_to_ratio(cents)
    if cents >= 0:
        return (ratio - 1.0) * 100.0
    # ratio = 1/(1+|p|/100)  →  |p| = 100*(1/ratio - 1)
    return -100.0 * (1.0 / ratio - 1.0)


def cents_to_ratio(cents: float) -> float:
    """Convert cents to a frequency/tempo scale factor."""
    return 2.0 ** (cents / 1200.0)


def _pcm_codec_for_aiff(probed: dict[str, Any]) -> str:
    """Pick an AIFF PCM codec matching the source as closely as possible."""
    codec = (probed.get("codec") or "").lower()
    if codec.startswith("pcm_"):
        return codec
    bit_depth = probed.get("bit_depth")
    try:
        bd = int(bit_depth) if bit_depth is not None else 16
    except (TypeError, ValueError):
        bd = 16
    if bd >= 32:
        return "pcm_s32be"
    if bd >= 24:
        return "pcm_s24be"
    return "pcm_s16be"


def copy_all_tags(src: Path, dst: Path) -> None:
    """Copy every mutagen tag from `src` onto `dst` (same container family).

    Used after a re-encode so the pitched file keeps the original AIFF/M4A/MP3
    tag set (cover, blob GEOB, freeform atoms, etc.) instead of rebuilding
    player-visible tags from scratch.
    """
    src_suffix = src.suffix.lower()
    dst_suffix = dst.suffix.lower()

    if src_suffix in (".aiff", ".aif") and dst_suffix in (".aiff", ".aif"):
        src_audio = AIFF(str(src))
        dst_audio = AIFF(str(dst))
        if src_audio.tags is None:
            return
        if dst_audio.tags is None:
            dst_audio.add_tags()
        else:
            dst_audio.tags.clear()
        assert dst_audio.tags is not None
        for key in list(src_audio.tags.keys()):
            dst_audio.tags.setall(key, src_audio.tags.getall(key))
        dst_audio.save()
        return

    if src_suffix == ".mp3" and dst_suffix == ".mp3":
        src_audio = MP3(str(src))
        dst_audio = MP3(str(dst))
        if src_audio.tags is None:
            return
        if dst_audio.tags is None:
            dst_audio.add_tags()
        else:
            dst_audio.tags.clear()
        assert dst_audio.tags is not None
        for key in list(src_audio.tags.keys()):
            dst_audio.tags.setall(key, src_audio.tags.getall(key))
        dst_audio.save()
        return

    if src_suffix in (".m4a", ".mp4") and dst_suffix in (".m4a", ".mp4"):
        src_audio = MP4(str(src))
        dst_audio = MP4(str(dst))
        if not src_audio.tags:
            return
        if dst_audio.tags is None:
            dst_audio.add_tags()
        else:
            dst_audio.tags.clear()
        assert dst_audio.tags is not None
        for key, value in src_audio.tags.items():
            dst_audio.tags[key] = value
        dst_audio.save()
        return

    raise ValueError(f"Cannot copy tags between {src_suffix!r} and {dst_suffix!r}")


_TUNING_TXXX_DESC = "TM_TUNING"
_TUNING_M4A_ATOM = "----:com.tm:tuning"
_PAD_TXXX_DESC = "TM_PAD"
_PAD_M4A_ATOM = "----:com.tm:pad"


def apply_tuning_tags(path: Path, *, title: str, tuning_label: str) -> None:
    """Write tune metadata into existing tags (title + dedicated tuning field).

    Does not rename the file. Sets player-visible title and a ``TM_TUNING``
    tag (ID3 TXXX / MP4 freeform) so the pitch change is recorded in-container.
    """
    suffix = path.suffix.lower()
    if suffix in (".aiff", ".aif", ".mp3"):
        audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags.delall("TIT2")
        audio.tags.add(TIT2(encoding=3, text=[title]))
        # Replace any prior TM_TUNING frames.
        for frame in list(audio.tags.getall("TXXX")):
            if getattr(frame, "desc", None) == _TUNING_TXXX_DESC:
                audio.tags.delall(frame.HashKey)
        audio.tags.add(TXXX(encoding=3, desc=_TUNING_TXXX_DESC, text=[tuning_label]))
        audio.save()
        return

    if suffix in (".m4a", ".mp4"):
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags["\xa9nam"] = [title]
        audio.tags[_TUNING_M4A_ATOM] = [tuning_label.encode("utf-8")]
        audio.save()
        return

    raise ValueError(f"Unsupported format for tuning tags: {suffix!r}")


def format_pad_label(*, pad_start_seconds: float, pad_end_seconds: float) -> str:
    """Human/machine label for cumulative silence pads (e.g. for ``TM_PAD``)."""
    return (
        f"start={pad_start_seconds * 1000:.1f}ms " f"end={pad_end_seconds * 1000:.1f}ms"
    )


def parse_pad_label(label: str) -> Optional[tuple[float, float]]:
    """Parse ``TM_PAD`` text into ``(pad_start_seconds, pad_end_seconds)``.

    Returns None if the label cannot be parsed.
    """
    text = (label or "").strip()
    if not text:
        return None
    start_m = re.search(r"start\s*=\s*([+-]?\d+(?:\.\d+)?)\s*ms", text, re.I)
    end_m = re.search(r"end\s*=\s*([+-]?\d+(?:\.\d+)?)\s*ms", text, re.I)
    if not start_m or not end_m:
        return None
    try:
        start_ms = float(start_m.group(1))
        end_ms = float(end_m.group(1))
    except ValueError:
        return None
    return (start_ms / 1000.0, end_ms / 1000.0)


def read_recorded_pad(path: Path) -> Optional[tuple[float, float]]:
    """Return cumulative ``(pad_start_seconds, pad_end_seconds)`` from tags, if any.

    Accepts legacy ``TM_EXTEND`` / ``com.tm:extend`` written by earlier builds.
    """
    suffix = path.suffix.lower()
    legacy_desc = "TM_EXTEND"
    legacy_atom = "----:com.tm:extend"
    try:
        if suffix in (".aiff", ".aif", ".mp3"):
            audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
            tags = audio.tags
            if not tags:
                return None
            for frame in tags.getall("TXXX"):
                desc = getattr(frame, "desc", None)
                if desc in (_PAD_TXXX_DESC, legacy_desc) and frame.text:
                    return parse_pad_label(str(frame.text[0]))
            return None
        if suffix in (".m4a", ".mp4"):
            audio = MP4(str(path))
            tags = audio.tags
            if not tags:
                return None
            raw = tags.get(_PAD_M4A_ATOM) or tags.get(legacy_atom)
            if not raw:
                return None
            payload = raw[0] if isinstance(raw, list) else raw
            if isinstance(payload, (bytes, bytearray)):
                text = bytes(payload).decode("utf-8", errors="replace")
            else:
                text = str(payload)
            return parse_pad_label(text)
    except Exception:
        return None
    return None


def apply_pad_tags(path: Path, *, pad_label: str) -> None:
    """Write cumulative pad info as ``TM_PAD`` (does not change title)."""
    suffix = path.suffix.lower()
    if suffix in (".aiff", ".aif", ".mp3"):
        audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        for frame in list(audio.tags.getall("TXXX")):
            if getattr(frame, "desc", None) == _PAD_TXXX_DESC:
                audio.tags.delall(frame.HashKey)
        audio.tags.add(TXXX(encoding=3, desc=_PAD_TXXX_DESC, text=[pad_label]))
        audio.save()
        return

    if suffix in (".m4a", ".mp4"):
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags[_PAD_M4A_ATOM] = [pad_label.encode("utf-8")]
        audio.save()
        return

    raise ValueError(f"Unsupported format for pad tags: {suffix!r}")


def clear_pad_tags(path: Path) -> None:
    """Remove ``TM_PAD`` (and legacy ``TM_EXTEND``) from the file's tags."""
    suffix = path.suffix.lower()
    legacy_desc = "TM_EXTEND"
    legacy_atom = "----:com.tm:extend"
    if suffix in (".aiff", ".aif", ".mp3"):
        audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
        if not audio.tags:
            return
        changed = False
        for frame in list(audio.tags.getall("TXXX")):
            if getattr(frame, "desc", None) in (_PAD_TXXX_DESC, legacy_desc):
                audio.tags.delall(frame.HashKey)
                changed = True
        if changed:
            audio.save()
        return

    if suffix in (".m4a", ".mp4"):
        audio = MP4(str(path))
        if not audio.tags:
            return
        changed = False
        for key in (_PAD_M4A_ATOM, legacy_atom):
            if key in audio.tags:
                del audio.tags[key]
                changed = True
        if changed:
            audio.save()
        return

    raise ValueError(f"Unsupported format for pad tags: {suffix!r}")


_HAS_RUBBERBAND: Optional[bool] = None


def _ffmpeg_has_rubberband() -> bool:
    global _HAS_RUBBERBAND
    if _HAS_RUBBERBAND is not None:
        return _HAS_RUBBERBAND
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
        _HAS_RUBBERBAND = "rubberband" in (out.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        _HAS_RUBBERBAND = False
    return _HAS_RUBBERBAND


def _atempo_chain(tempo: float) -> str:
    """Build one or more ``atempo`` filters covering `tempo` (atempo ∈ [0.5, 2.0])."""
    if tempo <= 0:
        raise ValueError(f"atempo factor must be positive, got {tempo}")
    parts: list[str] = []
    remaining = tempo
    # Keep each stage inside the portable atempo range.
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.10f}")
    return ",".join(parts)


def _pitch_filter(cents: float, sample_rate: int) -> str:
    """Pitch-shift filter that keeps tempo/BPM unchanged.

    Prefers ``rubberband`` (pitch only, tempo=1). Falls back to
    ``asetrate`` + ``aresample`` + compensating ``atempo`` when rubberband
    is unavailable.

    Rubberband defaults to ``pitchq=speed`` (fast/cheaper). We force
    ``pitchq=quality`` and ``channels=together`` (stereo coherence), but
    keep rubberband's own ``transients=crisp`` / ``window=standard`` —
    ``smooth``+``long`` smears kicks/hats and sounds worse on club material
    even though pitch may look "cleaner". Stacking in-place tunes still
    compounds artifacts.
    """
    ratio = cents_to_ratio(cents)
    if _ffmpeg_has_rubberband():
        return (
            f"rubberband=pitch={ratio:.10f}:tempo=1:"
            f"pitchq=quality:transients=crisp:window=standard:"
            f"channels=together"
        )

    # asetrate changes pitch+tempo together; atempo=1/ratio restores duration.
    return (
        f"asetrate={sample_rate * ratio:.10f},"
        f"aresample={sample_rate},"
        f"{_atempo_chain(1.0 / ratio)}"
    )


# End-pad fill: keep the original body untouched; only invent audio in the pad.
EndTailMode = Literal["reverb", "silence"]
PAD_END_TAIL_DEFAULT: EndTailMode = "reverb"
# Ending slice fed into aecho (read-only source for the pad).
_PAD_END_TAIL_SOURCE_SECONDS = 0.40
# Quiet wet level so the pad is glue, not a new outro.
_PAD_END_TAIL_WET = 0.32
# Tiny pad-side fade-in to avoid a click at the join (does not touch body).
_PAD_END_TAIL_FADE_IN_SECONDS = 0.004
# Multi-tap aecho ≈ short room wash (in_gain:out_gain:delays:decays).
_PAD_END_TAIL_AECHO = (
    "0.75:0.35:"
    "20|40|65|95|130|175|230|300|400|520:"
    "0.5|0.42|0.35|0.28|0.22|0.18|0.14|0.1|0.07|0.04"
)


def _adelay_filter(pad_start_seconds: float, channels: Optional[int]) -> str:
    delay_ms = max(1, int(round(pad_start_seconds * 1000.0)))
    if channels and channels > 1:
        delays = "|".join([str(delay_ms)] * channels)
    else:
        delays = str(delay_ms)
    return f"adelay={delays}:all=1"


def build_pad_end_reverb_filter_complex(
    *,
    orig_duration: float,
    pad_start_seconds: float,
    pad_end_seconds: float,
    channels: Optional[int],
    source_seconds: float = _PAD_END_TAIL_SOURCE_SECONDS,
    wet: float = _PAD_END_TAIL_WET,
    fade_in_seconds: float = _PAD_END_TAIL_FADE_IN_SECONDS,
    aecho: str = _PAD_END_TAIL_AECHO,
) -> str:
    """Build a filter_complex that pads the end with a wet-only reverb tail.

    The body (and optional start silence) is left dry; only the appended
    ``pad_end_seconds`` region contains the generated wash.
    """
    if orig_duration <= 0:
        raise ValueError("orig_duration must be > 0 for reverb end tail")
    if pad_end_seconds <= 0:
        raise ValueError("pad_end_seconds must be > 0 for reverb end tail")

    src_len = min(max(source_seconds, 0.05), orig_duration)
    trim_start = max(0.0, orig_duration - src_len)
    fade_in = min(max(fade_in_seconds, 0.0), pad_end_seconds * 0.25)
    # Fade the pad itself so it's gone by the next "1".
    fade_out_d = max(pad_end_seconds * 0.75, pad_end_seconds - fade_in)
    fade_out_st = max(0.0, pad_end_seconds - fade_out_d)
    wet_g = max(0.0, min(float(wet), 1.0))

    body_chain = (
        _adelay_filter(pad_start_seconds, channels)
        if pad_start_seconds > 0
        else "anull"
    )

    # Feed ending slice → silence of pad length → aecho (rings into silence) →
    # keep only the pad region → quiet + pad-side fades.
    return (
        f"[0:a]asplit=2[bodyin][tailin];"
        f"[bodyin]{body_chain}[body];"
        f"[tailin]atrim=start={trim_start:.10f}:end={orig_duration:.10f},"
        f"asetpts=PTS-STARTPTS,"
        f"apad=pad_dur={pad_end_seconds:.10f},"
        f"aecho={aecho},"
        f"atrim=start={src_len:.10f}:duration={pad_end_seconds:.10f},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={wet_g:.4f},"
        f"afade=t=in:d={fade_in:.10f},"
        f"afade=t=out:st={fade_out_st:.10f}:d={fade_out_d:.10f}[pad];"
        f"[body][pad]concat=n=2:v=0:a=1[out]"
    )


def pad_silence_to(
    src: Path,
    dst: Path,
    *,
    pad_start_seconds: float = 0.0,
    pad_end_seconds: float = 0.0,
    target_format: str,
    bitrate_kbps: Optional[int] = None,
    end_tail: EndTailMode = PAD_END_TAIL_DEFAULT,
) -> Path:
    """Write `src` to `dst` with silence prepended and/or appended.

    The original body is not faded or wet-processed. When ``end_tail`` is
    ``\"reverb\"`` and an end pad is requested, the padded region is filled
    with a quiet multi-tap wash derived from the ending (pad-only). Use
    ``end_tail=\"silence\"`` for dry silence.

    Preserves container / encoding parameters the same way as
    ``pitch_shift_to``. Tags are not preserved — callers should
    ``copy_all_tags`` afterward.
    """
    if target_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported target format: {target_format!r}. "
            f"Expected one of: {', '.join(SUPPORTED_FORMATS)}"
        )
    if pad_start_seconds < 0 or pad_end_seconds < 0:
        raise ValueError("Pad durations must be non-negative")
    if pad_start_seconds == 0 and pad_end_seconds == 0:
        raise ValueError(
            "At least one of pad_start_seconds / pad_end_seconds must be > 0"
        )
    if end_tail not in ("reverb", "silence"):
        raise ValueError(f"end_tail must be 'reverb' or 'silence', got {end_tail!r}")

    probed = probe_audio(src)
    sample_rate = probed.get("sample_rate") or 44100
    try:
        sample_rate = int(sample_rate)
    except (TypeError, ValueError):
        sample_rate = 44100

    channels = probed.get("channels")
    try:
        channels_i = int(channels) if channels is not None else None
    except (TypeError, ValueError):
        channels_i = None

    try:
        orig_duration = float(probed.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        orig_duration = 0.0

    start_pad = pad_start_seconds if pad_start_seconds > 0 else 0.0
    use_reverb_tail = pad_end_seconds > 0 and end_tail == "reverb" and orig_duration > 0

    staging: Optional[Path] = None
    encode_dst = dst
    if sys.platform == "win32" and not _is_ascii_path(dst):
        staging = ascii_staging_path(dst, prefix=".tm_pad")
        encode_dst = staging

    cmd: list[str] = [
        "ffmpeg",
        "-i",
        ffmpeg_arg_path(src),
        "-vn",
    ]

    if use_reverb_tail:
        fc = build_pad_end_reverb_filter_complex(
            orig_duration=orig_duration,
            pad_start_seconds=start_pad,
            pad_end_seconds=pad_end_seconds,
            channels=channels_i,
        )
        cmd.extend(["-filter_complex", fc, "-map", "[out]"])
    else:
        filters: list[str] = []
        if start_pad > 0:
            filters.append(_adelay_filter(start_pad, channels_i))
        if pad_end_seconds > 0:
            filters.append(f"apad=pad_dur={pad_end_seconds:.10f}")
        cmd.extend(["-af", ",".join(filters)])

    if target_format == "aiff":
        cmd.extend(
            [
                "-c:a",
                _pcm_codec_for_aiff(probed),
                "-ar",
                str(sample_rate),
                "-f",
                "aiff",
            ]
        )
    elif target_format == "m4a":
        kbps = bitrate_kbps
        if kbps is None:
            kbps = probed.get("bitrate_kbps") or 256
        cmd.extend(["-c:a", "aac", "-b:a", f"{int(kbps)}k", "-movflags", "+faststart"])
    else:  # mp3
        kbps = bitrate_kbps
        if kbps is None:
            kbps = probed.get("bitrate_kbps") or 320
        cmd.extend(["-c:a", "libmp3lame", "-b:a", f"{int(kbps)}k"])

    if channels_i is not None and channels_i > 0:
        cmd.extend(["-ac", str(channels_i)])

    cmd.extend(["-y", ffmpeg_arg_path(encode_dst)])

    try:
        _run_ffmpeg(cmd)
        if staging is not None:
            staging.replace(dst)
    except Exception:
        if staging is not None and staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass
        raise
    return dst


def trim_silence_pads_to(
    src: Path,
    dst: Path,
    *,
    trim_start_seconds: float = 0.0,
    trim_end_seconds: float = 0.0,
    target_format: str,
    bitrate_kbps: Optional[int] = None,
) -> Path:
    """Write `src` to `dst` with silence removed from the start and/or end.

    Inverse of ``pad_silence_to``. Tags are not preserved — callers should
    ``copy_all_tags`` afterward.
    """
    if target_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported target format: {target_format!r}. "
            f"Expected one of: {', '.join(SUPPORTED_FORMATS)}"
        )
    if trim_start_seconds < 0 or trim_end_seconds < 0:
        raise ValueError("Trim durations must be non-negative")
    if trim_start_seconds == 0 and trim_end_seconds == 0:
        raise ValueError(
            "At least one of trim_start_seconds / trim_end_seconds must be > 0"
        )

    probed = probe_audio(src)
    duration = probed.get("duration_seconds")
    if duration is None or float(duration) <= 0:
        raise EncodeError(f"Could not probe duration for {src}")
    duration_f = float(duration)
    keep = duration_f - trim_start_seconds - trim_end_seconds
    if keep <= 0.01:
        raise EncodeError(
            f"Trim amounts ({trim_start_seconds:.3f}s + {trim_end_seconds:.3f}s) "
            f"leave no audio (duration {duration_f:.3f}s)"
        )

    sample_rate = probed.get("sample_rate") or 44100
    try:
        sample_rate = int(sample_rate)
    except (TypeError, ValueError):
        sample_rate = 44100

    channels = probed.get("channels")
    try:
        channels_i = int(channels) if channels is not None else None
    except (TypeError, ValueError):
        channels_i = None

    staging: Optional[Path] = None
    encode_dst = dst
    if sys.platform == "win32" and not _is_ascii_path(dst):
        staging = ascii_staging_path(dst, prefix=".tm_unpad")
        encode_dst = staging

    cmd = [
        "ffmpeg",
        "-ss",
        f"{trim_start_seconds:.10f}",
        "-i",
        ffmpeg_arg_path(src),
        "-t",
        f"{keep:.10f}",
        "-vn",
    ]
    if target_format == "aiff":
        cmd.extend(
            [
                "-c:a",
                _pcm_codec_for_aiff(probed),
                "-ar",
                str(sample_rate),
                "-f",
                "aiff",
            ]
        )
    elif target_format == "m4a":
        kbps = bitrate_kbps
        if kbps is None:
            kbps = probed.get("bitrate_kbps") or 256
        cmd.extend(["-c:a", "aac", "-b:a", f"{int(kbps)}k", "-movflags", "+faststart"])
    else:  # mp3
        kbps = bitrate_kbps
        if kbps is None:
            kbps = probed.get("bitrate_kbps") or 320
        cmd.extend(["-c:a", "libmp3lame", "-b:a", f"{int(kbps)}k"])

    if channels_i is not None and channels_i > 0:
        cmd.extend(["-ac", str(channels_i)])

    cmd.extend(["-y", ffmpeg_arg_path(encode_dst)])

    try:
        _run_ffmpeg(cmd)
        if staging is not None:
            staging.replace(dst)
    except Exception:
        if staging is not None and staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass
        raise
    return dst


def pitch_shift_to(
    src: Path,
    dst: Path,
    cents: float,
    target_format: str,
    *,
    bitrate_kbps: Optional[int] = None,
) -> Path:
    """Pitch-shift audio into `dst` while keeping tempo/BPM the same.

    Replaces the audio while keeping the source container format and encoding
    parameters (AIFF PCM codec / sample rate / channels; M4A/MP3 bitrate).
    Tags are not preserved by ffmpeg — callers should ``copy_all_tags`` from
    `src` afterward.
    """
    if target_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported target format: {target_format!r}. "
            f"Expected one of: {', '.join(SUPPORTED_FORMATS)}"
        )

    probed = probe_audio(src)
    sample_rate = probed.get("sample_rate") or 44100
    try:
        sample_rate = int(sample_rate)
    except (TypeError, ValueError):
        sample_rate = 44100

    channels = probed.get("channels")
    try:
        channels_i = int(channels) if channels is not None else None
    except (TypeError, ValueError):
        channels_i = None

    af = _pitch_filter(cents, sample_rate)

    staging: Optional[Path] = None
    encode_dst = dst
    if sys.platform == "win32" and not _is_ascii_path(dst):
        staging = ascii_staging_path(dst, prefix=".tm_tune")
        encode_dst = staging

    cmd = [
        "ffmpeg",
        "-i",
        ffmpeg_arg_path(src),
        "-vn",
        "-af",
        af,
    ]
    if target_format == "aiff":
        # Keep the original AIFF PCM encoding — do not downconvert to 16-bit/44.1k.
        cmd.extend(
            [
                "-c:a",
                _pcm_codec_for_aiff(probed),
                "-ar",
                str(sample_rate),
                "-f",
                "aiff",
            ]
        )
    elif target_format == "m4a":
        kbps = bitrate_kbps
        if kbps is None:
            kbps = probed.get("bitrate_kbps") or 256
        cmd.extend(["-c:a", "aac", "-b:a", f"{int(kbps)}k", "-movflags", "+faststart"])
    else:  # mp3
        kbps = bitrate_kbps
        if kbps is None:
            kbps = probed.get("bitrate_kbps") or 320
        cmd.extend(["-c:a", "libmp3lame", "-b:a", f"{int(kbps)}k"])

    if channels_i is not None and channels_i > 0:
        cmd.extend(["-ac", str(channels_i)])

    cmd.extend(["-y", ffmpeg_arg_path(encode_dst)])

    try:
        _run_ffmpeg(cmd)
        if staging is not None:
            if dst.exists():
                dst.unlink()
            staging.rename(dst)
        return dst
    except Exception:
        if staging is not None and staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass
        raise


def _run_ffmpeg(cmd: list[str]) -> None:
    # Centralised dependency check (and test simulation) lives in track_manager.deps
    from . import deps as tm_deps

    try:
        tm_deps.ensure_ffmpeg_available()
    except tm_deps.MissingDependencyError as e:
        # Surface as an EncodeError so callers that expect encoding problems
        # handle it uniformly.
        raise EncodeError(str(e))

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr_output = e.stderr.strip() if e.stderr else str(e)
        raise EncodeError(f"ffmpeg failed: {stderr_output[-500:]}") from e


# ---------------------------------------------------------------------------
# Basic tags (player-visible projection of the blob document)
# ---------------------------------------------------------------------------


def apply_basic_tags(
    path: Path,
    doc: dict[str, Any],
    cover_data: Optional[bytes] = None,
) -> None:
    """Write player-visible tags onto `path` derived from the canonical document.

    Always idempotent: existing basic tags are cleared first so re-applying
    cannot accumulate duplicates. The track-manager blob is left untouched.
    """
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".mp4"):
        _apply_m4a_tags(path, doc, cover_data)
    elif suffix == ".mp3":
        _apply_id3_tags(MP3(str(path)), doc, cover_data)
    elif suffix in (".aiff", ".aif"):
        _apply_id3_tags(AIFF(str(path)), doc, cover_data)
    else:
        raise ValueError(f"Unsupported audio format for tagging: {suffix}")


def _apply_m4a_tags(
    path: Path, doc: dict[str, Any], cover_data: Optional[bytes]
) -> None:
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    track = doc.get("track") or {}

    # Clear basic tags (don't touch our blob atom or anything else namespaced).
    _BASIC_M4A_KEYS = (
        "\xa9nam",
        "\xa9ART",
        "aART",
        "\xa9alb",
        "\xa9day",
        "\xa9gen",
        "trkn",
        "disk",
        "covr",
        "----:com.apple.iTunes:ISRC",
        "----:com.apple.iTunes:LABEL",
    )
    for key in _BASIC_M4A_KEYS:
        if key in tags:
            del tags[key]

    if track.get("title"):
        tags["\xa9nam"] = [track["title"]]
    if track.get("artist_string"):
        tags["\xa9ART"] = [track["artist_string"]]
    if track.get("album_artist"):
        tags["aART"] = [track["album_artist"]]
    if track.get("album"):
        tags["\xa9alb"] = [track["album"]]
    if track.get("date"):
        tags["\xa9day"] = [str(track["date"])]
    if track.get("genre"):
        tags["\xa9gen"] = [track["genre"]]
    if track.get("track_number") is not None:
        tags["trkn"] = [(int(track["track_number"]), 0)]
    if track.get("disc_number") is not None:
        tags["disk"] = [(int(track["disc_number"]), 0)]
    if track.get("isrc"):
        tags["----:com.apple.iTunes:ISRC"] = [track["isrc"].encode("utf-8")]
    if track.get("label"):
        tags["----:com.apple.iTunes:LABEL"] = [track["label"].encode("utf-8")]

    if cover_data:
        tags["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


def _apply_id3_tags(audio, doc: dict[str, Any], cover_data: Optional[bytes]) -> None:
    """Write basic tags onto an MP3 or AIFF file (both use ID3v2)."""
    if audio.tags is None:
        audio.add_tags()
    tags: ID3 = audio.tags
    track = doc.get("track") or {}

    # Clear the basic frames we own; leave GEOB / private / non-basic frames alone.
    for frame_id in (
        "TIT2",
        "TPE1",
        "TPE2",
        "TALB",
        "TDRC",
        "TCON",
        "TRCK",
        "TPOS",
        "TSRC",
        "TPUB",
        "APIC",
    ):
        tags.delall(frame_id)

    if track.get("title"):
        tags.add(TIT2(encoding=3, text=track["title"]))
    if track.get("artist_string"):
        tags.add(TPE1(encoding=3, text=track["artist_string"]))
    if track.get("album_artist"):
        tags.add(TPE2(encoding=3, text=track["album_artist"]))
    if track.get("album"):
        tags.add(TALB(encoding=3, text=track["album"]))
    if track.get("date"):
        tags.add(TDRC(encoding=3, text=str(track["date"])))
    if track.get("genre"):
        tags.add(TCON(encoding=3, text=track["genre"]))
    if track.get("track_number") is not None:
        tags.add(TRCK(encoding=3, text=str(track["track_number"])))
    if track.get("disc_number") is not None:
        tags.add(TPOS(encoding=3, text=str(track["disc_number"])))
    if track.get("isrc"):
        tags.add(TSRC(encoding=3, text=track["isrc"]))
    if track.get("label"):
        tags.add(TPUB(encoding=3, text=track["label"]))

    if cover_data:
        tags.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,  # cover (front)
                desc="cover",
                data=cover_data,
            )
        )

    audio.save()


# ---------------------------------------------------------------------------
# Cover art helpers
# ---------------------------------------------------------------------------


def fetch_cover(url: str, timeout: int = 10) -> Optional[bytes]:
    """Download cover art bytes from `url`. Returns None on failure."""
    try:
        import requests

        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"⚠️ Failed to download cover art: {e}", file=sys.stderr)
        return None


def thumbnail_to_jpeg(path: Path) -> Optional[bytes]:
    """Read a thumbnail file (jpg/webp/png) and return JPEG bytes.

    yt-dlp typically writes thumbnails as `.webp` (YouTube) or `.jpg`
    (SoundCloud). ID3 APIC and MP4 covr both want JPEG, so we normalise.
    Returns None if the file is missing or ffmpeg fails.
    """
    if not path.exists():
        return None

    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        try:
            return path.read_bytes()
        except OSError:
            return None

    if not shutil.which("ffmpeg"):
        return None

    try:
        out = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(path),
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return out.stdout or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
