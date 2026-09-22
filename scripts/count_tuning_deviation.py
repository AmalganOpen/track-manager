#!/usr/bin/env python3
"""Count library tracks by absolute cents deviation from A440.

Buckets are half-open 5¢ ranges: ``[0,5), [5,10), [10,15), …``

Usage::

  python scripts/count_tuning_deviation.py
  python scripts/count_tuning_deviation.py --library ~/Music/tm/tracks
  python scripts/count_tuning_deviation.py --duration 30 -v
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Allow running as ``python scripts/…`` without installing the package editable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from track_manager import check_tuning as tm_check  # noqa: E402
from track_manager.config import Config  # noqa: E402
from track_manager.library import list_library_tracks  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bucket library tracks by |cents| deviation from A440.",
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
        "--bin-size",
        type=float,
        default=5.0,
        help="Cents per bucket (default: 5)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each track's cents as it is analysed",
    )
    return parser.parse_args(argv)


def abs_cents_for_track(path: Path, *, duration: float, offset: float | None) -> float:
    """Return absolute cents deviation from A440 (key detection skipped)."""
    librosa = tm_check._ensure_librosa()
    y, sr, _ = tm_check.load_analysis_audio(path, duration=duration, offset=offset)
    tuning = librosa.estimate_tuning(y=y, sr=sr, bins_per_octave=12)
    return abs(float(tuning) * 100.0)


def bucket_lower(abs_cents: float, bin_size: float) -> float:
    """Lower edge of the half-open bucket containing `abs_cents`."""
    if abs_cents < 0:
        abs_cents = 0.0
    return (abs_cents // bin_size) * bin_size


def format_bucket(lo: float, bin_size: float) -> str:
    hi = lo + bin_size
    # Pretty integers when bin_size is integral.
    if float(bin_size).is_integer() and float(lo).is_integer():
        return f"{int(lo)}-{int(hi)}"
    return f"{lo:g}-{hi:g}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bin_size = float(args.bin_size)
    if bin_size <= 0:
        print("❌ --bin-size must be positive", file=sys.stderr)
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
    print(f"🔍 Analysing {args.duration:g}s windows (bin size {bin_size:g}¢)")
    print()

    counts: Counter[float] = Counter()
    failed: list[tuple[Path, str]] = []
    cents_values: list[float] = []

    for i, path in enumerate(tracks, 1):
        try:
            cents = abs_cents_for_track(
                path, duration=args.duration, offset=args.offset
            )
        except Exception as e:
            failed.append((path, str(e)))
            if args.verbose:
                print(f"[{i}/{len(tracks)}] ❌ {path.name}: {e}", flush=True)
            else:
                print(
                    f"\r[{i}/{len(tracks)}] analysing…",
                    end="",
                    flush=True,
                )
            continue

        cents_values.append(cents)
        lo = bucket_lower(cents, bin_size)
        counts[lo] += 1
        if args.verbose:
            print(
                f"[{i}/{len(tracks)}] {path.name}: {cents:+.1f}¢  "
                f"→ {format_bucket(lo, bin_size)}",
                flush=True,
            )
        else:
            print(f"\r[{i}/{len(tracks)}] analysing…", end="", flush=True)

    if not args.verbose:
        print()

    analysed = len(cents_values)
    print()
    print("📊 Absolute deviation from A440 (cents)")
    print("─" * 40)
    if counts:
        for lo in sorted(counts):
            label = format_bucket(lo, bin_size)
            n = counts[lo]
            pct = 100.0 * n / analysed if analysed else 0.0
            bar = "█" * max(1, round(n * 20 / analysed)) if analysed else ""
            print(f"  {label:>10}¢  {n:4d}  ({pct:5.1f}%)  {bar}")
    else:
        print("  (no successful analyses)")

    print("─" * 40)
    print(f"  Analysed: {analysed}")
    print(f"  Failed:   {len(failed)}")
    if cents_values:
        print(f"  Mean |¢|: {sum(cents_values) / len(cents_values):.1f}")
        print(f"  Max |¢|:  {max(cents_values):.1f}")
        print(f"  Median:   {sorted(cents_values)[len(cents_values) // 2]:.1f}")

    if failed and args.verbose:
        print()
        print("❌ Failures:")
        for path, err in failed:
            print(f"  {path.name}: {err}")

    return 0 if analysed else 1


if __name__ == "__main__":
    raise SystemExit(main())
