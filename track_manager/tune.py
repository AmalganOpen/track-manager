"""Pitch-tune a single library track (pitch shift, BPM unchanged)."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Optional

from . import audio as tm_audio
from . import blob as tm_blob
from .library import (  # noqa: F401 — re-exported for older imports/tests
    AUDIO_EXTENSIONS,
    find_matching_tracks,
    list_library_tracks,
    pick_track,
    resolve_track,
)


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

    # Warn only — retuning stacks pitch-shift artifacts on already-processed audio.
    from .check_tuning import read_recorded_tuning_label

    prior = read_recorded_tuning_label(src)
    if prior:
        print(
            f"⚠️ Already tuned ({prior}). Another pass stacks artifacts on "
            f"the current audio — prefer restoring/redownloading a clean "
            f"copy first.",
            file=sys.stderr,
        )

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
