"""Library track discovery and interactive selection.

Shared by commands that operate on a single library track (``tune``,
``check-tuning``, …): partial-title search against ``output_dir``, with an
optional absolute-path mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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
