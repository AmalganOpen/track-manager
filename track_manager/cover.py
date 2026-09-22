"""Scale embedded cover art down to a Pioneer-safe JPEG.

Rekordbox copies artwork into ``PIONEER/Artwork/`` on import/USB export, so
the embedded APIC does not need to be 2000–3000px. A large JPEG on the tail
of an AIFF is extra bytes after the PCM; XDJ-1000 MK2 can walk into that
tail at end-of-track and report "unsupported file format".

640×640 is the largest embedded cover that loaded on an XDJ-1000 MK2 in the
ink2 playlist (CURSE OF CENTRALIA). Images already at or under the cap are
left alone; we never upscale.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple, Optional

from mutagen.aiff import AIFF
from mutagen.id3 import APIC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

# Largest cover that loaded on XDJ-1000 MK2 (Centralia). High Fashion is 500.
COVER_MAX_SIDE = 640
# Hard ceiling for --max-side so a typo cannot ask ffmpeg to allocate a giant frame.
COVER_SIDE_LIMIT = 4096
# ffmpeg mjpeg q: 2 = visually lossless, 31 = worst. 5 ≈ JPEG quality ~85.
_JPEG_Q = 5


class CoverInfo(NamedTuple):
    width: int
    height: int
    progressive: bool
    size_bytes: int


class CoverScaleResult(NamedTuple):
    path: Path
    action: str  # scaled | skipped | failed
    detail: str
    old: Optional[CoverInfo]
    new: Optional[CoverInfo]


def jpeg_info(data: bytes) -> Optional[CoverInfo]:
    """Return JPEG dimensions / progressive flag, or None if not a JPEG."""
    if not data.startswith(b"\xff\xd8"):
        return None
    progressive = False
    width = height = 0
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            break
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        # JPEG segment length includes the 2-byte length field (minimum 2).
        if seglen < 2:
            break
        payload = data[i + 2 : i + seglen]
        if marker == 0xC2:
            progressive = True
        if marker in (0xC0, 0xC1, 0xC2) and len(payload) >= 5:
            height = struct.unpack(">H", payload[1:3])[0]
            width = struct.unpack(">H", payload[3:5])[0]
            break
        i += seglen
        if marker == 0xDA:
            break
    if width <= 0 or height <= 0:
        return None
    return CoverInfo(width, height, progressive, len(data))


def needs_scale(info: CoverInfo, max_side: int = COVER_MAX_SIDE) -> bool:
    """True if the JPEG is over ``max_side`` or is progressive."""
    return info.progressive or max(info.width, info.height) > max_side


def _clamp_max_side(max_side: int) -> Optional[int]:
    """Return ``max_side`` if it is a usable long-side cap, else None."""
    if max_side < 1 or max_side > COVER_SIDE_LIMIT:
        return None
    return max_side


def prepare_cover_jpeg(data: bytes, *, max_side: int = COVER_MAX_SIDE) -> bytes:
    """Return baseline JPEG bytes with the long side at most ``max_side``.

    Already-small baseline JPEGs are returned unchanged. On ffmpeg failure
    the original bytes are returned so a download still gets *some* cover.
    """
    cap = _clamp_max_side(max_side)
    if cap is None:
        return data
    info = jpeg_info(data)
    if info is not None and not needs_scale(info, cap):
        return data
    scaled = _ffmpeg_scale_jpeg(data, cap)
    return scaled if scaled else data


def extract_embedded_cover(path: Path) -> Optional[bytes]:
    """Pull front-cover JPEG/PNG bytes out of AIFF/MP3 APIC or M4A covr."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".aiff", ".aif", ".mp3"):
            audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
            tags = audio.tags
            if not tags:
                return None
            frames = tags.getall("APIC")
            return frames[0].data if frames else None
        if suffix in (".m4a", ".mp4"):
            audio = MP4(str(path))
            if not audio.tags:
                return None
            covers = audio.tags.get("covr") or []
            if not covers:
                return None
            payload = covers[0]
            return bytes(payload)
    except Exception:
        return None
    return None


def replace_embedded_cover(path: Path, jpeg: bytes) -> None:
    """Replace the player-visible cover; leave the metadata blob untouched."""
    suffix = path.suffix.lower()
    if suffix in (".aiff", ".aif", ".mp3"):
        audio = AIFF(str(path)) if suffix in (".aiff", ".aif") else MP3(str(path))
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags.delall("APIC")
        audio.tags.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="cover",
                data=jpeg,
            )
        )
        audio.save()
        return
    if suffix in (".m4a", ".mp4"):
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        return
    raise ValueError(f"Unsupported audio format for cover art: {suffix}")


def _write_cover_atomically(path: Path, jpeg: bytes) -> None:
    """Rewrite cover on a sibling copy, then ``os.replace`` over ``path``.

    mutagen's AIFF/MP3/MP4 ``save()`` mutates the file in place (insert/delete
    bytes). Doing that on the live library file can leave a truncated AIFF if
    the process dies mid-write. The original is only replaced after the copy
    saves successfully.
    """
    tmp = path.with_name(f".tm_cover_{os.getpid()}{path.suffix}")
    if tmp.exists():
        tmp.unlink()
    try:
        shutil.copy2(path, tmp)
        replace_embedded_cover(tmp, jpeg)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _acceptable_cover(jpeg: bytes, max_side: int) -> Optional[CoverInfo]:
    """Return JPEG info only when the bytes are a Pioneer-safe baseline JPEG."""
    info = jpeg_info(jpeg)
    if info is None or needs_scale(info, max_side):
        return None
    return info


def scale_cover_in_file(
    path: Path,
    *,
    max_side: int = COVER_MAX_SIDE,
    dry_run: bool = False,
) -> CoverScaleResult:
    """Scale this file's embedded cover if it exceeds ``max_side``.

    Never writes the live file in place: a sibling temp is mutated and
    swapped in only after a successful save. Failures leave ``path``
    untouched and return ``action="failed"`` instead of raising.
    """
    cap = _clamp_max_side(max_side)
    if cap is None:
        return CoverScaleResult(
            path, "failed", f"max_side must be 1–{COVER_SIDE_LIMIT}", None, None
        )
    try:
        return _scale_cover_in_file(path, max_side=cap, dry_run=dry_run)
    except Exception as e:
        return CoverScaleResult(path, "failed", f"{type(e).__name__}: {e}", None, None)


def _scale_cover_in_file(
    path: Path, *, max_side: int, dry_run: bool
) -> CoverScaleResult:
    raw = extract_embedded_cover(path)
    if not raw:
        return CoverScaleResult(path, "skipped", "no cover", None, None)
    info = jpeg_info(raw)
    if info is None:
        # Non-JPEG (webp/png in APIC) — still run through ffmpeg.
        if dry_run:
            return CoverScaleResult(
                path, "scaled", f"non-JPEG {len(raw)} bytes (dry-run)", None, None
            )
        jpeg = prepare_cover_jpeg(raw, max_side=max_side)
        new = _acceptable_cover(jpeg, max_side)
        if jpeg == raw or new is None:
            return CoverScaleResult(
                path, "failed", "could not convert cover", None, None
            )
        _write_cover_atomically(path, jpeg)
        return CoverScaleResult(path, "scaled", "converted to JPEG", None, new)
    if not needs_scale(info, max_side):
        return CoverScaleResult(
            path,
            "skipped",
            f"{info.width}×{info.height} already ≤ {max_side}px",
            info,
            info,
        )
    if dry_run:
        return CoverScaleResult(
            path,
            "scaled",
            f"{info.width}×{info.height} → max {max_side}px (dry-run)",
            info,
            None,
        )
    jpeg = prepare_cover_jpeg(raw, max_side=max_side)
    new = _acceptable_cover(jpeg, max_side)
    if jpeg == raw or new is None:
        detail = (
            "ffmpeg not found on PATH"
            if not shutil.which("ffmpeg")
            else "ffmpeg did not resize cover"
        )
        return CoverScaleResult(path, "failed", detail, info, None)
    _write_cover_atomically(path, jpeg)
    return CoverScaleResult(
        path,
        "scaled",
        f"{info.width}×{info.height} → {new.width}×{new.height}",
        info,
        new,
    )


def scale_library_covers(
    tracks: list[Path],
    *,
    max_side: int = COVER_MAX_SIDE,
    dry_run: bool = False,
) -> list[CoverScaleResult]:
    """Run :func:`scale_cover_in_file` on every path in ``tracks``."""
    return [
        scale_cover_in_file(path, max_side=max_side, dry_run=dry_run) for path in tracks
    ]


def _ffmpeg_scale_jpeg(data: bytes, max_side: int) -> Optional[bytes]:
    if _clamp_max_side(max_side) is None:
        return None
    if not shutil.which("ffmpeg"):
        return None
    # max_side is an int in 1..=COVER_SIDE_LIMIT; safe to interpolate into -vf.
    vf = f"scale={max_side}:{max_side}:" "force_original_aspect_ratio=decrease"
    fd, tmp_name = tempfile.mkstemp(suffix=".img")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        out = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(tmp),
                "-vf",
                vf,
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                str(_JPEG_Q),
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return out.stdout or None
