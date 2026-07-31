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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

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
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

SUPPORTED_FORMATS = ("aiff", "m4a", "mp3")
DEFAULT_FORMAT = "aiff"


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
            str(path),
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
        str(src),
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
        str(dst),
    ]
    _run_ffmpeg(cmd)
    return dst


def encode_to_m4a(src: Path, dst: Path, bitrate_kbps: int = 256) -> Path:
    """Encode to M4A (AAC, default 256 kbps, 48 kHz)."""
    cmd = [
        "ffmpeg",
        "-i",
        str(src),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        f"{bitrate_kbps}k",
        "-movflags",
        "+faststart",
        "-y",
        str(dst),
    ]
    _run_ffmpeg(cmd)
    return dst


def encode_to_mp3(src: Path, dst: Path, bitrate_kbps: int = 320) -> Path:
    """Encode to MP3 (libmp3lame CBR, default 320 kbps)."""
    cmd = [
        "ffmpeg",
        "-i",
        str(src),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{bitrate_kbps}k",
        "-y",
        str(dst),
    ]
    _run_ffmpeg(cmd)
    return dst


def encode_to(target_format: str, src: Path, dst: Path, **kwargs: Any) -> Path:
    """Dispatch to the right encoder based on `target_format`."""
    if target_format == "aiff":
        return encode_to_aiff(src, dst)
    if target_format == "m4a":
        return encode_to_m4a(src, dst, **kwargs)
    if target_format == "mp3":
        return encode_to_mp3(src, dst, **kwargs)
    raise ValueError(
        f"Unsupported target format: {target_format!r}. "
        f"Expected one of: {', '.join(SUPPORTED_FORMATS)}"
    )


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
