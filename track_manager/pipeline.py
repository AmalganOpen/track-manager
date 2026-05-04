"""Shared download finalize pipeline.

Every source handler converges here once it has a temp audio file and a
canonical metadata document. The pipeline:

  1. Encode the temp file into the target format, with codec-aware
     passthrough to avoid lossy re-encodes when the bytes already match.
  2. Probe the resulting file and fill `audio.*` (codec, bitrate, sample
     rate, etc.) into the document.
  3. Embed cover art (downloading from URL if no bytes were pre-supplied),
     hash it, and record `cover_art.{sha256, embedded}`.
  4. Apply player-visible tags derived from the document.
  5. Write the canonical document into the audio file as the metadata blob.

Cleans up the temp file on success and on encode failure. Returns the final
path or None if the encode failed.
"""

from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path
from typing import Optional

from . import audio as tm_audio
from . import blob as tm_blob


def finalize(
    temp_path: Path,
    final_path: Path,
    doc: dict,
    target_format: str,
    cover_data: Optional[bytes] = None,
) -> Optional[Path]:
    """Encode/passthrough → probe → tag → blob. Mutates `doc` in place."""
    try:
        tm_audio.encode_or_passthrough(target_format, temp_path, final_path)
    except tm_audio.EncodeError as e:
        print(f"⚠️ Encoding failed: {e}", file=sys.stderr)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return None

    # Cover art: prefer bytes the caller already has (e.g. yt-dlp thumbnail),
    # otherwise fetch from the URL recorded in the document.
    if cover_data is None:
        cover_url = doc.get("cover_art", {}).get("url")
        if cover_url:
            cover_data = tm_audio.fetch_cover(cover_url)

    if cover_data:
        doc.setdefault("cover_art", {})
        doc["cover_art"]["sha256"] = sha256(cover_data).hexdigest()
        doc["cover_art"]["embedded"] = True

    info = tm_audio.probe_audio(final_path)
    doc.setdefault("audio", {})
    doc["audio"]["format"] = target_format
    doc["audio"]["codec"] = info.get("codec")
    doc["audio"]["bitrate_kbps"] = info.get("bitrate_kbps")
    doc["audio"]["sample_rate"] = info.get("sample_rate")
    doc["audio"]["bit_depth"] = info.get("bit_depth")
    doc["audio"]["channels"] = info.get("channels")
    doc["audio"]["size_bytes"] = info.get("size_bytes")
    if (
        doc.get("track", {}).get("duration_seconds") is None
        and info.get("duration_seconds") is not None
    ):
        doc["track"]["duration_seconds"] = info["duration_seconds"]

    try:
        tm_audio.apply_basic_tags(final_path, doc, cover_data)
    except Exception as e:
        print(f"⚠️ Failed to apply player-visible tags: {e}", file=sys.stderr)

    try:
        tm_blob.write_blob(final_path, doc)
    except Exception as e:
        print(f"⚠️ Failed to write metadata blob: {e}", file=sys.stderr)

    return final_path
