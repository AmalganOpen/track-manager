"""Rekordbox master.db editor (preferred over the XML round-trip).

Rekordbox 6+ stores its collection in a SQLCipher-encrypted SQLite
database at ``~/Library/Pioneer/rekordbox/master.db``. The decryption
key lives encrypted in ``options.json`` as the ``dp`` field; pyrekordbox
handles the derivation transparently.

Updating a track's file path (``DjmdContent.FolderPath``) bypasses
Rekordbox's import logic entirely. Cue points (``DjmdCue``), beat grids
(``DjmdBeatGrid``/``.anlz`` files), playlist memberships
(``DjmdSongInPlaylist``), ratings, color tags, play counts, and analysis
caches are all keyed by ``ContentID`` and follow the path update
untouched.

CRITICAL: Rekordbox MUST be closed before any write. The DB is locked
while it is running, and pyrekordbox will fail to connect — but more
worryingly, any write under those conditions risks corrupting the
collection. ``update_paths_to_aiff`` checks for a running Rekordbox
process and refuses to run if one is found.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional

# Pioneer's FileType enum on DjmdContent. Confirmed against multiple
# real-world exports / pyrekordbox source.
FILETYPE_BY_EXT = {
    ".mp3": 0,
    ".m4a": 1,
    ".wav": 4,
    ".aiff": 5,
    ".aif": 5,
    ".flac": 11,
}

MASTER_DB_PATH = Path.home() / "Library/Pioneer/rekordbox/master.db"


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class TrackInfo(NamedTuple):
    """Read-only summary of a DjmdContent row."""

    content_id: int
    folder_path: Path
    file_name: str
    title: Optional[str]
    artist: Optional[str]
    file_size: int
    bitrate_kbps: int
    sample_rate: int
    file_type: int
    inside_library: bool


class UpdatePlan(NamedTuple):
    content_id: int
    old_path: Path
    new_path: Path
    new_size: int
    new_bitrate: Optional[int]
    new_sample_rate: Optional[int]
    new_filetype: int


class UpdateResult(NamedTuple):
    planned: list[UpdatePlan]
    skipped_outside: list[TrackInfo]
    skipped_already_aiff: list[TrackInfo]
    skipped_no_aiff: list[TrackInfo]
    backup_path: Optional[Path]
    committed: bool


# ---------------------------------------------------------------------------
# Process / DB safety
# ---------------------------------------------------------------------------


class RekordboxProcess(NamedTuple):
    pid: int
    command: str

    @property
    def is_agent(self) -> bool:
        # The background helper is the persistent one; safe to kill (no
        # user state). The main app holds unsaved analysis / collection
        # changes, so we don't auto-kill that.
        return "rekordboxagent" in self.command.lower()


def running_rekordbox_processes() -> list[RekordboxProcess]:
    """List rekordbox / rekordboxAgent processes currently running.

    Uses ``pgrep -fil rekordbox`` (case-insensitive full-cmdline match).
    Returns an empty list if pgrep isn't available or nothing matches.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-fil", "rekordbox"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    procs: list[RekordboxProcess] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # pgrep -l output: "<pid> <cmdline>"
        pid_str, _, cmd = line.partition(" ")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        # Filter false positives: ignore our own process if its name
        # mentions rekordbox (e.g. running from a script named that).
        if pid == os.getpid():
            continue
        procs.append(RekordboxProcess(pid=pid, command=cmd))
    return procs


def is_rekordbox_running() -> bool:
    """Backward-compat shim: True iff any rekordbox process is running."""
    return bool(running_rekordbox_processes())


def kill_rekordbox_agent(timeout: float = 5.0) -> tuple[bool, list[RekordboxProcess]]:
    """Send SIGTERM to every rekordboxAgent process and wait for them to exit.

    Does NOT touch the main rekordbox.app process — that has user state.
    Returns ``(success, remaining)`` where ``remaining`` lists any
    rekordbox processes still running afterwards (which may include the
    main GUI app the caller asked us not to touch).
    """
    targets = [p for p in running_rekordbox_processes() if p.is_agent]
    for p in targets:
        try:
            os.kill(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            return False, running_rekordbox_processes()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        agents_left = [p for p in running_rekordbox_processes() if p.is_agent]
        if not agents_left:
            break
        time.sleep(0.25)

    remaining = running_rekordbox_processes()
    success = not any(p.is_agent for p in remaining)
    return success, remaining


def _open_db():
    """Return an opened pyrekordbox database handle (read-write)."""
    try:
        from pyrekordbox import Rekordbox6Database
    except ImportError as e:
        raise ImportError(
            "pyrekordbox is required for Rekordbox database operations. "
            "Install with: pip install pyrekordbox"
        ) from e
    # The class name says "6" but it covers Rekordbox 7 too — the schema
    # has only minor additive changes.
    return Rekordbox6Database()


def _backup_master_db() -> Path:
    """Copy master.db to a timestamped backup beside it. Returns the backup path."""
    if not MASTER_DB_PATH.exists():
        raise FileNotFoundError(f"master.db not found at {MASTER_DB_PATH}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = MASTER_DB_PATH.with_name(f"master.db.bak.{stamp}")
    shutil.copy2(MASTER_DB_PATH, target)
    return target


# ---------------------------------------------------------------------------
# Read-only audit
# ---------------------------------------------------------------------------


def list_tracks(library_dir: Optional[Path] = None) -> list[TrackInfo]:
    """Read every DjmdContent row, flagging library membership."""
    db = _open_db()
    library = library_dir.resolve() if library_dir else None

    tracks: list[TrackInfo] = []
    for content in db.get_content():
        path_str = content.FolderPath or ""
        if not path_str:
            continue
        try:
            path = Path(path_str)
        except (ValueError, OSError):
            continue

        artist_name = None
        artist = getattr(content, "Artist", None)
        if artist is not None:
            artist_name = getattr(artist, "Name", None)

        tracks.append(
            TrackInfo(
                content_id=int(content.ID),
                folder_path=path,
                file_name=content.FileNameL or content.FileNameS or path.name,
                title=content.Title,
                artist=artist_name,
                file_size=int(content.FileSize or 0),
                bitrate_kbps=int(content.BitRate or 0),
                sample_rate=int(content.SampleRate or 0),
                file_type=int(content.FileType or 0),
                inside_library=(library is not None and _is_inside(path, library)),
            )
        )
    return tracks


# ---------------------------------------------------------------------------
# Plan + apply
# ---------------------------------------------------------------------------


def plan_update_to_aiff(
    library_dir: Path,
) -> tuple[list[UpdatePlan], list[TrackInfo], list[TrackInfo], list[TrackInfo]]:
    """Compute the per-track update plan. No DB writes.

    Returns ``(planned, outside_library, already_aiff, no_aiff_on_disk)``.
    """
    library = library_dir.resolve()

    from . import audio as tm_audio

    planned: list[UpdatePlan] = []
    outside: list[TrackInfo] = []
    already_aiff: list[TrackInfo] = []
    no_aiff: list[TrackInfo] = []

    for track in list_tracks(library):
        if not track.inside_library:
            outside.append(track)
            continue
        if track.folder_path.suffix.lower() in (".aiff", ".aif"):
            already_aiff.append(track)
            continue

        new_path = track.folder_path.with_suffix(".aiff")
        if not new_path.exists():
            no_aiff.append(track)
            continue

        info = tm_audio.probe_audio(new_path)
        try:
            new_size = new_path.stat().st_size
        except OSError:
            new_size = 0

        planned.append(
            UpdatePlan(
                content_id=track.content_id,
                old_path=track.folder_path,
                new_path=new_path,
                new_size=new_size,
                new_bitrate=info.get("bitrate_kbps"),
                new_sample_rate=info.get("sample_rate"),
                new_filetype=FILETYPE_BY_EXT.get(new_path.suffix.lower(), 5),
            )
        )

    return planned, outside, already_aiff, no_aiff


def update_paths_to_aiff(
    library_dir: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> UpdateResult:
    """Update master.db so every library track points at its AIFF counterpart.

    Refuses to run if Rekordbox / rekordboxAgent is in the process list.
    Backs up master.db to ``master.db.bak.<timestamp>`` before any write
    unless ``backup=False``.
    """
    procs = running_rekordbox_processes()
    if procs:
        details = "; ".join(f"{p.command} (pid {p.pid})" for p in procs)
        raise RuntimeError(
            f"Rekordbox is running ({details}). Quit the app and the "
            f"rekordboxAgent helper before running this command."
        )

    planned, outside, already_aiff, no_aiff = plan_update_to_aiff(library_dir)

    backup_path: Optional[Path] = None
    committed = False

    if not dry_run and planned:
        if backup:
            backup_path = _backup_master_db()

        db = _open_db()
        # pyrekordbox returns model instances directly when filtering by
        # primary key, but the exact shape varies across versions. Build
        # a single lookup table from one full iteration so this works on
        # any version and avoids N round-trips to the DB.
        content_by_id = {int(c.ID): c for c in db.get_content()}

        for plan in planned:
            content = content_by_id.get(plan.content_id)
            if content is None:
                # Track was deleted between plan and apply; ignore.
                continue
            content.FolderPath = str(plan.new_path)
            content.FileNameL = plan.new_path.name
            content.FileNameS = plan.new_path.name
            content.FileSize = plan.new_size
            if plan.new_bitrate is not None:
                content.BitRate = plan.new_bitrate
            if plan.new_sample_rate is not None:
                content.SampleRate = plan.new_sample_rate
            content.FileType = plan.new_filetype
        db.commit()
        committed = True

    return UpdateResult(
        planned=planned,
        skipped_outside=outside,
        skipped_already_aiff=already_aiff,
        skipped_no_aiff=no_aiff,
        backup_path=backup_path,
        committed=committed,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_inside(child: Path, parent: Path) -> bool:
    """Return True if `child` is `parent` or a descendant. Lexical only."""
    try:
        # Don't resolve `child` — the source file may already have been
        # moved by `tm migrate-to-aiff` to .tm-migration-backup/ and we
        # still want to count its old library path as "inside library".
        child_abs = child if child.is_absolute() else (Path.cwd() / child)
        child_abs.relative_to(parent)
        return True
    except ValueError:
        return False
