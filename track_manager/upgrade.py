"""Upgrade low/mid quality tracks to higher quality versions."""

import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mutagen import File as MutagenFile

from . import audio as tm_audio
from . import blob as tm_blob
from .quality import format_bitrate, get_audio_info

# Audio extensions we consider for upgrade. AIFF is included because migrated
# files have a high PCM container bitrate but may still derive from a low-quality
# source (recorded in the embedded blob's provenance.original_bitrate).
_UPGRADE_EXTS = (".m4a", ".mp3", ".flac", ".aiff", ".aif")

# Containers we know how to re-encode the upgraded download into. Right now
# AIFF is the only post-process target — that's the format the migration tool
# normalises everything to, and the only one where the file's container bitrate
# diverges from the underlying source quality.
_REENCODE_TARGETS = {".aiff", ".aif"}


def _read_m4a_freeform(audio: MutagenFile, key: str) -> Optional[str]:
    """Read a freeform iTunes atom or plain tag from a mutagen file."""
    tag = audio.tags.get(f"----:com.apple.iTunes:{key}")
    if tag:
        val = tag[0] if isinstance(tag, list) else tag
        return val.decode("utf-8") if hasattr(val, "decode") else str(val)
    tag = audio.tags.get(key)
    if tag:
        val = tag[0] if isinstance(tag, list) else tag
        return str(val)
    return None


def read_track_url(file_path: Path) -> Optional[str]:
    """Read the TRACK_URL provenance tag from an audio file."""
    try:
        audio = MutagenFile(str(file_path))
        if audio and audio.tags:
            return _read_m4a_freeform(audio, "TRACK_URL")
    except Exception:
        pass
    return None


def read_original_provenance(file_path: Path) -> dict:
    """Read all provenance tags from an audio file.

    Returns a dict with any of: track_url, playlist_url, source,
    original_format, original_bitrate, isrc — omitting keys whose tags
    are absent so callers can use .get() with their own defaults.
    """
    result: dict = {}
    try:
        audio = MutagenFile(str(file_path))
        if not audio or not audio.tags:
            return result
        for key in ("TRACK_URL", "PLAYLIST_URL", "SOURCE", "ORIGINAL_FORMAT",
                    "ORIGINAL_BITRATE", "ISRC", "UPGRADE_ATTEMPTS",
                    "LAST_UPGRADE_ATTEMPT_AT"):
            val = _read_m4a_freeform(audio, key)
            if val is not None:
                result[key.lower()] = val
    except Exception:
        pass
    return result


def _read_upgrade_attempts(file_path: Path) -> int:
    """Read the number of times we've attempted to upgrade this track.

    Looks at the blob's ``provenance.upgrade_attempts`` first, then the legacy
    ``UPGRADE_ATTEMPTS`` freeform tag for files that don't have a blob yet.
    Returns 0 if neither is present.
    """
    doc = tm_blob.read_blob(file_path)
    if doc is not None:
        prov = doc.get("provenance") or {}
        n = prov.get("upgrade_attempts")
        if isinstance(n, (int, float)) and n >= 0:
            return int(n)

    legacy = read_original_provenance(file_path)
    raw = legacy.get("upgrade_attempts")
    if raw is not None:
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            return 0
    return 0


def _write_upgrade_attempts(file_path: Path, attempts: int) -> None:
    """Persist the upgrade-attempt counter on `file_path`.

    Prefers updating the blob in place when one exists. For legacy files
    without a blob we fall back to writing an ``UPGRADE_ATTEMPTS`` freeform
    tag (m4a / mp3) so subsequent runs can still see the count without
    forcing a full blob rewrite on first sight.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    suffix = file_path.suffix.lower()

    doc = tm_blob.read_blob(file_path)
    if doc is not None:
        merged = tm_blob.merge_into_template(doc)
        merged["provenance"]["upgrade_attempts"] = int(attempts)
        merged["provenance"]["last_upgrade_attempt_at"] = now_iso
        tm_blob.write_blob(file_path, merged)
        return

    # No blob — write a freeform tag instead. AIFF without a blob is unusual
    # (migration always writes one) so we only handle m4a/mp3 here. For other
    # formats we silently skip rather than mutate something we don't own.
    try:
        from mutagen.mp4 import MP4
        from mutagen.id3 import ID3, TXXX

        if suffix == ".m4a":
            audio = MP4(str(file_path))
            if audio.tags is None:
                audio.add_tags()
            audio.tags["----:com.apple.iTunes:UPGRADE_ATTEMPTS"] = str(attempts).encode("utf-8")
            audio.tags["----:com.apple.iTunes:LAST_UPGRADE_ATTEMPT_AT"] = now_iso.encode("utf-8")
            audio.save()
        elif suffix == ".mp3":
            try:
                tags = ID3(str(file_path))
            except Exception:
                tags = ID3()
            tags.delall("TXXX:UPGRADE_ATTEMPTS")
            tags.delall("TXXX:LAST_UPGRADE_ATTEMPT_AT")
            tags.add(TXXX(encoding=3, desc="UPGRADE_ATTEMPTS", text=str(attempts)))
            tags.add(TXXX(encoding=3, desc="LAST_UPGRADE_ATTEMPT_AT", text=now_iso))
            tags.save(str(file_path))
    except Exception as e:
        print(f"  ⚠️  Failed to record upgrade attempt counter: {e}", file=sys.stderr)


def _patch_playlist_url(file_path: Path, playlist_url: str) -> None:
    """Write PLAYLIST_URL back into a file whose re-download left it absent."""
    try:
        from mutagen.mp4 import MP4
        from mutagen.id3 import ID3, TXXX

        if file_path.suffix.lower() == ".m4a":
            audio = MP4(str(file_path))
            audio["----:com.apple.iTunes:PLAYLIST_URL"] = playlist_url.encode("utf-8")
            audio.save()
        elif file_path.suffix.lower() == ".mp3":
            try:
                audio = ID3(str(file_path))
            except Exception:
                audio = ID3()
            audio.add(TXXX(encoding=3, desc="PLAYLIST_URL", text=playlist_url))
            audio.save(str(file_path))
    except Exception:
        pass


def _source_quality(file_path: Path) -> dict:
    """Resolve a file's *source* quality, not its container's quality.

    Looks in this order:
      1. Embedded blob: ``provenance.original_bitrate`` / ``original_format``
         and ``provenance.track_url`` (current source of truth).
      2. Legacy ``ORIGINAL_BITRATE`` / ``TRACK_URL`` freeform tags (pre-blob
         files written by older track-manager versions).
      3. Container probe (the file's own bitrate) — used only as a last resort
         for files with no provenance at all.

    Returns a dict with keys:
        bitrate_kbps: int | None       — source bitrate in kbps
        format:       str  | None      — source container/codec
        track_url:    str  | None
        from_blob:    bool             — provenance came from the blob
        from_container: bool           — bitrate came from container probe
    """
    out: dict = {
        "bitrate_kbps": None,
        "format": None,
        "track_url": None,
        "from_blob": False,
        "from_container": False,
    }

    doc = tm_blob.read_blob(file_path)
    if doc is not None:
        prov = doc.get("provenance") or {}
        ob = prov.get("original_bitrate")
        if isinstance(ob, (int, float)) and ob > 0:
            out["bitrate_kbps"] = int(ob)
        of = prov.get("original_format")
        if isinstance(of, str) and of:
            out["format"] = of
        tu = prov.get("track_url")
        if isinstance(tu, str) and tu:
            out["track_url"] = tu
        out["from_blob"] = True

    if out["track_url"] is None:
        legacy_url = read_track_url(file_path)
        if legacy_url:
            out["track_url"] = legacy_url

    if out["bitrate_kbps"] is None:
        legacy = read_original_provenance(file_path)
        legacy_kbps = legacy.get("original_bitrate")
        if legacy_kbps:
            try:
                out["bitrate_kbps"] = int(float(legacy_kbps))
            except (TypeError, ValueError):
                pass
        if out["format"] is None and legacy.get("original_format"):
            out["format"] = legacy["original_format"]

    if out["bitrate_kbps"] is None:
        info = get_audio_info(file_path)
        if info and info.get("bitrate", 0) > 0:
            out["bitrate_kbps"] = info["bitrate"] // 1000
            out["from_container"] = True
            if out["format"] is None:
                out["format"] = info.get("format")

    return out


def find_upgradeable_tracks(
    library_dir: Path,
    threshold_kbps: int = 256,
    max_attempts: Optional[int] = 0,
) -> list[dict]:
    """Scan library for tracks whose *source* quality is below the threshold
    and that have a TRACK_URL we can re-download from.

    Source quality is read from the embedded blob's
    ``provenance.original_bitrate`` first (so an AIFF migrated from a 128 kbps
    m4a is correctly seen as a 128 kbps track), then from legacy tags, and
    only finally from the file's container bitrate.

    Args:
        library_dir: Directory to scan
        threshold_kbps: Bitrate threshold in kbps; tracks below this are candidates
        max_attempts: Skip tracks that have already been attempted more than
            this many times. ``0`` (the default) means "only never-attempted
            tracks". Pass ``None`` to disable the filter entirely.

    Returns:
        List of dicts with keys:
            path:       Path to file
            track_url:  Source URL to re-download from
            bitrate:    Source bitrate in *bps* (kept for back-compat with
                        callers that use ``format_bitrate``)
            format:     Source format string (e.g. "mp3", "aac")
            attempts:   Number of prior upgrade attempts on this track
    """
    candidates: list[dict] = []
    seen: set[Path] = set()

    for entry in sorted(library_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() not in _UPGRADE_EXTS:
            continue
        # Dedupe across case-insensitive filesystems where iterdir may yield
        # the same file once even though earlier glob-based code listed it
        # twice (e.g. *.m4a + *.M4A).
        resolved = entry.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)

        src = _source_quality(entry)
        if src["bitrate_kbps"] is None or src["bitrate_kbps"] >= threshold_kbps:
            continue
        if not src["track_url"]:
            continue

        attempts = _read_upgrade_attempts(entry)
        if max_attempts is not None and attempts > max_attempts:
            continue

        candidates.append(
            {
                "path": entry,
                "track_url": src["track_url"],
                "bitrate": src["bitrate_kbps"] * 1000,
                "format": src["format"] or entry.suffix.lstrip(".").lower(),
                "attempts": attempts,
                "source_from_blob": src["from_blob"],
                "source_from_container": src["from_container"],
            }
        )

    return candidates


def upgrade_track(
    original_path: Path,
    track_url: str,
    config: "Config",  # type: ignore[name-defined]
    verbose: bool = False,
    downloader: Optional["Downloader"] = None,  # type: ignore[name-defined]
) -> tuple[bool, str]:
    """Download a higher-quality version of a track and replace the original in-place.

    The upgraded file is placed at the same path as the original (same
    directory + same stem).  If the new file has a different extension
    the old file is removed and the new extension is used; the caller
    should inform the user so they can relocate the track in Rekordbox.

    Args:
        original_path: Path to the existing (lower-quality) file
        track_url: Source URL stored in the file's TRACK_URL tag
        config: Config instance
        verbose: Print extra detail
        downloader: Optional pre-created Downloader to reuse (avoids re-initialising
            spotdl's global Spotify client on every track).

    Returns:
        Tuple of (success, message)
    """
    from .downloader import Downloader

    # Snapshot provenance from the original file before we replace it.
    # Fields like PLAYLIST_URL represent user context (which playlist this
    # track was originally downloaded from) and won't be re-populated by a
    # single-track re-download. We also remember the original's source
    # bitrate so we can decide whether the new download is actually better.
    original_provenance = read_original_provenance(original_path)
    original_src = _source_quality(original_path)
    original_ext = original_path.suffix.lower()
    must_reencode = original_ext in _REENCODE_TARGETS

    # Record the attempt up front so a crash mid-download or a hard failure
    # still leaves a counter behind on disk — preventing the same track from
    # being retried forever in a default "attempts == 0" run. We re-read the
    # blob *after* writing so original_blob carries the bumped counter into
    # _refresh_aiff_metadata's rewritten document below.
    prev_attempts = _read_upgrade_attempts(original_path)
    new_attempts = prev_attempts + 1
    _write_upgrade_attempts(original_path, new_attempts)
    original_blob = tm_blob.read_blob(original_path)

    with tempfile.TemporaryDirectory(prefix="tm-upgrade-") as tmp_str:
        tmp_dir = Path(tmp_str)

        if downloader is not None:
            # Reuse the existing downloader but point it at the per-track temp dir.
            downloader.output_dir = tmp_dir
        else:
            downloader = Downloader(config, output_dir=tmp_dir)

        try:
            download_result = downloader.download(track_url, format="auto", show_header=False)
        except Exception as e:
            return False, f"Download failed: {e}"
        if download_result is False:
            return False, "Source download failed (no file saved)"

        # Find what was downloaded
        audio_exts = {
            ".m4a",
            ".mp3",
            ".flac",
            ".wav",
            ".ogg",
            ".aac",
            ".opus",
            ".aiff",
            ".aif",
        }
        new_files = [
            f for f in tmp_dir.iterdir() if f.suffix.lower() in audio_exts
        ]

        if not new_files:
            return False, "Download produced no audio file"

        if len(new_files) > 1:
            # Prefer m4a, then flac, then anything
            def _rank(p: Path) -> int:
                return {".m4a": 0, ".flac": 1, ".mp3": 2}.get(p.suffix.lower(), 9)

            new_files.sort(key=_rank)

        new_file = new_files[0]

        # Verify the new download is actually better quality than the
        # original *source* (not the original file's container bitrate;
        # an AIFF migrated from a 128 kbps mp3 has a 1411 kbps PCM
        # container but its real source quality is still 128 kbps).
        new_probe = tm_audio.probe_audio(new_file)
        new_doc = tm_blob.read_blob(new_file)
        new_kbps = new_probe.get("bitrate_kbps")
        if isinstance(new_doc, dict):
            prov = new_doc.get("provenance") or {}
            prov_kbps = prov.get("original_bitrate")
            if isinstance(prov_kbps, (int, float)) and prov_kbps > 0:
                new_kbps = int(prov_kbps)
        src_kbps = original_src.get("bitrate_kbps")
        if new_kbps and src_kbps and new_kbps <= src_kbps:
            if verbose:
                print(
                    f"  ⚠️  New download ({new_kbps} kbps) is not better "
                    f"than source ({src_kbps} kbps), skipping"
                )
            return False, (
                f"New download ({new_kbps} kbps) is not better than "
                f"source ({src_kbps} kbps)"
            )

        # Post-process: bring the new file into the same container as the
        # original (so e.g. an upgraded AIFF stays AIFF instead of silently
        # downgrading the file's container to m4a/flac).
        if must_reencode:
            # The downloader may already have produced AIFF (e.g. smart
            # downloads with target_format=auto). Re-encoding AIFF->AIFF can
            # fail when ffmpeg sees identical input/output paths.
            if new_file.suffix.lower() in {".aiff", ".aif"}:
                staged_path = new_file
            else:
                staged_path = tmp_dir / f"{original_path.stem}.aiff"
                try:
                    tm_audio.encode_to_aiff(new_file, staged_path)
                except tm_audio.EncodeError as e:
                    return False, f"Re-encode to AIFF failed: {e}"
            dest_path = original_path  # AIFF stays AIFF, same path
            extension_changed = False
            shutil.move(str(staged_path), str(dest_path))
            try:
                _refresh_aiff_metadata(
                    dest_path,
                    original_blob=original_blob,
                    original_provenance=original_provenance,
                    new_source_file=new_file,
                    new_source_probe=new_probe,
                    new_source_doc=new_doc,
                )
            except Exception as e:
                # Don't fail the upgrade just because tag refresh failed —
                # the upgraded audio is already in place.
                print(f"  ⚠️  Failed to refresh metadata: {e}", file=sys.stderr)
        else:
            dest_path = original_path.with_suffix(new_file.suffix)
            extension_changed = dest_path.suffix.lower() != original_ext
            shutil.move(str(new_file), str(dest_path))

            # Restore provenance fields that a single-track re-download won't set.
            # PLAYLIST_URL is the most important: it records which playlist the
            # track originally came from and is lost when re-downloading as a
            # standalone track URL.
            if original_provenance.get("playlist_url"):
                _patch_playlist_url(dest_path, original_provenance["playlist_url"])

            # Remove the original only if the extension changed (otherwise we just
            # overwrote it via the move above)
            if extension_changed and original_path.exists():
                original_path.unlink()

        if extension_changed:
            msg = (
                f"Upgraded and saved as {dest_path.name} "
                f"(extension changed from {original_ext} → {dest_path.suffix}; "
                f"relocate in Rekordbox)"
            )
        elif must_reencode:
            new_kbps_str = f"{new_kbps} kbps" if new_kbps else "unknown bitrate"
            src_kbps_str = f"{src_kbps} kbps" if src_kbps else "unknown"
            msg = (
                f"Upgraded in-place: {dest_path.name} "
                f"(source {src_kbps_str} → {new_kbps_str}, re-encoded to AIFF)"
            )
        else:
            msg = f"Upgraded in-place: {dest_path.name}"

        return True, msg


def _refresh_aiff_metadata(
    dest_path: Path,
    *,
    original_blob: Optional[dict],
    original_provenance: dict,
    new_source_file: Path,
    new_source_probe: dict,
    new_source_doc: Optional[dict] = None,
) -> None:
    """Update an upgraded AIFF's blob + player-visible tags to reflect the new source.

    The newly downloaded file *is* the new source-of-truth for this track, so
    ``provenance.original_format`` and ``provenance.original_bitrate`` are
    overwritten with its actuals (rather than running through the migration's
    "lowest-quality step wins" bottleneck logic, which would keep the old low
    bitrate that we just spent a download upgrading away from).

    Cover art is pulled from the new download if present, otherwise we keep
    whatever cover hash the original blob recorded.
    """
    new_probe_dest = tm_audio.probe_audio(dest_path)
    intermediate_format = None
    intermediate_bitrate = None
    if isinstance(new_source_doc, dict):
        prov = new_source_doc.get("provenance") or {}
        prov_fmt = prov.get("original_format")
        if isinstance(prov_fmt, str) and prov_fmt:
            intermediate_format = prov_fmt
        prov_br = prov.get("original_bitrate")
        if isinstance(prov_br, (int, float)) and prov_br > 0:
            intermediate_bitrate = int(prov_br)
    if not intermediate_format:
        intermediate_format = (
            new_source_probe.get("codec") or new_source_file.suffix.lstrip(".").lower()
        )
    if intermediate_bitrate is None:
        intermediate_bitrate = new_source_probe.get("bitrate_kbps")

    if original_blob is not None:
        doc = tm_blob.merge_into_template(original_blob)
    else:
        doc = tm_blob.empty_document()

    doc["provenance"]["track_url"] = original_provenance.get("track_url") \
        or doc["provenance"].get("track_url")
    if original_provenance.get("playlist_url"):
        doc["provenance"]["playlist_url"] = original_provenance["playlist_url"]
    doc["provenance"]["original_format"] = intermediate_format
    doc["provenance"]["original_bitrate"] = intermediate_bitrate
    doc["provenance"]["migrated_from"] = {
        "format": intermediate_format,
        "codec": new_source_probe.get("codec"),
        "bitrate_kbps": intermediate_bitrate,
        "at": datetime.now(timezone.utc).isoformat(),
        "via": "upgrade",
    }

    doc["audio"]["format"] = "aiff"
    doc["audio"]["codec"] = new_probe_dest.get("codec")
    doc["audio"]["bitrate_kbps"] = new_probe_dest.get("bitrate_kbps")
    doc["audio"]["sample_rate"] = new_probe_dest.get("sample_rate")
    doc["audio"]["bit_depth"] = new_probe_dest.get("bit_depth")
    doc["audio"]["channels"] = new_probe_dest.get("channels")
    doc["audio"]["size_bytes"] = new_probe_dest.get("size_bytes")

    cover_data = _extract_cover_bytes_any(new_source_file)
    if cover_data:
        from hashlib import sha256
        doc["cover_art"]["sha256"] = sha256(cover_data).hexdigest()
        doc["cover_art"]["embedded"] = True

    tm_audio.apply_basic_tags(dest_path, doc, cover_data)
    tm_blob.write_blob(dest_path, doc)


def _extract_cover_bytes_any(path: Path) -> Optional[bytes]:
    """Pull cover art bytes from M4A / MP3 / FLAC. Returns None on miss/error."""
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
