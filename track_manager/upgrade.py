"""Upgrade low/mid quality tracks to higher quality versions."""

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from mutagen import File as MutagenFile

from .quality import format_bitrate, get_audio_info


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
                    "ORIGINAL_BITRATE", "ISRC"):
            val = _read_m4a_freeform(audio, key)
            if val is not None:
                result[key.lower()] = val
    except Exception:
        pass
    return result


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


def find_upgradeable_tracks(
    library_dir: Path,
    threshold_kbps: int = 256,
) -> list[dict]:
    """Scan library for tracks below quality threshold that have a TRACK_URL.

    Args:
        library_dir: Directory to scan
        threshold_kbps: Bitrate threshold in kbps; tracks below this are candidates

    Returns:
        List of dicts with keys: path, track_url, bitrate, format
    """
    candidates = []
    threshold_bps = threshold_kbps * 1000

    for pattern in ["*.m4a", "*.M4A", "*.mp3", "*.MP3", "*.flac", "*.FLAC"]:
        for file_path in sorted(library_dir.glob(pattern)):
            info = get_audio_info(file_path)
            if not info or info["bitrate"] <= 0:
                continue
            if info["bitrate"] >= threshold_bps:
                continue

            track_url = read_track_url(file_path)
            if not track_url:
                continue

            candidates.append(
                {
                    "path": file_path,
                    "track_url": track_url,
                    "bitrate": info["bitrate"],
                    "format": info["format"],
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
    # single-track re-download.
    original_provenance = read_original_provenance(original_path)

    with tempfile.TemporaryDirectory(prefix="tm-upgrade-") as tmp_str:
        tmp_dir = Path(tmp_str)

        if downloader is not None:
            # Reuse the existing downloader but point it at the per-track temp dir.
            downloader.output_dir = tmp_dir
        else:
            downloader = Downloader(config, output_dir=tmp_dir)

        try:
            downloader.download(track_url, format="auto", show_header=False)
        except Exception as e:
            return False, f"Download failed: {e}"

        # Find what was downloaded
        audio_exts = {".m4a", ".mp3", ".flac", ".wav", ".ogg", ".aac", ".opus"}
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

        # Verify the new file is actually better quality
        new_info = get_audio_info(new_file)
        if new_info and new_info["bitrate"] > 0:
            old_info = get_audio_info(original_path)
            old_bitrate = old_info["bitrate"] if old_info else 0

            if new_info["bitrate"] <= old_bitrate:
                if verbose:
                    print(
                        f"  ⚠️  New download ({format_bitrate(new_info['bitrate'])}) "
                        f"is not better than original ({format_bitrate(old_bitrate)}), skipping"
                    )
                return False, (
                    f"New download ({format_bitrate(new_info['bitrate'])}) is not "
                    f"better than original ({format_bitrate(old_bitrate)})"
                )

        # Place new file next to the original, keeping the original stem
        dest_path = original_path.with_suffix(new_file.suffix)

        extension_changed = dest_path.suffix.lower() != original_path.suffix.lower()

        # Move the new file into place
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
                f"(extension changed from {original_path.suffix} → {dest_path.suffix}; "
                f"relocate in Rekordbox)"
            )
        else:
            msg = f"Upgraded in-place: {dest_path.name}"

        return True, msg
