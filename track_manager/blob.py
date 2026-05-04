"""Embedded metadata blob: read/write the track-manager JSON record from inside an audio file.

The audio file is the single source of truth: every track carries a complete JSON
document inside its container (a custom MP4 atom for M4A, an ID3v2 GEOB frame for
MP3 and AIFF). Player-visible tags (TIT2, TPE1, COVR, etc.) are derived projections
of this document, written by the encoders/taggers in `downloader.py`.

Deleting the audio file deletes the metadata. No sidecars, no orphans.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

from mutagen.aiff import AIFF
from mutagen.id3 import GEOB, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

# Reverse-DNS namespace keeps our atoms/frames out of any well-known iTunes/ID3 space.
_M4A_ATOM = "----:com.tm:metadata"
_GEOB_DESC = "tm:metadata"

# Current schema version. Bump when making a breaking change to the template.
SCHEMA_VERSION = 1

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "track.json"


def load_template() -> dict[str, Any]:
    """Return a fresh deep copy of the empty track template."""
    with open(_TEMPLATE_PATH) as f:
        return json.load(f)


def merge_into_template(data: dict[str, Any]) -> dict[str, Any]:
    """Merge `data` over the template so every required key is present.

    Unknown top-level keys in `data` are preserved (forward-compat for user
    additions), and missing keys are filled in from the template.
    """
    merged = load_template()
    _deep_merge(merged, data)
    merged["$schema_version"] = SCHEMA_VERSION
    return merged


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_blob(path: Path) -> Optional[dict[str, Any]]:
    """Return the embedded metadata blob, or None if the file has none.

    Returns None for files that pre-date the blob format (legacy m4a/mp3/aiff
    written by older versions of track-manager). Callers that need to handle
    legacy files should fall back to reading scattered tags.
    """
    suffix = path.suffix.lower()

    try:
        if suffix in (".m4a", ".mp4"):
            return _read_m4a(path)
        if suffix == ".mp3":
            return _read_id3(MP3(str(path)).tags)
        if suffix in (".aiff", ".aif"):
            return _read_id3(AIFF(str(path)).tags)
    except (FileNotFoundError, ID3NoHeaderError):
        return None
    except Exception:
        # Corrupt tags shouldn't crash the caller; treat as "no blob".
        return None

    return None


def _read_m4a(path: Path) -> Optional[dict[str, Any]]:
    audio = MP4(str(path))
    raw = audio.tags.get(_M4A_ATOM) if audio.tags else None
    if not raw:
        return None
    # MP4FreeForm or list of MP4FreeForm — normalise to bytes.
    payload = raw[0] if isinstance(raw, list) else raw
    payload = bytes(payload) if not isinstance(payload, (bytes, bytearray)) else payload
    return json.loads(payload.decode("utf-8"))


def _read_id3(tags: Optional[ID3]) -> Optional[dict[str, Any]]:
    if tags is None:
        return None
    for frame in tags.getall("GEOB"):
        if getattr(frame, "desc", None) == _GEOB_DESC:
            return json.loads(bytes(frame.data).decode("utf-8"))
    return None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_blob(path: Path, data: dict[str, Any]) -> None:
    """Embed `data` into the audio file at `path`.

    The data is merged into the template first so every schema key is present
    in the stored document. Writing replaces any previously-stored blob; other
    tags on the file are left untouched.
    """
    document = merge_into_template(data)
    payload = json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False).encode("utf-8")

    suffix = path.suffix.lower()
    if suffix in (".m4a", ".mp4"):
        _write_m4a(path, payload)
    elif suffix == ".mp3":
        _write_mp3(path, payload)
    elif suffix in (".aiff", ".aif"):
        _write_aiff(path, payload)
    else:
        raise ValueError(f"Unsupported audio format for blob storage: {suffix}")


def _write_m4a(path: Path, payload: bytes) -> None:
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags[_M4A_ATOM] = [payload]
    audio.save()


def _write_mp3(path: Path, payload: bytes) -> None:
    audio = MP3(str(path))
    if audio.tags is None:
        audio.add_tags()
    _set_geob(audio.tags, payload)
    audio.save()


def _write_aiff(path: Path, payload: bytes) -> None:
    audio = AIFF(str(path))
    if audio.tags is None:
        audio.add_tags()
    _set_geob(audio.tags, payload)
    audio.save()


def _set_geob(tags: ID3, payload: bytes) -> None:
    # Remove any pre-existing tm:metadata GEOB frames, then add the fresh one.
    existing = [f for f in tags.getall("GEOB") if getattr(f, "desc", None) == _GEOB_DESC]
    for frame in existing:
        tags.delall(frame.HashKey)
    tags.add(
        GEOB(
            encoding=3,  # UTF-8
            mime="application/json",
            filename="",
            desc=_GEOB_DESC,
            data=payload,
        )
    )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def has_blob(path: Path) -> bool:
    """Return True if the file already carries a track-manager blob."""
    return read_blob(path) is not None


def empty_document() -> dict[str, Any]:
    """Return a new, empty document that conforms to the template."""
    doc = load_template()
    doc["$schema_version"] = SCHEMA_VERSION
    return doc
