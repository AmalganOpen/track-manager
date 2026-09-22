#!/usr/bin/env python3
"""Scale embedded cover art down to a Pioneer-safe JPEG.

XDJ-1000 MK2 can walk off the end of an AIFF into a huge ID3 APIC and
report "unsupported file format". Rekordbox already copies artwork into
``PIONEER/Artwork/`` on USB export, so the embedded image only needs to
be large enough to import. Default long-side cap is 640px.

Does not touch the track-manager metadata blob or other ID3 frames.

Usage::

  python scripts/scale_covers.py
  python scripts/scale_covers.py -n
  python scripts/scale_covers.py --max-side 640
  python scripts/scale_covers.py skaiwater
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from track_manager import cover as tm_cover  # noqa: E402
from track_manager.config import Config  # noqa: E402
from track_manager.library import (  # noqa: E402
    find_matching_tracks,
    list_library_tracks,
    pick_track,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scale embedded cover art to a Pioneer-safe JPEG.",
    )
    parser.add_argument(
        "track",
        nargs="?",
        default=None,
        help="Partial filename to match (default: whole library)",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="Library directory (default: config output_dir)",
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=tm_cover.COVER_MAX_SIDE,
        help=f"Long-side cap in pixels (default: {tm_cover.COVER_MAX_SIDE})",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would change without writing",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt for a whole-library run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_side < 1 or args.max_side > tm_cover.COVER_SIDE_LIMIT:
        print(
            f"❌ --max-side must be 1–{tm_cover.COVER_SIDE_LIMIT}",
            file=sys.stderr,
        )
        return 1

    library = args.library
    if library is None:
        library = Config().output_dir
    library = library.expanduser().resolve()
    if not library.is_dir():
        print(f"❌ Library directory not found: {library}", file=sys.stderr)
        return 1

    if args.track:
        matches = find_matching_tracks(args.track, library)
        if not matches:
            print(f"❌ No library track matching {args.track!r}", file=sys.stderr)
            return 1
        chosen = pick_track(matches)
        if chosen is None:
            return 1
        tracks = [chosen]
    else:
        tracks = list_library_tracks(library)

    if not tracks:
        print("No audio files found to check.")
        return 0

    if not args.dry_run and shutil.which("ffmpeg") is None:
        print("❌ ffmpeg not found on PATH (needed to scale covers)", file=sys.stderr)
        return 1

    if not args.dry_run and args.track is None and not args.yes:
        reply = input(
            f"Scale covers in {len(tracks)} track(s) in {library}? "
            "Each file is replaced only after a successful rewrite. [y/N] "
        )
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    print(
        f"🖼️ {'Dry-run: would scale' if args.dry_run else 'Scaling'} covers "
        f"(max {args.max_side}px) in {len(tracks)} track(s)..."
    )
    print()
    results = tm_cover.scale_library_covers(
        tracks, max_side=args.max_side, dry_run=args.dry_run
    )
    scaled = [r for r in results if r.action == "scaled"]
    skipped = [r for r in results if r.action == "skipped"]
    failed = [r for r in results if r.action == "failed"]
    for r in scaled:
        print(f"  🔄 {r.path.name}: {r.detail}")
    if failed:
        print()
        for r in failed:
            print(f"  ❌ {r.path.name}: {r.detail}")
    print()
    print(f"  🔄 Scaled:  {len(scaled)}")
    print(f"  ⏭️  Skipped: {len(skipped)}")
    print(f"  ❌ Failed:  {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
