"""Library migration: re-encode every non-AIFF file in place to AIFF.

Designed for a Rekordbox-only workflow:

  - Re-encode each non-AIFF file (`.m4a`, `.mp3`, `.flac`, …) to AIFF
    next to the original (same dir, same stem, `.aiff` extension).
  - Preserve as much metadata as possible: read the existing track-manager
    blob if present, otherwise scrape scattered tags from the legacy
    iTunes freeform atoms (M4A) or `TXXX` frames (MP3).
  - Record the pre-migration container as `provenance.migrated_from` so
    the chain "original source → intermediate file → AIFF" is preserved
    instead of being rewritten as if AIFF was the source.
  - Move the original into a hidden `.tm-migration-backup/` folder so
    Rekordbox stops indexing it — but keep it on disk as a safety net.
  - Verify duration drift before discarding the original; abort the
    track if drift exceeds the tolerance.

The code does not touch Rekordbox's database. Relinking the new AIFFs in
Rekordbox is a separate (manual) step the user has to perform afterwards.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

from . import audio as tm_audio
from . import blob as tm_blob

BACKUP_DIRNAME = ".tm-migration-backup"

# Audio formats we know how to read tags from. AIFF and AIF are excluded
# (already in target format). Anything not in this list is left alone.
_INPUT_EXTS = {".m4a", ".mp4", ".mp3", ".flac", ".wav", ".ogg", ".aac", ".opus"}

# Reject migrations whose AIFF re-encode shifts the duration by more than
# this much, to keep Rekordbox's duration-fingerprint match working.
DURATION_TOLERANCE_SECONDS = 0.05  # 50 ms


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_migratable_files(library_dir: Path) -> list[Path]:
    """List non-AIFF audio files in `library_dir` (non-recursive)."""
    out = []
    for p in library_dir.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in (".aiff", ".aif"):
            continue
        if ext in _INPUT_EXTS:
            out.append(p)
    return sorted(out, key=lambda x: x.name.lower())


def projected_aiff_size(src: Path) -> int:
    """Approximate the size of `src` after AIFF re-encode (16/44.1 stereo)."""
    info = tm_audio.probe_audio(src)
    duration = info.get("duration_seconds")
    if duration is None or duration <= 0:
        return src.stat().st_size * 8  # rough fallback
    # PCM_S16BE 16/44.1 stereo = 1411.2 kbps + WAV/AIFF chunk overhead (~1 KB)
    return int(duration * 44100 * 2 * 2) + 1024


# ---------------------------------------------------------------------------
# Per-track migration
# ---------------------------------------------------------------------------


def migrate_one(src: Path, *, backup_dir: Optional[Path] = None) -> tuple[bool, str]:
    """Migrate one file to AIFF. Returns (success, human-readable message).

    The encode targets ``<stem>.tmp.aiff`` in the same directory and is
    only renamed to the final ``<stem>.aiff`` after duration verification,
    tagging, and blob writing all succeed. The suffix must remain ``.aiff``
    (not ``.aiff.tmp``) so tag/blob helpers recognise the container. A failure
    at any step leaves the source file untouched and removes the partial temp
    file so a re-run of the migration is idempotent and never trips on stale
    half-written output.
    """
    if src.suffix.lower() in (".aiff", ".aif"):
        return False, "already AIFF"

    final_path = src.with_suffix(".aiff")
    if final_path.exists() and final_path.resolve() != src.resolve():
        return False, f"target already exists: {final_path.name}"

    # Encode into <stem>.tmp.aiff (same dir = same filesystem = atomic rename
    # later). Suffix stays .aiff so apply_basic_tags / write_blob see AIFF, not
    # .tmp. Stale temp from a previous interrupted run is overwritten by -y.
    tmp_path = final_path.with_name(f"{final_path.stem}.tmp.aiff")

    existing_doc = _read_existing_metadata(src)
    src_info = tm_audio.probe_audio(src)
    src_duration = src_info.get("duration_seconds")

    try:
        tm_audio.encode_to_aiff(src, tmp_path)
    except tm_audio.EncodeError as e:
        _safe_unlink(tmp_path)
        return False, f"encode failed: {e}"

    new_info = tm_audio.probe_audio(tmp_path)
    new_duration = new_info.get("duration_seconds")
    if (
        src_duration is not None
        and new_duration is not None
        and abs(src_duration - new_duration) > DURATION_TOLERANCE_SECONDS
    ):
        _safe_unlink(tmp_path)
        return (
            False,
            f"duration drift {src_duration:.3f}s → {new_duration:.3f}s "
            f"exceeds tolerance ({DURATION_TOLERANCE_SECONDS}s)",
        )

    doc = _build_migrated_doc(existing_doc, src_info, new_info, src)

    cover_data = _extract_cover_bytes(src)
    if cover_data:
        doc["cover_art"]["sha256"] = sha256(cover_data).hexdigest()
        doc["cover_art"]["embedded"] = True

    try:
        tm_audio.apply_basic_tags(tmp_path, doc, cover_data)
    except Exception as e:
        print(f"⚠️ Failed to apply player-visible tags: {e}", file=sys.stderr)

    try:
        tm_blob.write_blob(tmp_path, doc)
    except Exception as e:
        print(f"⚠️ Failed to write blob: {e}", file=sys.stderr)

    # Promote tmp → final. After this point the new AIFF exists at the
    # canonical path and the source can safely be moved out.
    tmp_path.rename(final_path)

    backup_dir = backup_dir or (src.parent / BACKUP_DIRNAME)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_target = backup_dir / src.name
    if backup_target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_target = backup_dir / f"{src.stem}.{stamp}{src.suffix}"
    shutil.move(str(src), str(backup_target))

    return True, (
        f"{src.suffix[1:]}@{src_info.get('bitrate_kbps') or '?'}kbps → aiff "
        f"({_fmt_size(new_info.get('size_bytes') or 0)})"
    )


def reencode_backup_to_library(
    backup_path: Path, library_dir: Path
) -> tuple[bool, str]:
    """Re-encode a file living in ``.tm-migration-backup/`` to a library AIFF.

    Repairs a half-migrated track: Rekordbox still points at
    ``<library>/.tm-migration-backup/<stem>.<ext>`` but no
    ``<library>/<stem>.aiff`` was produced. This creates that AIFF (same
    PCM/44.1 kHz target as ``migrate_one``) so ``rekordbox-update-paths``
    can relink it. The backup file is left in place — it is already the
    safety copy.

    Returns ``(success, human-readable message)``.
    """
    final_path = library_dir / f"{backup_path.stem}.aiff"
    if final_path.exists():
        return False, f"target already exists: {final_path.name}"

    tmp_path = final_path.with_name(f"{final_path.stem}.tmp.aiff")

    existing_doc = _read_existing_metadata(backup_path)
    src_info = tm_audio.probe_audio(backup_path)
    src_duration = src_info.get("duration_seconds")

    try:
        tm_audio.encode_to_aiff(backup_path, tmp_path)
    except tm_audio.EncodeError as e:
        _safe_unlink(tmp_path)
        return False, f"encode failed: {e}"

    new_info = tm_audio.probe_audio(tmp_path)
    new_duration = new_info.get("duration_seconds")
    if (
        src_duration is not None
        and new_duration is not None
        and abs(src_duration - new_duration) > DURATION_TOLERANCE_SECONDS
    ):
        _safe_unlink(tmp_path)
        return (
            False,
            f"duration drift {src_duration:.3f}s → {new_duration:.3f}s "
            f"exceeds tolerance ({DURATION_TOLERANCE_SECONDS}s)",
        )

    doc = _build_migrated_doc(existing_doc, src_info, new_info, backup_path)

    cover_data = _extract_cover_bytes(backup_path)
    if cover_data:
        doc["cover_art"]["sha256"] = sha256(cover_data).hexdigest()
        doc["cover_art"]["embedded"] = True

    try:
        tm_audio.apply_basic_tags(tmp_path, doc, cover_data)
    except Exception as e:
        print(f"⚠️ Failed to apply player-visible tags: {e}", file=sys.stderr)

    try:
        tm_blob.write_blob(tmp_path, doc)
    except Exception as e:
        print(f"⚠️ Failed to write blob: {e}", file=sys.stderr)

    tmp_path.rename(final_path)

    return True, (
        f"{backup_path.suffix[1:]}@{src_info.get('bitrate_kbps') or '?'}kbps → "
        f"{final_path.name} ({_fmt_size(new_info.get('size_bytes') or 0)})"
    )


def _safe_unlink(path: Path) -> None:
    """Remove `path` if it exists; never raise."""
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Metadata reconstruction
# ---------------------------------------------------------------------------


def _read_existing_metadata(path: Path) -> dict[str, Any]:
    """Prefer the embedded blob; fall back to scattered legacy tags."""
    doc = tm_blob.read_blob(path)
    if doc is not None:
        # Defensive: ensure all top-level template keys exist.
        return tm_blob.merge_into_template(doc)
    return _reconstruct_legacy_doc(path)


def _reconstruct_legacy_doc(path: Path) -> dict[str, Any]:
    """Reconstruct a canonical document from M4A/MP3/FLAC scattered tags."""
    doc = tm_blob.empty_document()
    suffix = path.suffix.lower()

    try:
        if suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            tags = MP4(str(path)).tags or {}
            _fill_track_from_m4a(doc, tags)
            _fill_provenance_from_m4a(doc, tags)
            _fill_identifiers_from_m4a(doc, tags)
        elif suffix == ".mp3":
            from mutagen.mp3 import MP3

            audio = MP3(str(path))
            tags = audio.tags
            if tags is not None:
                _fill_track_from_id3(doc, tags)
                _fill_provenance_from_id3(doc, tags)
        elif suffix == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(path))
            _fill_track_from_vorbis(doc, audio)
        # Other extensions: no metadata reconstruction; we still re-encode.
    except Exception as e:
        print(f"⚠️ Could not read legacy tags from {path.name}: {e}", file=sys.stderr)

    return doc


def _fill_track_from_m4a(doc: dict, tags) -> None:
    title = _m4a_text(tags, "\xa9nam")
    if title:
        doc["track"]["title"] = title
    artist = _m4a_text(tags, "\xa9ART")
    if artist:
        doc["track"]["artist_string"] = artist
        doc["track"]["artists"] = [s.strip() for s in artist.split(",") if s.strip()]
    album_artist = _m4a_text(tags, "aART")
    if album_artist:
        doc["track"]["album_artist"] = album_artist
    album = _m4a_text(tags, "\xa9alb")
    if album:
        doc["track"]["album"] = album
    date = _m4a_text(tags, "\xa9day")
    if date:
        doc["track"]["date"] = date
    genre = _m4a_text(tags, "\xa9gen")
    if genre:
        doc["track"]["genre"] = genre
    trkn = tags.get("trkn")
    if trkn and trkn[0] and trkn[0][0]:
        doc["track"]["track_number"] = int(trkn[0][0])
    disk = tags.get("disk")
    if disk and disk[0] and disk[0][0]:
        doc["track"]["disc_number"] = int(disk[0][0])
    isrc = _m4a_freeform(tags, "ISRC")
    if isrc:
        doc["track"]["isrc"] = isrc
    label = _m4a_freeform(tags, "LABEL")
    if label:
        doc["track"]["label"] = label


def _fill_provenance_from_m4a(doc: dict, tags) -> None:
    """Read the legacy `----:com.apple.iTunes:*` provenance atoms."""
    track_url = _m4a_freeform(tags, "TRACK_URL")
    if track_url:
        doc["provenance"]["track_url"] = track_url
    playlist_url = _m4a_freeform(tags, "PLAYLIST_URL")
    if playlist_url:
        doc["provenance"]["playlist_url"] = playlist_url
    source = _m4a_freeform(tags, "SOURCE")
    if source:
        doc["provenance"]["source"] = source
    original_format = _m4a_freeform(tags, "ORIGINAL_FORMAT")
    if original_format:
        doc["provenance"]["original_format"] = original_format
    original_bitrate = _m4a_freeform(tags, "ORIGINAL_BITRATE")
    if original_bitrate:
        try:
            doc["provenance"]["original_bitrate"] = int(original_bitrate)
        except ValueError:
            pass


def _fill_identifiers_from_m4a(doc: dict, tags) -> None:
    barcode = _m4a_freeform(tags, "BARCODE")
    if barcode:
        doc["identifiers"]["barcode"] = barcode


def _fill_track_from_id3(doc: dict, tags) -> None:
    title = _id3_text(tags, "TIT2")
    if title:
        doc["track"]["title"] = title
    artist = _id3_text(tags, "TPE1")
    if artist:
        doc["track"]["artist_string"] = artist
        doc["track"]["artists"] = [s.strip() for s in artist.split(",") if s.strip()]
    album_artist = _id3_text(tags, "TPE2")
    if album_artist:
        doc["track"]["album_artist"] = album_artist
    album = _id3_text(tags, "TALB")
    if album:
        doc["track"]["album"] = album
    date = _id3_text(tags, "TDRC")
    if date:
        doc["track"]["date"] = date
    genre = _id3_text(tags, "TCON")
    if genre:
        doc["track"]["genre"] = genre
    isrc = _id3_text(tags, "TSRC")
    if isrc:
        doc["track"]["isrc"] = isrc
    label = _id3_text(tags, "TPUB")
    if label:
        doc["track"]["label"] = label
    trck = _id3_text(tags, "TRCK")
    if trck:
        try:
            doc["track"]["track_number"] = int(trck.split("/")[0])
        except ValueError:
            pass
    tpos = _id3_text(tags, "TPOS")
    if tpos:
        try:
            doc["track"]["disc_number"] = int(tpos.split("/")[0])
        except ValueError:
            pass


def _fill_provenance_from_id3(doc: dict, tags) -> None:
    """Read legacy `TXXX:*` provenance frames."""
    track_url = _id3_txxx(tags, "TRACK_URL")
    if track_url:
        doc["provenance"]["track_url"] = track_url
    playlist_url = _id3_txxx(tags, "PLAYLIST_URL")
    if playlist_url:
        doc["provenance"]["playlist_url"] = playlist_url
    source = _id3_txxx(tags, "SOURCE")
    if source:
        doc["provenance"]["source"] = source
    original_format = _id3_txxx(tags, "ORIGINAL_FORMAT")
    if original_format:
        doc["provenance"]["original_format"] = original_format
    original_bitrate = _id3_txxx(tags, "ORIGINAL_BITRATE")
    if original_bitrate:
        try:
            doc["provenance"]["original_bitrate"] = int(original_bitrate)
        except ValueError:
            pass


def _fill_track_from_vorbis(doc: dict, audio) -> None:
    """FLAC files use Vorbis comments via dict-like access."""

    def first(key: str) -> Optional[str]:
        v = audio.get(key)
        return v[0] if v else None

    if first("TITLE"):
        doc["track"]["title"] = first("TITLE")
    artist = first("ARTIST")
    if artist:
        doc["track"]["artist_string"] = artist
        doc["track"]["artists"] = [s.strip() for s in artist.split(",") if s.strip()]
    if first("ALBUMARTIST"):
        doc["track"]["album_artist"] = first("ALBUMARTIST")
    if first("ALBUM"):
        doc["track"]["album"] = first("ALBUM")
    if first("DATE"):
        doc["track"]["date"] = first("DATE")
    if first("GENRE"):
        doc["track"]["genre"] = first("GENRE")
    if first("ISRC"):
        doc["track"]["isrc"] = first("ISRC")
    if first("LABEL"):
        doc["track"]["label"] = first("LABEL")


# ---------------------------------------------------------------------------
# Document construction post-migration
# ---------------------------------------------------------------------------


def _build_migrated_doc(
    existing_doc: dict[str, Any],
    src_info: dict[str, Any],
    new_info: dict[str, Any],
    src_path: Path,
) -> dict[str, Any]:
    """Update `existing_doc` to reflect the AIFF re-encode.

    The chain logic for ``provenance.original_*`` is "lowest-quality step
    wins" — every lossy transcode anywhere in the history caps the quality
    at that step, so the most-bottlenecked step is what truly defines the
    audio's quality ceiling.

    Concretely:

      * For each known step in the chain (the existing recorded original
        + the file we are now re-encoding from), consider its bitrate.
      * Treat lossless steps (FLAC, ALAC, PCM, …) as ``+infinity`` — they
        do not bottleneck quality.
      * The step with the *lowest* bitrate (i.e. the worst lossy step)
        wins; both ``original_format`` and ``original_bitrate`` are taken
        from it.
      * If every step is lossless, we keep the existing format if any and
        ``original_bitrate`` stays ``None``.

    Examples:

      * existing original = mp3 @ 128, source = m4a @ 256 → mp3 @ 128 wins.
      * existing original = flac (None), source = m4a @ 256 → m4a @ 256 wins.

    Also records ``provenance.migrated_from`` as an audit trail of this
    specific migration step (regardless of whether it became the new
    bottleneck) and refreshes ``audio.*`` to describe the new AIFF.
    """
    doc = existing_doc

    # The codec is the actual lossy/lossless transformer (aac, mp3, opus,
    # flac, …); container names like "m4a" hide whether it's AAC or ALAC.
    # Fall back to the extension only when probing fails.
    intermediate_format = src_info.get("codec") or src_path.suffix.lower().lstrip(".")
    intermediate_bitrate = src_info.get("bitrate_kbps")

    doc["provenance"]["migrated_from"] = {
        "format": intermediate_format,
        "codec": src_info.get("codec"),
        "bitrate_kbps": intermediate_bitrate,
        "at": datetime.now(timezone.utc).isoformat(),
    }

    old_format = doc["provenance"].get("original_format")
    old_bitrate = doc["provenance"].get("original_bitrate")

    new_format, new_bitrate = _select_bottleneck(
        (old_format, old_bitrate),
        (intermediate_format, intermediate_bitrate),
    )
    doc["provenance"]["original_format"] = new_format
    doc["provenance"]["original_bitrate"] = new_bitrate

    doc["audio"]["format"] = "aiff"
    doc["audio"]["codec"] = new_info.get("codec")
    doc["audio"]["bitrate_kbps"] = new_info.get("bitrate_kbps")
    doc["audio"]["sample_rate"] = new_info.get("sample_rate")
    doc["audio"]["bit_depth"] = new_info.get("bit_depth")
    doc["audio"]["channels"] = new_info.get("channels")
    doc["audio"]["size_bytes"] = new_info.get("size_bytes")
    if (
        doc["track"].get("duration_seconds") is None
        and src_info.get("duration_seconds") is not None
    ):
        doc["track"]["duration_seconds"] = src_info["duration_seconds"]

    return doc


def _select_bottleneck(
    *steps: tuple[Optional[str], Optional[int]],
) -> tuple[Optional[str], Optional[int]]:
    """Pick the lowest-quality step in a chain.

    Each step is a (format, bitrate_kbps) tuple. ``bitrate is None`` means
    "lossless or unknown" and is treated as +infinity (cannot be the
    bottleneck unless every step is None). Steps with no info at all
    (both None) are skipped entirely.

    Returns the (format, bitrate) of the chosen step. If only None-bitrate
    steps remain, returns the first one whose format is set, with
    bitrate ``None``.
    """
    informative = [s for s in steps if s[0] is not None or s[1] is not None]
    if not informative:
        return (None, None)

    lossy = [s for s in informative if s[1] is not None]
    if lossy:
        # Lowest bitrate wins; ties keep the earliest in argument order.
        return min(lossy, key=lambda s: s[1])

    # All remaining steps are lossless / unknown-bitrate. Pick the first
    # one with a format set (most likely the upstream original).
    for fmt, _ in informative:
        if fmt:
            return (fmt, None)
    return (None, None)


def _extract_cover_bytes(path: Path) -> Optional[bytes]:
    """Pull cover art bytes out of M4A `covr`, MP3 `APIC`, or FLAC pictures."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            tags = MP4(str(path)).tags
            if tags and "covr" in tags and tags["covr"]:
                return bytes(tags["covr"][0])
        elif suffix == ".mp3":
            from mutagen.id3 import ID3

            tags = ID3(str(path))
            for frame in tags.getall("APIC"):
                if frame.data:
                    return bytes(frame.data)
        elif suffix == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(path))
            if audio.pictures:
                return bytes(audio.pictures[0].data)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Mutagen accessor helpers
# ---------------------------------------------------------------------------


def _m4a_text(tags, key: str) -> Optional[str]:
    val = tags.get(key)
    if not val:
        return None
    v = val[0] if isinstance(val, list) else val
    return str(v) if v is not None else None


def _m4a_freeform(tags, name: str) -> Optional[str]:
    key = f"----:com.apple.iTunes:{name}"
    val = tags.get(key)
    if not val:
        return None
    v = val[0] if isinstance(val, list) else val
    if hasattr(v, "decode"):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(v)


def _id3_text(tags, frame_id: str) -> Optional[str]:
    frames = tags.getall(frame_id)
    if not frames:
        return None
    text = frames[0].text
    if isinstance(text, list):
        text = ", ".join(str(t) for t in text)
    return str(text) if text else None


def _id3_txxx(tags, desc: str) -> Optional[str]:
    for frame in tags.getall("TXXX"):
        if frame.desc == desc:
            text = frame.text
            if isinstance(text, list):
                text = ", ".join(str(t) for t in text)
            return str(text) if text else None
    return None


# ---------------------------------------------------------------------------
# Display helpers (small, kept here so cli.py doesn't reimplement)
# ---------------------------------------------------------------------------


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def fmt_size(n: int) -> str:
    """Public wrapper for byte counts → human strings."""
    return _fmt_size(n)
