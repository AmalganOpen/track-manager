#!/usr/bin/env python3
"""List library tracks whose |cents| deviation from A440 exceeds a threshold.

Usage::

  python scripts/list_tuning_outliers.py 10
  python scripts/list_tuning_outliers.py 20 --library ~/Music/tm/tracks
  python scripts/list_tuning_outliers.py 15 --duration 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
for path in (_REPO_ROOT, _SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import count_tuning_deviation as ctd  # noqa: E402

from track_manager.config import Config  # noqa: E402
from track_manager.library import list_library_tracks  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List tracks with |cents| from A440 above a threshold.",
    )
    parser.add_argument(
        "threshold",
        type=float,
        help="List tracks with absolute cents deviation ≥ this value",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="Library directory (default: config output_dir)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=45.0,
        help="Seconds of audio to analyse per track (default: 45)",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=None,
        help="Fixed start offset in seconds (default: auto past intros)",
    )
    parser.add_argument(
        "--signed",
        action="store_true",
        help="Show signed cents (sharp +, flat −) instead of absolute only",
    )
    return parser.parse_args(argv)


def signed_cents_for_track(
    path: Path, *, duration: float, offset: float | None
) -> float:
    """Return signed cents vs A440 (positive = sharp)."""
    from track_manager import check_tuning as tm_check

    librosa = tm_check._ensure_librosa()
    y, sr, _ = tm_check.load_analysis_audio(path, duration=duration, offset=offset)
    tuning = librosa.estimate_tuning(y=y, sr=sr, bins_per_octave=12)
    return float(tuning) * 100.0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    threshold = float(args.threshold)
    if threshold < 0:
        print("❌ threshold must be ≥ 0", file=sys.stderr)
        return 1

    library = args.library
    if library is None:
        library = Config().output_dir
    library = library.expanduser().resolve()

    tracks = list_library_tracks(library)
    if not tracks:
        print(f"No audio tracks found in {library}")
        return 0

    print(f"📁 Library: {library}")
    print(f"🎵 Tracks: {len(tracks)}")
    print(f"🎚️ Listing |¢| ≥ {threshold:g}")
    print()

    outliers: list[tuple[float, Path]] = []
    failed: list[tuple[Path, str]] = []

    for i, path in enumerate(tracks, 1):
        print(f"\r[{i}/{len(tracks)}] analysing…", end="", flush=True)
        try:
            if args.signed:
                cents = signed_cents_for_track(
                    path, duration=args.duration, offset=args.offset
                )
                magnitude = abs(cents)
            else:
                magnitude = ctd.abs_cents_for_track(
                    path, duration=args.duration, offset=args.offset
                )
                cents = magnitude
        except Exception as e:
            failed.append((path, str(e)))
            continue

        if magnitude >= threshold:
            outliers.append((cents, path))

    print()
    outliers.sort(key=lambda item: abs(item[0]), reverse=True)

    print()
    print(f"📊 {len(outliers)} track(s) with |¢| ≥ {threshold:g}")
    print("─" * 60)
    for cents, path in outliers:
        if args.signed:
            print(f"  {cents:+6.1f}¢  {path.name}")
        else:
            print(f"  {cents:5.1f}¢  {path.name}")
    print("─" * 60)
    print(f"  Above threshold: {len(outliers)}")
    print(f"  Failed:          {len(failed)}")

    if failed:
        print()
        print("❌ Failures:")
        for path, err in failed:
            print(f"  {path.name}: {err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
