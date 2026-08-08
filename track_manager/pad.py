"""Pad a track to bar boundaries using the Rekordbox beat grid.

Pads the start and/or end when the file opens or closes past the threshold
beat (default: the \"3\") so the file starts on a \"1\" and/or ends on the
next \"1\". Start pads shift ANLZ + DB cue/grid times by the same offset —
no re-analysis — so loops stay consistent.

End pads keep the original body untouched and fill only the padded region
(default: quiet reverb wash; optional dry silence).
"""

from __future__ import annotations

import copy
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from . import audio as tm_audio
from . import blob as tm_blob
from . import rekordbox_db as tm_rb

BEATS_PER_BAR = 4
# Ignore pads smaller than this (float / grid noise).
_MIN_PAD_SECONDS = 0.001
# If the next "1" is within this many beats, treat the position as already
# aligned (avoids nearly-full-bar start pads when the downbeat is just after t=0).
_NEAR_ONE_BEATS = 0.5
# Waveform detail tags use 150 columns per second of audio.
_WF_DETAIL_HZ = 150


@dataclass(frozen=True)
class PadPlan:
    """Computed silence pads for one track."""

    duration_seconds: float
    beat_duration_seconds: float
    phase_start: float  # beats into the bar at t=0, in [0, 4)
    phase_end: float  # beats into the bar at EOF, in [0, 4)
    threshold_beat: int
    pad_start_seconds: float
    pad_end_seconds: float

    @property
    def needs_pad(self) -> bool:
        return self.pad_start_seconds > 0 or self.pad_end_seconds > 0


class PadError(RuntimeError):
    """User-facing pad failure."""


def beat_duration_seconds(bpms: Sequence[float]) -> float:
    """Representative beat length (seconds) from grid BPMs."""
    valid = [float(b) for b in bpms if b and b > 0]
    if not valid:
        raise PadError("Beat grid has no valid BPM values")
    valid.sort()
    mid = valid[len(valid) // 2]
    return 60.0 / mid


def phase_at_time(
    t: float,
    *,
    times: Sequence[float],
    beats: Sequence[int],
    beat_duration: float,
    beats_per_bar: int = BEATS_PER_BAR,
) -> float:
    """Bar phase at time `t` in beats ``[0, beats_per_bar)``.

    0 = on the \"1\", 1 = on the \"2\", …, 3 = on the \"4\".
    """
    if not times or not beats:
        raise PadError("Beat grid is empty")
    if beat_duration <= 0:
        raise PadError(f"Invalid beat duration: {beat_duration}")

    # Nearest grid tick as anchor.
    best_i = 0
    best_dist = abs(float(times[0]) - t)
    for i, ti in enumerate(times):
        dist = abs(float(ti) - t)
        if dist < best_dist:
            best_dist = dist
            best_i = i

    anchor_t = float(times[best_i])
    anchor_beat = int(beats[best_i])
    if anchor_beat < 1 or anchor_beat > beats_per_bar:
        raise PadError(f"Unexpected beat number in grid: {anchor_beat}")

    anchor_phase = float(anchor_beat - 1)  # beat 1 → 0.0
    phase = (anchor_phase + (t - anchor_t) / beat_duration) % beats_per_bar
    # Keep in [0, beats_per_bar); modulo can return -0.0
    if phase < 0:
        phase += beats_per_bar
    return phase


def compute_pad_plan(
    *,
    duration_seconds: float,
    times: Sequence[float],
    beats: Sequence[int],
    bpms: Sequence[float],
    threshold_beat: int = 3,
    beats_per_bar: int = BEATS_PER_BAR,
) -> PadPlan:
    """Decide start/end silence pads from the beat grid.

    Pads only when the phase is on or past ``threshold_beat`` (default 3):
    i.e. on the \"3\" or \"4\". Start pad reaches back to the previous \"1\";
    end pad reaches forward to the next \"1\".

    Phase is always modulo ``beats_per_bar`` in ``[0, beats_per_bar)``.
    Positions within ``_NEAR_ONE_BEATS`` of the next \"1\" are treated as
    already aligned for the *start* (so a downbeat just after t=0 does not
    trigger a nearly-full empty bar of silence).
    """
    if duration_seconds <= 0:
        raise PadError(f"Invalid duration: {duration_seconds}")
    if threshold_beat < 1 or threshold_beat > beats_per_bar:
        raise PadError(
            f"threshold beat must be 1–{beats_per_bar}, got {threshold_beat}"
        )

    beat_dur = beat_duration_seconds(bpms)
    phase_start = phase_at_time(
        0.0,
        times=times,
        beats=beats,
        beat_duration=beat_dur,
        beats_per_bar=beats_per_bar,
    )
    phase_end = phase_at_time(
        duration_seconds,
        times=times,
        beats=beats,
        beat_duration=beat_dur,
        beats_per_bar=beats_per_bar,
    )

    thresh_phase = float(threshold_beat - 1)  # beat 3 → 2.0

    pad_start = 0.0
    to_next_start = _beats_to_next_one(phase_start, beats_per_bar)
    # Near the upcoming "1" ⇒ already effectively aligned at the start.
    if (
        phase_start >= thresh_phase
        and to_next_start > _NEAR_ONE_BEATS
        and phase_start > _MIN_PAD_SECONDS / beat_dur
    ):
        pad_start = phase_start * beat_dur

    pad_end = 0.0
    # phase 0 == exactly on a "1" → already aligned, never pad.
    if phase_end >= thresh_phase:
        pad_end = _beats_to_next_one(phase_end, beats_per_bar) * beat_dur

    if pad_start < _MIN_PAD_SECONDS:
        pad_start = 0.0
    if pad_end < _MIN_PAD_SECONDS:
        pad_end = 0.0

    return PadPlan(
        duration_seconds=duration_seconds,
        beat_duration_seconds=beat_dur,
        phase_start=phase_start,
        phase_end=phase_end,
        threshold_beat=threshold_beat,
        pad_start_seconds=pad_start,
        pad_end_seconds=pad_end,
    )


def _beats_to_next_one(phase: float, beats_per_bar: int = BEATS_PER_BAR) -> float:
    """Beats from `phase` forward to the next \"1\" (0 when already on it)."""
    if phase < 1e-12:
        return 0.0
    return float(beats_per_bar) - phase


def format_phase(phase: float, *, beats_per_bar: int = BEATS_PER_BAR) -> str:
    """Describe bar phase for humans (phase is modulo ``beats_per_bar``).

    Examples: ``the 1``, ``the 3.5``, ``0.12 before the 1``.
    """
    to_next = _beats_to_next_one(phase, beats_per_bar)
    if phase < 1e-6 or to_next < 1e-6:
        return "the 1"
    # Prefer wrap wording when closer to the next 1 than half a beat.
    if to_next <= _NEAR_ONE_BEATS:
        return f"{to_next:.2f} before the 1".rstrip("0").rstrip(".")
    beat = int(phase) + 1  # 1..4
    frac = phase - int(phase)
    if frac < 1e-6:
        return f"the {beat}"
    label = f"{beat + frac:.2f}".rstrip("0").rstrip(".")
    return f"the {label}"


def _format_phase(phase: float) -> str:
    """Backward-compatible alias for :func:`format_phase`."""
    return format_phase(phase)


def _shift_anlz_times(anlz: object, delta_seconds: float) -> None:
    """Shift beat-grid and cue timestamps in an ANLZ file by `delta_seconds`."""
    if abs(delta_seconds) < 1e-12:
        return
    delta_ms = int(round(delta_seconds * 1000.0))

    # Beat grids (PQTZ / PQT2)
    for key in ("beat_grid", "beat_grid2"):
        if key not in anlz:  # type: ignore[operator]
            continue
        for tag in anlz.getall_tags(key):  # type: ignore[attr-defined]
            times = [float(t) + delta_seconds for t in tag.get_times()]
            tag.set_times(times)

    # Cue lists (PCOB / PCO2) — mutate entry times in ms
    for key in ("cue_list", "cue_list2"):
        if key not in anlz:  # type: ignore[operator]
            continue
        for tag in anlz.getall_tags(key):  # type: ignore[attr-defined]
            entries = getattr(tag.content, "entries", None)
            if not entries:
                continue
            for entry in entries:
                if hasattr(entry, "time") and entry.time is not None:
                    entry.time = int(entry.time) + delta_ms
                loop_time = getattr(entry, "loop_time", None)
                # -1 / 0xffffffff means \"no loop end\"
                if loop_time is not None and int(loop_time) not in (-1, 0xFFFFFFFF):
                    entry.loop_time = int(loop_time) + delta_ms


def _pad_waveform_detail(anlz: object, pad_start: float, pad_end: float) -> None:
    """Insert/append silence columns on detail waveform tags (150 Hz)."""
    n_start = int(round(pad_start * _WF_DETAIL_HZ)) if pad_start > 0 else 0
    n_end = int(round(pad_end * _WF_DETAIL_HZ)) if pad_end > 0 else 0
    if n_start == 0 and n_end == 0:
        return

    for type_code in ("PWV3", "PWV5", "PWV6", "PWV7"):
        if type_code not in anlz:  # type: ignore[operator]
            continue
        for tag in anlz.getall_tags(type_code):  # type: ignore[attr-defined]
            content = tag.content
            entries = content.entries
            entry_bytes = int(getattr(content, "len_entry_bytes", 1) or 1)
            if isinstance(entries, (bytes, bytearray)):
                head = bytes(n_start * entry_bytes)
                tail = bytes(n_end * entry_bytes)
                content.entries = head + bytes(entries) + tail
                content.len_entries = len(content.entries) // entry_bytes
                payload_len = len(content.entries)
            else:
                new_entries = [0] * n_start + list(entries) + [0] * n_end
                content.entries = new_entries
                content.len_entries = len(new_entries)
                payload_len = len(new_entries) * entry_bytes
            # Default AbstractAnlzTag.update_len is a no-op — fix len_tag so
            # AnlzFile.build() does not raise BuildTagLengthError.
            if getattr(tag, "struct", None) is not None and tag.LEN_HEADER:
                tag.struct.len_tag = int(tag.LEN_HEADER) + payload_len


def recorded_pads(path: Path) -> tuple[float, float]:
    """Cumulative silence pads recorded on `path` (blob first, then ``TM_PAD``).

    Returns ``(pad_start_seconds, pad_end_seconds)`` — zeros when nothing recorded.
    """
    doc = tm_blob.read_blob(path)
    if isinstance(doc, dict):
        processing = doc.get("processing") or {}
        ext = processing.get("padding") or processing.get("extend") or {}
        try:
            start_ms = float(ext.get("pad_start_ms") or 0.0)
            end_ms = float(ext.get("pad_end_ms") or 0.0)
            if start_ms > 0 or end_ms > 0:
                return (start_ms / 1000.0, end_ms / 1000.0)
        except (TypeError, ValueError):
            pass

    tagged = tm_audio.read_recorded_pad(path)
    if tagged is not None:
        return tagged
    return (0.0, 0.0)


def _write_pad_metadata(
    path: Path,
    *,
    total_start_seconds: float,
    total_end_seconds: float,
    threshold_beat: int,
    this_start_seconds: float,
    this_end_seconds: float,
) -> None:
    """Persist cumulative pads to ``TM_PAD`` + the embedded blob."""
    label = tm_audio.format_pad_label(
        pad_start_seconds=total_start_seconds,
        pad_end_seconds=total_end_seconds,
    )
    try:
        tm_audio.apply_pad_tags(path, pad_label=label)
    except Exception as e:
        print(f"⚠️ Failed to write TM_PAD tag: {e}", file=sys.stderr)

    doc = tm_blob.read_blob(path)
    if doc is None:
        doc = tm_blob.empty_document()
        if " - " in path.stem:
            artist, title = path.stem.split(" - ", 1)
            doc["track"]["artist_string"] = artist.strip() or None
            doc["track"]["artists"] = [artist.strip()] if artist.strip() else []
            doc["track"]["title"] = title.strip() or path.stem
        else:
            doc["track"]["title"] = path.stem
    else:
        doc = copy.deepcopy(doc)

    processing = doc.setdefault("processing", {})
    padding_doc = processing.setdefault("padding", {})
    padding_doc["pad_start_ms"] = round(total_start_seconds * 1000.0, 3)
    padding_doc["pad_end_ms"] = round(total_end_seconds * 1000.0, 3)
    padding_doc["threshold_beat"] = threshold_beat
    padding_doc["padded_at"] = datetime.now(timezone.utc).isoformat()
    padding_doc["last_pad_start_ms"] = round(this_start_seconds * 1000.0, 3)
    padding_doc["last_pad_end_ms"] = round(this_end_seconds * 1000.0, 3)

    info = tm_audio.probe_audio(path)
    doc.setdefault("audio", {})
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
        doc.setdefault("track", {})["duration_seconds"] = info["duration_seconds"]

    notes = doc.setdefault("user", {}).get("notes")
    note = f"padded {label}"
    if notes:
        if "padded start=" not in str(notes):
            doc["user"]["notes"] = f"{notes}; {note}"
        else:
            # Refresh the pad note so it reflects cumulative totals.
            parts = [p.strip() for p in str(notes).split(";")]
            parts = [p for p in parts if not p.startswith("padded start=")]
            parts.append(note)
            doc["user"]["notes"] = "; ".join(p for p in parts if p)
    else:
        doc.setdefault("user", {})
        doc["user"]["notes"] = note

    try:
        tm_blob.write_blob(path, doc)
    except Exception as e:
        print(f"⚠️ Failed to write pad metadata blob: {e}", file=sys.stderr)


def apply_recorded_pads(
    path: Path,
    *,
    pad_start_seconds: float,
    pad_end_seconds: float,
    end_tail: tm_audio.EndTailMode = tm_audio.PAD_END_TAIL_DEFAULT,
) -> bool:
    """Re-apply stored silence pads in place (e.g. after an upgrade).

    Does not touch Rekordbox. Returns True if audio was rewritten.
    """
    if pad_start_seconds <= 0 and pad_end_seconds <= 0:
        return False
    target_format = tm_audio.format_from_path(path)
    tmp = path.with_name(f".tm_pad_reapply_{os.getpid()}{path.suffix}")
    if tmp.exists():
        tmp.unlink()
    try:
        tm_audio.pad_silence_to(
            path,
            tmp,
            pad_start_seconds=max(0.0, pad_start_seconds),
            pad_end_seconds=max(0.0, pad_end_seconds),
            target_format=target_format,
            end_tail=end_tail,
        )
        try:
            tm_audio.copy_all_tags(path, tmp)
        except Exception as e:
            print(f"⚠️ Failed to copy tags while re-applying pad: {e}", file=sys.stderr)
        label = tm_audio.format_pad_label(
            pad_start_seconds=pad_start_seconds,
            pad_end_seconds=pad_end_seconds,
        )
        try:
            tm_audio.apply_pad_tags(tmp, pad_label=label)
        except Exception as e:
            print(f"⚠️ Failed to write TM_PAD while re-applying: {e}", file=sys.stderr)
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _strip_waveform_detail(anlz: object, pad_start: float, pad_end: float) -> None:
    """Remove silence columns previously inserted on detail waveform tags."""
    n_start = int(round(pad_start * _WF_DETAIL_HZ)) if pad_start > 0 else 0
    n_end = int(round(pad_end * _WF_DETAIL_HZ)) if pad_end > 0 else 0
    if n_start == 0 and n_end == 0:
        return

    for type_code in ("PWV3", "PWV5", "PWV6", "PWV7"):
        if type_code not in anlz:  # type: ignore[operator]
            continue
        for tag in anlz.getall_tags(type_code):  # type: ignore[attr-defined]
            content = tag.content
            entries = content.entries
            entry_bytes = int(getattr(content, "len_entry_bytes", 1) or 1)
            if isinstance(entries, (bytes, bytearray)):
                data = bytes(entries)
                start_b = n_start * entry_bytes
                end_b = n_end * entry_bytes
                if len(data) < start_b + end_b:
                    continue
                if end_b:
                    data = data[start_b : len(data) - end_b]
                else:
                    data = data[start_b:]
                content.entries = data
                content.len_entries = len(data) // entry_bytes
                payload_len = len(data)
            else:
                lst = list(entries)
                if len(lst) < n_start + n_end:
                    continue
                if n_end:
                    lst = lst[n_start : len(lst) - n_end]
                else:
                    lst = lst[n_start:]
                content.entries = lst
                content.len_entries = len(lst)
                payload_len = len(lst) * entry_bytes
            if getattr(tag, "struct", None) is not None and tag.LEN_HEADER:
                tag.struct.len_tag = int(tag.LEN_HEADER) + payload_len


def _clear_pad_metadata(path: Path) -> None:
    """Zero/remove recorded pad metadata from tags + blob."""
    try:
        tm_audio.clear_pad_tags(path)
    except Exception as e:
        print(f"⚠️ Failed to clear TM_PAD tag: {e}", file=sys.stderr)

    doc = tm_blob.read_blob(path)
    if doc is None:
        return
    doc = copy.deepcopy(doc)
    processing = doc.setdefault("processing", {})
    padding_doc = processing.setdefault("padding", {})
    padding_doc["pad_start_ms"] = 0
    padding_doc["pad_end_ms"] = 0
    padding_doc["last_pad_start_ms"] = 0
    padding_doc["last_pad_end_ms"] = 0
    padding_doc["padded_at"] = None
    # Drop legacy key if present
    processing.pop("extend", None)

    notes = doc.setdefault("user", {}).get("notes")
    if notes:
        parts = [p.strip() for p in str(notes).split(";")]
        parts = [
            p
            for p in parts
            if not p.startswith("padded start=") and not p.startswith("extended start=")
        ]
        doc["user"]["notes"] = "; ".join(p for p in parts if p) or None

    info = tm_audio.probe_audio(path)
    if info.get("duration_seconds") is not None:
        doc.setdefault("track", {})["duration_seconds"] = info["duration_seconds"]
    if info.get("size_bytes") is not None:
        doc.setdefault("audio", {})["size_bytes"] = info["size_bytes"]

    try:
        tm_blob.write_blob(path, doc)
    except Exception as e:
        print(f"⚠️ Failed to clear pad metadata blob: {e}", file=sys.stderr)


def _shift_content_cues(
    db: object,
    content: object,
    *,
    delta_seconds: float,
    sample_rate: int,
) -> None:
    """Shift cue/censor times for `content` by `delta_seconds` (may be negative)."""
    content_id = str(content.ID)
    delta_ms = int(round(delta_seconds * 1000.0))

    def _shift_cue_row(row: object) -> None:
        for attr in ("InMsec", "OutMsec"):
            val = getattr(row, attr, None)
            if val is not None and int(val) >= 0:
                setattr(row, attr, max(0, int(val) + delta_ms))
        cue_us = getattr(row, "CueMicrosec", None)
        if cue_us is not None and int(cue_us) >= 0:
            setattr(row, "CueMicrosec", max(0, int(cue_us) + delta_ms * 1000))
        for msec_attr, frame_attr in (("InMsec", "InFrame"), ("OutMsec", "OutFrame")):
            msec = getattr(row, msec_attr, None)
            if msec is not None and hasattr(row, frame_attr):
                setattr(
                    row,
                    frame_attr,
                    int(round(int(msec) / 1000.0 * sample_rate)),
                )

    cues = db.get_cue(ContentID=content_id)  # type: ignore[attr-defined]
    if cues is not None:
        for cue in list(cues):
            _shift_cue_row(cue)

    try:
        rows = db.get_hot_cue_banklist_songs(ContentID=content_id)  # type: ignore[attr-defined]
        if rows is not None:
            for row in list(rows):
                _shift_cue_row(row)
    except Exception:
        pass
    try:
        rows = db.get_content_active_censor(ContentID=content_id)  # type: ignore[attr-defined]
        if rows is not None:
            for row in list(rows):
                for attr in ("InMsec", "OutMsec"):
                    val = getattr(row, attr, None)
                    if val is not None and int(val) >= 0:
                        setattr(row, attr, max(0, int(val) + delta_ms))
    except Exception:
        pass


def _require_rekordbox_closed(*, action: str = "padding") -> None:
    procs = tm_rb.running_rekordbox_processes()
    if procs:
        details = "; ".join(f"{p.command} (pid {p.pid})" for p in procs)
        raise PadError(
            f"Rekordbox is running ({details}). Quit the app and "
            f"rekordboxAgent before {action}."
        )


def unpad_track(
    src: Path,
    *,
    dry_run: bool = False,
    backup_db: bool = True,
) -> tuple[float, float]:
    """Remove cumulative recorded pads from `src` and reverse analysis shifts.

    Uses ``TM_PAD`` / blob ``processing.padding`` totals. Returns the
    ``(pad_start_seconds, pad_end_seconds)`` that were undone.
    """
    src = src.resolve()
    if not src.is_file():
        raise PadError(f"File not found: {src}")

    _require_rekordbox_closed(action="undoing pads")

    start_s, end_s = recorded_pads(src)
    print(f"🎵 Track: {src.name}")
    print(f"↩️ Undo pads: start {start_s * 1000:.1f} ms, end {end_s * 1000:.1f} ms")

    if start_s <= 0 and end_s <= 0:
        raise PadError(
            "No recorded pads found (TM_PAD / processing.padding). " "Nothing to undo."
        )

    probed = tm_audio.probe_audio(src)
    duration = probed.get("duration_seconds")
    if duration is None or float(duration) <= 0:
        raise PadError(f"Could not probe duration for {src}")
    duration_f = float(duration)
    if start_s + end_s >= duration_f - 0.01:
        raise PadError(
            f"Recorded pads ({start_s + end_s:.3f}s) are too large for "
            f"duration {duration_f:.3f}s"
        )

    target_format = tm_audio.format_from_path(src)
    if target_format in ("m4a", "mp3"):
        print(
            f"⚠️ Undoing pads on {target_format.upper()} re-encodes the whole file "
            f"(lossy → another generation). Prefer AIFF.",
            file=sys.stderr,
        )

    if dry_run:
        print("ℹ️ Dry run — no file or database written")
        return (start_s, end_s)

    if backup_db:
        backup_path = tm_rb._backup_master_db()
        print(f"💾 Backed up master.db → {backup_path.name}")

    db = tm_rb._open_db()
    content = _find_content_for_path(db, src)
    anlz_files = _read_anlz_files_safe(db, content)
    if not anlz_files:
        raise PadError(f"No ANLZ analysis files found for {src.name}")

    tmp = src.with_name(f".tm_unpad_{os.getpid()}{src.suffix}")
    if tmp.exists():
        tmp.unlink()

    try:
        tm_audio.trim_silence_pads_to(
            src,
            tmp,
            trim_start_seconds=start_s,
            trim_end_seconds=end_s,
            target_format=target_format,
        )
    except tm_audio.EncodeError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise PadError(f"Audio unpad failed: {e}") from e

    try:
        tm_audio.copy_all_tags(src, tmp)
    except Exception as e:
        print(f"⚠️ Failed to copy tags: {e}", file=sys.stderr)

    os.replace(tmp, src)
    _clear_pad_metadata(src)

    new_info = tm_audio.probe_audio(src)
    new_duration = float(new_info.get("duration_seconds") or 0.0)
    new_size = src.stat().st_size

    # Reverse start-pad time shift in DAT only (never rewrite EXT/2EX).
    _apply_anlz_pad_shift(anlz_files, delta_seconds=-start_s)

    sample_rate = int(new_info.get("sample_rate") or content.SampleRate or 44100)
    _shift_content_cues(db, content, delta_seconds=-start_s, sample_rate=sample_rate)

    if new_duration > 0:
        content.Length = int(math.floor(new_duration + 1e-6))
    content.FileSize = int(new_size)
    db.commit()  # type: ignore[attr-defined]

    print(
        f"✅ Unpadded in place "
        f"({duration_f:.3f}s → {new_duration:.3f}s). "
        "Do not re-analyse in Rekordbox."
    )
    return (start_s, end_s)


def _read_anlz_files_safe(db: object, content: object) -> dict[Path, object]:
    """Load ANLZ files for `content`, ignoring non DAT/EXT/2EX junk in the folder.

    pyrekordbox's ``ANLZ####.*`` regex is unanchored, so backups like
    ``ANLZ0000.EXT.pregridfix`` get picked up and then fail to parse.
    """
    from pyrekordbox.anlz import AnlzFile

    paths = db.get_anlz_paths(content)  # type: ignore[attr-defined]
    files: dict[Path, object] = {}
    for kind in ("DAT", "EXT", "2EX"):
        path = paths.get(kind) if isinstance(paths, dict) else None
        if path is None:
            continue
        path = Path(path)
        if path.suffix.upper() not in (".DAT", ".EXT", ".2EX"):
            continue
        if not path.is_file():
            continue
        files[path] = AnlzFile.parse_file(path)
    return files


def _iter_dat_anlz(
    anlz_files: dict,
) -> list[tuple[Path, object]]:
    """Return only ``.DAT`` ANLZ files (safe to rewrite with pyrekordbox).

    Rewriting ``.EXT`` / ``.2EX`` via parse→build destroys the PQT2 extended
    beat-grid payload (incomplete pyrekordbox support), which makes beat
    markers disappear in Rekordbox 7. Cue/grid shifts for the desktop
    collection live in ``master.db``; DAT still carries PQTZ for exports.
    """
    out: list[tuple[Path, object]] = []
    for path, anlz in anlz_files.items():
        if path.suffix.upper() == ".DAT":
            out.append((path, anlz))
    return out


def _apply_anlz_pad_shift(
    anlz_files: dict,
    *,
    delta_seconds: float,
) -> None:
    """Shift beat-grid/cue times in DAT only. No-op when delta is 0."""
    if abs(delta_seconds) < 1e-12:
        return
    for anlz_path, anlz in _iter_dat_anlz(anlz_files):
        _shift_anlz_times(anlz, delta_seconds)
        anlz.save(anlz_path)


def _find_content_for_path(db: object, path: Path) -> object:
    """Return the DjmdContent row whose FolderPath matches `path`."""
    target = path.resolve()
    target_str = str(target)
    matches = []
    for content in db.get_content():  # type: ignore[attr-defined]
        folder = getattr(content, "FolderPath", None) or ""
        if not folder:
            continue
        try:
            if Path(folder).resolve() == target or folder == target_str:
                matches.append(content)
                continue
        except (OSError, ValueError):
            pass
        # Fallback: same filename in case of path normalisation quirks
        if Path(folder).name == target.name:
            matches.append(content)

    if not matches:
        raise PadError(
            f"Track not found in Rekordbox master.db: {path}\n"
            "Confirm the file is in your collection and Rekordbox is closed."
        )
    if len(matches) > 1:
        # Prefer exact resolved-path match
        exact = [
            c
            for c in matches
            if (c.FolderPath or "") == target_str
            or Path(c.FolderPath or "").resolve() == target
        ]
        if len(exact) == 1:
            return exact[0]
        raise PadError(
            f"Multiple Rekordbox entries match {path.name!r}; " "use a unique path."
        )
    return matches[0]


def pad_track(
    src: Path,
    *,
    threshold_beat: int = 3,
    pad_start: bool = True,
    pad_end: bool = True,
    end_tail: tm_audio.EndTailMode = tm_audio.PAD_END_TAIL_DEFAULT,
    dry_run: bool = False,
    backup_db: bool = True,
) -> PadPlan:
    """Pad `src` to bar boundaries in place and shift Rekordbox analysis.

    Requires Rekordbox to be fully quit. Returns the computed plan (also when
    dry-running or when no pad is needed).

    ``end_tail`` controls the end-pad fill only (``reverb`` = quiet pad-only
    wash; ``silence`` = dry silence). The original body is never faded.
    """
    src = src.resolve()
    if not src.is_file():
        raise PadError(f"File not found: {src}")

    _require_rekordbox_closed(action="padding")

    probed = tm_audio.probe_audio(src)
    duration = probed.get("duration_seconds")
    if duration is None or float(duration) <= 0:
        raise PadError(f"Could not probe duration for {src}")
    duration_f = float(duration)

    db = tm_rb._open_db()
    content = _find_content_for_path(db, src)
    anlz_files = _read_anlz_files_safe(db, content)
    if not anlz_files:
        raise PadError(f"No ANLZ analysis files found for {src.name}")

    # Prefer .DAT beat grid
    beat_tag = None
    dat_anlz = None
    for path, anlz in anlz_files.items():
        if path.suffix.upper() == ".DAT" and "beat_grid" in anlz:
            dat_anlz = anlz
            beat_tag = anlz.get_tag("beat_grid")
            break
    if beat_tag is None:
        for path, anlz in anlz_files.items():
            if "beat_grid" in anlz:
                dat_anlz = anlz
                beat_tag = anlz.get_tag("beat_grid")
                break
    if beat_tag is None or dat_anlz is None:
        raise PadError(f"No beat grid (PQTZ) found for {src.name}")

    times = list(beat_tag.get_times())
    beats = [int(b) for b in beat_tag.get_beats()]
    bpms = [float(b) for b in beat_tag.get_bpms()]
    if not times:
        raise PadError(f"Beat grid is empty for {src.name}")

    plan = compute_pad_plan(
        duration_seconds=duration_f,
        times=times,
        beats=beats,
        bpms=bpms,
        threshold_beat=threshold_beat,
    )

    start_s = plan.pad_start_seconds if pad_start else 0.0
    end_s = plan.pad_end_seconds if pad_end else 0.0
    # Re-wrap plan with possibly disabled sides for reporting
    plan = PadPlan(
        duration_seconds=plan.duration_seconds,
        beat_duration_seconds=plan.beat_duration_seconds,
        phase_start=plan.phase_start,
        phase_end=plan.phase_end,
        threshold_beat=plan.threshold_beat,
        pad_start_seconds=start_s,
        pad_end_seconds=end_s,
    )

    print(f"🎵 Track: {src.name}")
    print(
        f"🥁 Grid: {60.0 / plan.beat_duration_seconds:.2f} BPM, "
        f"beat ≈ {plan.beat_duration_seconds * 1000:.1f} ms"
    )
    print(
        f"📍 Phase: start {_format_phase(plan.phase_start)}, "
        f"end {_format_phase(plan.phase_end)} "
        f"(pad if on/past the {threshold_beat}; "
        f"start skips if <{_NEAR_ONE_BEATS:g} beat before the 1)"
    )
    print(
        f"➕ Pad: start {plan.pad_start_seconds * 1000:.1f} ms, "
        f"end {plan.pad_end_seconds * 1000:.1f} ms"
    )
    if plan.pad_end_seconds > 0:
        print(
            f"🌫️ End fill: {end_tail} "
            "(body untouched; only the padded region is filled)"
        )

    target_format = tm_audio.format_from_path(src)
    if plan.needs_pad and target_format in ("m4a", "mp3"):
        print(
            f"⚠️ Padding {target_format.upper()} re-encodes the whole file "
            f"(lossy → another generation). Prefer AIFF "
            f"(e.g. tm migrate-to-aiff) so pads stay PCM-safe.",
            file=sys.stderr,
        )

    if not plan.needs_pad:
        print("✅ Already aligned — nothing to do")
        return plan

    if dry_run:
        print("ℹ️ Dry run — no file or database written")
        return plan

    backup_path = None
    if backup_db:
        backup_path = tm_rb._backup_master_db()
        print(f"💾 Backed up master.db → {backup_path.name}")

    # 1) Pad audio in place (temp sibling → replace)
    tmp = src.with_name(f".tm_pad_{os.getpid()}{src.suffix}")
    if tmp.exists():
        tmp.unlink()

    try:
        tm_audio.pad_silence_to(
            src,
            tmp,
            pad_start_seconds=plan.pad_start_seconds,
            pad_end_seconds=plan.pad_end_seconds,
            target_format=target_format,
            end_tail=end_tail,
        )
    except tm_audio.EncodeError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise PadError(f"Audio pad failed: {e}") from e

    try:
        tm_audio.copy_all_tags(src, tmp)
    except Exception as e:
        print(f"⚠️ Failed to copy tags: {e}", file=sys.stderr)

    prior_start, prior_end = recorded_pads(src)
    total_start = prior_start + plan.pad_start_seconds
    total_end = prior_end + plan.pad_end_seconds
    _write_pad_metadata(
        tmp,
        total_start_seconds=total_start,
        total_end_seconds=total_end,
        threshold_beat=threshold_beat,
        this_start_seconds=plan.pad_start_seconds,
        this_end_seconds=plan.pad_end_seconds,
    )
    print(
        f"📝 TM_PAD: start={total_start * 1000:.1f}ms "
        f"end={total_end * 1000:.1f}ms (cumulative)"
    )

    os.replace(tmp, src)

    new_info = tm_audio.probe_audio(src)
    new_duration = float(new_info.get("duration_seconds") or 0.0)
    new_size = src.stat().st_size

    # 2) Shift ANLZ analysis in DAT only (start pad). End-only pads skip ANLZ.
    # Never rewrite EXT/2EX — pyrekordbox round-trips wipe PQT2 and hide the grid.
    _apply_anlz_pad_shift(anlz_files, delta_seconds=plan.pad_start_seconds)

    # 3) Shift DB cues + update length/size
    delta = plan.pad_start_seconds
    sample_rate = int(new_info.get("sample_rate") or content.SampleRate or 44100)
    _shift_content_cues(db, content, delta_seconds=delta, sample_rate=sample_rate)

    # Length is whole seconds in master.db
    if new_duration > 0:
        content.Length = int(math.floor(new_duration + 1e-6))
    content.FileSize = int(new_size)
    db.commit()  # type: ignore[attr-defined]

    print(
        f"✅ Padded in place "
        f"({duration_f:.3f}s → {new_duration:.3f}s). "
        "Do not re-analyse in Rekordbox."
    )
    return plan
