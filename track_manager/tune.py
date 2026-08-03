"""Pitch-tune a single library track (vinyl-style BPM%/cents shift)."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Optional

from . import audio as tm_audio
from . import blob as tm_blob

AUDIO_EXTENSIONS = {".aiff", ".aif", ".m4a", ".mp4", ".mp3"}


def list_library_tracks(library_dir: Path) -> list[Path]:
    """Return audio files directly under `library_dir` (non-recursive)."""
    if not library_dir.is_dir():
        return []
    tracks: list[Path] = []
    for path in sorted(library_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            tracks.append(path)
    return tracks


def find_matching_tracks(query: str, library_dir: Path) -> list[Path]:
    """Case-insensitive substring match against library filenames.

    Matches both the full filename and the stem so users can type a partial
    title without the extension or artist prefix.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    matches: list[Path] = []
    for path in list_library_tracks(library_dir):
        name = path.name.lower()
        stem = path.stem.lower()
        if needle in name or needle in stem:
            matches.append(path)
    return matches


def pick_track(matches: list[Path]) -> Optional[Path]:
    """Interactively pick one track from `matches`. Returns None if cancelled."""
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    print(f"🔍 Found {len(matches)} matching tracks:")
    for i, path in enumerate(matches, 1):
        print(f"  [{i}] {path.name}")
    print("  [q] Cancel", flush=True)

    while True:
        choice = input(f"Choice [1-{len(matches)}/q]: ").strip().lower()
        if choice in ("q", "quit", "cancel"):
            return None
        try:
            idx = int(choice)
        except ValueError:
            print(f"Invalid choice. Enter 1-{len(matches)} or q.")
            continue
        if 1 <= idx <= len(matches):
            return matches[idx - 1]
        print(f"Invalid choice. Enter 1-{len(matches)} or q.")


def resolve_track(
    track_arg: str,
    *,
    absolute: bool,
    library_dir: Path,
) -> Path:
    """Resolve a track argument to an existing audio file path.

    With ``absolute=True``, treat `track_arg` as a filesystem path.
    Otherwise search the library for a partial title/filename match and
    prompt when there are multiple hits.
    """
    if absolute:
        path = Path(track_arg).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Track not found: {path}")
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError(
                f"Unsupported audio format: {path.suffix}. "
                f"Expected one of: {', '.join(sorted(AUDIO_EXTENSIONS))}"
            )
        return path.resolve()

    matches = find_matching_tracks(track_arg, library_dir)
    if not matches:
        raise FileNotFoundError(f"No tracks matching {track_arg!r} in {library_dir}")

    chosen = pick_track(matches)
    if chosen is None:
        raise KeyboardInterrupt
    return chosen


def format_tune_label(*, cents: float, bpm_percent: Optional[float]) -> str:
    """Human-readable tuning label for tags (e.g. ``+2%``, ``+50c``)."""
    if bpm_percent is not None:
        return f"{bpm_percent:+g}%"
    rounded = round(cents)
    if abs(cents - rounded) < 1e-6:
        return f"{rounded:+d}c"
    return f"{cents:+.2f}c"


def _source_title(src: Path) -> str:
    """Best-effort title from blob, else filename stem."""
    doc = tm_blob.read_blob(src)
    if doc:
        title = doc.get("track", {}).get("title")
        if title:
            return str(title)
    stem = src.stem
    if " - " in stem:
        return stem.split(" - ", 1)[1].strip() or stem
    return stem


def tune_track(
    src: Path,
    *,
    cents: float,
    bpm_percent: Optional[float] = None,
    dry_run: bool = False,
) -> Path:
    """Pitch-shift `src` in place. Returns the same path.

    Replaces only the audio inside the original container (same path / name),
    changing pitch while keeping tempo/BPM the same. Records the tune in tags
    (title + ``TM_TUNING``) and the metadata blob.
    """
    target_format = tm_audio.format_from_path(src)
    label = format_tune_label(cents=cents, bpm_percent=bpm_percent)
    ratio = tm_audio.cents_to_ratio(cents)

    print(f"🎵 Track: {src.name}")
    if bpm_percent is not None:
        print(
            f"🎚️ Tune: {bpm_percent:+g}% pitch → {cents:+.2f} cents "
            f"(ratio {ratio:.6f}, BPM unchanged)"
        )
    else:
        print(f"🎚️ Tune: {cents:+.2f} cents " f"(ratio {ratio:.6f}, BPM unchanged)")
    print("📁 In-place (same file, tags updated)")

    if dry_run:
        print("ℹ️ Dry run — no file written")
        return src

    # Pitch into a temp sibling, restore tags, then atomically replace `src`.
    tmp = src.with_name(f".tm_tune_{os.getpid()}{src.suffix}")
    if tmp.exists():
        tmp.unlink()

    try:
        tm_audio.pitch_shift_to(src, tmp, cents, target_format)
    except tm_audio.EncodeError as e:
        print(f"❌ Tune failed: {e}", file=sys.stderr)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise

    try:
        tm_audio.copy_all_tags(src, tmp)
    except Exception as e:
        print(f"⚠️ Failed to copy original tags: {e}", file=sys.stderr)

    base_title = _source_title(src)
    marker = f"({label})"
    new_title = (
        base_title
        if str(base_title).rstrip().endswith(marker)
        else f"{base_title} {marker}"
    )
    tuning_label = label if bpm_percent is not None else f"{cents:+.2f} cents"
    if bpm_percent is not None:
        tuning_label = f"{label} ({cents:+.2f} cents)"

    try:
        tm_audio.apply_tuning_tags(tmp, title=new_title, tuning_label=tuning_label)
    except Exception as e:
        print(f"⚠️ Failed to write tuning tags: {e}", file=sys.stderr)

    # Refresh blob fields that changed; keep everything else from the source.
    doc = tm_blob.read_blob(tmp)
    if doc is None:
        doc = tm_blob.empty_document()
        if " - " in src.stem:
            artist, title = src.stem.split(" - ", 1)
            doc["track"]["artist_string"] = artist.strip() or None
            doc["track"]["artists"] = [artist.strip()] if artist.strip() else []
            doc["track"]["title"] = title.strip() or src.stem
        else:
            doc["track"]["title"] = src.stem
    else:
        doc = copy.deepcopy(doc)

    track = doc.setdefault("track", {})
    track["title"] = new_title

    info = tm_audio.probe_audio(tmp)
    doc.setdefault("audio", {})
    doc["audio"]["format"] = target_format
    for key in (
        "codec",
        "bitrate_kbps",
        "sample_rate",
        "bit_depth",
        "channels",
        "size_bytes",
    ):
        if info.get(key) is not None:
            doc["audio"][key] = info[key]
    if info.get("duration_seconds") is not None:
        track["duration_seconds"] = info["duration_seconds"]

    notes = doc.setdefault("user", {}).get("notes")
    tune_note = f"tuned {tuning_label}"
    if notes:
        if tune_note not in str(notes):
            doc["user"]["notes"] = f"{notes}; {tune_note}"
    else:
        doc.setdefault("user", {})
        doc["user"]["notes"] = tune_note

    try:
        tm_blob.write_blob(tmp, doc)
    except Exception as e:
        print(f"⚠️ Failed to write metadata blob: {e}", file=sys.stderr)

    try:
        os.replace(tmp, src)
    except OSError as e:
        print(f"❌ Failed to replace original file: {e}", file=sys.stderr)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise

    print(f"✅ Tuned in place: {src.name}")
    print()
    return src
