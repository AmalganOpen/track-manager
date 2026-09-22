#!/usr/bin/env python3
"""List library tracks previously modified by ``tm tune``.

Detects the ``TM_TUNING`` tag (ID3 TXXX / MP4 freeform). Optionally also
matches blob notes containing ``tuned ``.

Usage::

  python scripts/list_tuned_tracks.py
  python scripts/list_tuned_tracks.py --library ~/Music/tm/tracks
  python scripts/list_tuned_tracks.py --paths
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from track_manager import blob as tm_blob  # noqa: E402
from track_manager.check_tuning import read_recorded_tuning_label  # noqa: E402
from track_manager.config import Config  # noqa: E402
from track_manager.library import list_library_tracks  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List tracks that have been pitch-tuned with tm tune.",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="Library directory (default: config output_dir)",
    )
    parser.add_argument(
        "--paths",
        action="store_true",
        help="Print absolute paths only (one per line)",
    )
    parser.add_argument(
        "--include-blob-notes",
        action="store_true",
        help="Also treat blob notes containing 'tuned ' as tuned "
        "(in case TM_TUNING tag is missing)",
    )
    return parser.parse_args(argv)


def blob_tuned_label(path: Path) -> str | None:
    """Return a tuning note from the metadata blob, if any."""
    doc = tm_blob.read_blob(path)
    if not doc:
        return None
    notes = doc.get("user", {}).get("notes")
    if not notes:
        return None
    text = str(notes)
    # Notes look like: "tuned +2% (+34.12 cents)" or stacked with "; ".
    parts = [p.strip() for p in text.split(";")]
    tuned = [p for p in parts if p.lower().startswith("tuned ")]
    if not tuned:
        return None
    return "; ".join(tuned)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    library = args.library
    if library is None:
        library = Config().output_dir
    library = library.expanduser().resolve()

    tracks = list_library_tracks(library)
    if not tracks:
        if not args.paths:
            print(f"No audio tracks found in {library}")
        return 0

    found: list[tuple[Path, str]] = []
    for path in tracks:
        label = read_recorded_tuning_label(path)
        if label is None and args.include_blob_notes:
            label = blob_tuned_label(path)
        if label is not None:
            found.append((path, label))

    if args.paths:
        for path, _ in found:
            print(path)
        return 0

    print(f"📁 Library: {library}")
    print(f"🎵 Tracks scanned: {len(tracks)}")
    print(f"🎚️ Tuned tracks: {len(found)}")
    print("─" * 60)
    for path, label in found:
        print(f"  {label:20}  {path.name}")
    print("─" * 60)
    print(f"  Total: {len(found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
