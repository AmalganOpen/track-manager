"""CDJ-2000NXS playability classifier.

Pioneer's CDJ-2000NXS (2012) has a strict hardware decoder. It accepts a
narrower set of formats than rekordbox or desktop players, so a file that
imports fine into rekordbox can still be flagged incompatible in the
"export to device" popup. This module decides, per file, whether a
CDJ-2000NXS can play it.

CDJ-2000NXS supported formats (from the official manual):

  - MP3:  MPEG-1/2 Layer-3, <= 48 kHz, 32-320 kbps (CBR or VBR).
  - AAC:  MPEG-2/4 AAC LC, <= 48 kHz. (ALAC is *not* AAC and is unsupported.)
  - WAV:  uncompressed PCM, 16- or 24-bit, 44.1 or 48 kHz.
  - AIFF: uncompressed PCM, 16- or 24-bit, 44.1 or 48 kHz.

Explicitly unsupported: FLAC, Apple Lossless (ALAC), 32-bit float PCM,
compressed AIFF-C (ALAW/uLaw/ADPCM), WAVE_FORMAT_EXTENSIBLE WAV headers,
and any sample rate above 48 kHz.

The classifier is intentionally conservative: anything it cannot positively
identify as supported is reported as incompatible/unknown so the user can
eyeball it rather than have a track silently rejected on the gear.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple, Optional

# CDJ-2000NXS sample-rate ceiling for every format it accepts.
_MAX_SAMPLE_RATE = 48000
_VALID_PCM_RATES = {44100, 48000}

# Uncompressed integer PCM codecs ffprobe reports for WAV/AIFF. Float PCM
# (pcm_f32*/pcm_f64*) and compressed AIFF-C codecs are deliberately absent.
_PCM_CODECS = {
    "pcm_s16be",
    "pcm_s16le",
    "pcm_s24be",
    "pcm_s24le",
}

# WAVE_FORMAT_EXTENSIBLE format tag. ffprobe surfaces it as codec_tag 0xfffe.
_WAV_EXTENSIBLE_TAG = 0xFFFE

_PCM_EXTS = {".wav", ".aiff", ".aif"}
_AAC_EXTS = {".m4a", ".mp4", ".aac"}


class CompatResult(NamedTuple):
    """Outcome of classifying one file against the CDJ-2000NXS decoder."""

    compatible: bool
    reason: str
    # ``unknown`` flags files we could not positively classify (missing
    # ffprobe, unreadable, or an extension we don't model). These are not
    # counted as hard incompatibilities — they need a manual look.
    unknown: bool = False


def _probe(path: Path) -> Optional[dict]:
    """Return ffprobe's first audio stream as a dict, or None on failure.

    Includes ``codec_tag`` (needed to detect WAVE_FORMAT_EXTENSIBLE) and
    ``sample_fmt`` (a secondary float-PCM signal) alongside the basics.
    """
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,codec_tag,sample_fmt,sample_rate,"
                "bits_per_raw_sample,bits_per_sample,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(out.stdout)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return None

    streams = data.get("streams") or []
    return streams[0] if streams else None


def _sample_rate(stream: dict) -> Optional[int]:
    try:
        return int(stream.get("sample_rate"))
    except (TypeError, ValueError):
        return None


def _codec_tag(stream: dict) -> Optional[int]:
    """Parse ffprobe's codec_tag (e.g. "0xfffe") into an int, or None."""
    raw = stream.get("codec_tag")
    if raw is None:
        return None
    try:
        return int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
    except (TypeError, ValueError):
        return None


def classify(path: Path) -> CompatResult:
    """Decide whether a CDJ-2000NXS can play ``path``.

    Decision is driven by the file's actual codec/sample-rate (via ffprobe),
    not just its extension, so a mislabelled file is judged on its real
    contents.
    """
    ext = path.suffix.lower()

    stream = _probe(path)
    if stream is None:
        if not shutil.which("ffprobe"):
            return CompatResult(
                False, "ffprobe unavailable - cannot verify", unknown=True
            )
        return CompatResult(False, "unreadable or no audio stream", unknown=True)

    codec = (stream.get("codec_name") or "").lower()
    sample_rate = _sample_rate(stream)
    sample_fmt = (stream.get("sample_fmt") or "").lower()

    # --- FLAC / ALAC: hard no on the original NXS ---
    if codec == "flac":
        return CompatResult(False, "FLAC not supported by CDJ-2000NXS")
    if codec == "alac":
        return CompatResult(False, "Apple Lossless (ALAC) not supported by CDJ-2000NXS")

    # --- MP3 ---
    if codec == "mp3":
        if sample_rate and sample_rate > _MAX_SAMPLE_RATE:
            return CompatResult(False, f"MP3 sample rate {sample_rate} Hz > 48 kHz")
        return CompatResult(True, "MP3")

    # --- AAC ---
    if codec == "aac":
        if sample_rate and sample_rate > _MAX_SAMPLE_RATE:
            return CompatResult(False, f"AAC sample rate {sample_rate} Hz > 48 kHz")
        return CompatResult(True, "AAC")

    # --- WAV / AIFF (uncompressed PCM only) ---
    if ext in _PCM_EXTS or codec.startswith("pcm_"):
        if codec.startswith("pcm_f"):
            return CompatResult(False, f"32-bit float PCM ({codec}) not supported")
        if "flt" in sample_fmt or "dbl" in sample_fmt:
            return CompatResult(
                False, f"float PCM (sample_fmt={sample_fmt}) not supported"
            )
        if codec not in _PCM_CODECS:
            # Compressed AIFF-C (ALAW/uLaw/ADPCM) or some exotic PCM width.
            return CompatResult(
                False, f"non-PCM/compressed codec ({codec or 'unknown'}) not supported"
            )
        if sample_rate not in _VALID_PCM_RATES:
            return CompatResult(
                False, f"sample rate {sample_rate} Hz not in {{44.1, 48}} kHz"
            )
        if ext == ".wav" and _codec_tag(stream) == _WAV_EXTENSIBLE_TAG:
            return CompatResult(False, "WAVE_FORMAT_EXTENSIBLE header not supported")
        depth = "24-bit" if "24" in codec else "16-bit"
        return CompatResult(True, f"PCM {depth} {sample_rate // 1000}kHz")

    # --- AAC-family extensions whose codec we somehow didn't catch above ---
    if ext in _AAC_EXTS:
        if sample_rate and sample_rate > _MAX_SAMPLE_RATE:
            return CompatResult(False, f"sample rate {sample_rate} Hz > 48 kHz")
        return CompatResult(True, codec.upper() or "AAC")

    return CompatResult(
        False, f"unrecognised format ({codec or ext or 'no extension'})", unknown=True
    )


# Audio extensions we attempt to classify when scanning a directory.
_AUDIO_EXTS = _PCM_EXTS | _AAC_EXTS | {".mp3", ".flac", ".ogg", ".opus"}


def scan_dir(library_dir: Path) -> list[tuple[Path, CompatResult]]:
    """Classify every audio file directly in ``library_dir`` (non-recursive).

    Hidden files and directories (including ``.tm-migration-backup/``) are
    skipped so backups don't pollute the audit.
    """
    results: list[tuple[Path, CompatResult]] = []
    for p in sorted(library_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() not in _AUDIO_EXTS:
            continue
        results.append((p, classify(p)))
    return results
