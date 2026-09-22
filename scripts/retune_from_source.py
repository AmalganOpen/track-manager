#!/usr/bin/env python3
"""Re-download tuned tracks clean, retune once (HQ), replace original audio.

For each library file with a ``TM_TUNING`` tag:

  1. Parse the recorded cents (and optional BPM%) from the tag
  2. Download a fresh copy via ``tm``'s Downloader into a temp dir
  3. Match the original container format (usually AIFF)
  4. Pitch-shift once with the current rubberband quality settings
  5. Copy tags/blob from the original onto the new audio
  6. Atomically replace the original path

Usage::

  python scripts/retune_from_source.py -n          # dry-run
  python scripts/retune_from_source.py -y          # do it
  python scripts/retune_from_source.py -y --path "/path/to/one.aiff"
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
for path in (_REPO_ROOT, _SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from list_tuned_tracks import blob_tuned_label  # noqa: E402

from track_manager import audio as tm_audio  # noqa: E402
from track_manager import blob as tm_blob  # noqa: E402
from track_manager.check_tuning import read_recorded_tuning_label  # noqa: E402
from track_manager.config import Config  # noqa: E402
from track_manager.downloader import Downloader  # noqa: E402
from track_manager.library import list_library_tracks  # noqa: E402
from track_manager.upgrade import _source_quality  # noqa: E402

_AUDIO_EXTS = {
    ".m4a",
    ".mp3",
    ".flac",
    ".wav",
    ".ogg",
    ".aac",
    ".opus",
    ".aiff",
    ".aif",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-download previously tuned tracks, retune once at HQ, "
            "and replace the original container audio."
        ),
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="Library directory (default: config output_dir)",
    )
    parser.add_argument(
        "--path",
        type=Path,
        action="append",
        default=None,
        help="Only process this file (repeatable). Must already be tuned.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="List what would be done without downloading or writing",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Perform replacements without an interactive confirm",
    )
    parser.add_argument(
        "--include-blob-notes",
        action="store_true",
        help="Also include tracks whose blob notes say 'tuned ' "
        "(if TM_TUNING is missing)",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep per-track temp dirs (prints their path)",
    )
    return parser.parse_args(argv)


def parse_tuning_cents(label: str) -> tuple[float, float | None]:
    """Parse ``TM_TUNING`` text → ``(cents, bpm_percent_or_None)``.

    Accepts forms written by ``tm tune``:
    - ``+12.00 cents`` / ``+50c``
    - ``+2% (+34.28 cents)``
    - ``+2%`` (converted via ``bpm_percent_to_cents``)
    """
    text = label.strip()
    if text.lower().startswith("tuned "):
        text = text[6:].strip()

    m = re.search(r"\(([+-]?\d+(?:\.\d+)?)\s*cents?\)", text, re.I)
    if m:
        cents = float(m.group(1))
        pct_m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)\s*%", text)
        return cents, float(pct_m.group(1)) if pct_m else None

    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:cents?|c)\b", text, re.I)
    if m:
        return float(m.group(1)), None

    pct_m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)\s*%", text)
    if pct_m:
        pct = float(pct_m.group(1))
        return tm_audio.bpm_percent_to_cents(pct), pct

    raise ValueError(f"Unparseable TM_TUNING label: {label!r}")


def _pick_downloaded_audio(tmp_dir: Path) -> Path:
    files = [
        f for f in tmp_dir.iterdir() if f.is_file() and f.suffix.lower() in _AUDIO_EXTS
    ]
    if not files:
        raise RuntimeError("Download produced no audio file")
    if len(files) > 1:

        def _rank(p: Path) -> int:
            return {".aiff": 0, ".aif": 0, ".m4a": 1, ".flac": 2, ".mp3": 3}.get(
                p.suffix.lower(), 9
            )

        files.sort(key=_rank)
    return files[0]


def _stage_matching_format(src: Path, target_format: str, tmp_dir: Path) -> Path:
    """Ensure `src` is in `target_format`; return path (may be `src`)."""
    try:
        src_format = tm_audio.format_from_path(src)
    except ValueError:
        src_format = ""
    if src_format == target_format:
        return src

    ext = {"aiff": ".aiff", "m4a": ".m4a", "mp3": ".mp3"}[target_format]
    staged = tmp_dir / f"clean_staged{ext}"
    tm_audio.encode_to(target_format, src, staged)
    return staged


def _refresh_blob_audio(path: Path) -> None:
    """Update blob ``audio.*`` / duration from a fresh probe."""
    doc = tm_blob.read_blob(path)
    if doc is None:
        return
    info = tm_audio.probe_audio(path)
    audio = doc.setdefault("audio", {})
    for key in (
        "format",
        "codec",
        "bitrate_kbps",
        "sample_rate",
        "bit_depth",
        "channels",
        "size_bytes",
    ):
        if info.get(key) is not None:
            audio[key] = info[key]
    if info.get("duration_seconds") is not None:
        doc.setdefault("track", {})["duration_seconds"] = info["duration_seconds"]
    tm_blob.write_blob(path, doc)


def discover_jobs(
    library: Path,
    *,
    paths: list[Path] | None,
    include_blob_notes: bool,
) -> list[tuple[Path, str, float, float | None, str]]:
    """Return ``(path, label, cents, bpm_percent, track_url)`` jobs."""
    if paths:
        candidates = [p.expanduser().resolve() for p in paths]
    else:
        candidates = list_library_tracks(library)

    jobs: list[tuple[Path, str, float, float | None, str]] = []
    for path in candidates:
        if not path.is_file():
            print(f"⚠️ Skipping missing file: {path}", file=sys.stderr)
            continue
        label = read_recorded_tuning_label(path)
        if label is None and include_blob_notes:
            label = blob_tuned_label(path)
        if label is None:
            if paths:
                print(f"⚠️ No TM_TUNING on {path.name}, skipping", file=sys.stderr)
            continue
        try:
            cents, bpm_percent = parse_tuning_cents(label)
        except ValueError as e:
            print(f"⚠️ {path.name}: {e}", file=sys.stderr)
            continue

        src = _source_quality(path)
        track_url = src.get("track_url")
        if not track_url:
            print(
                f"⚠️ {path.name}: no provenance.track_url — cannot re-download",
                file=sys.stderr,
            )
            continue
        jobs.append((path, label, cents, bpm_percent, str(track_url)))
    return jobs


def retune_one(
    path: Path,
    *,
    cents: float,
    track_url: str,
    config: Config,
    downloader: Downloader,
    keep_temp: bool,
) -> None:
    """Download clean → tune → replace `path` audio, keeping original tags."""
    target_format = tm_audio.format_from_path(path)
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if keep_temp:
        tmp_dir = Path(tempfile.mkdtemp(prefix="tm-retune-"))
        print(f"  📂 Temp: {tmp_dir}")
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="tm-retune-")
        tmp_dir = Path(tmp_ctx.name)

    try:
        downloader.output_dir = tmp_dir
        result = downloader.download(track_url, format=target_format, show_header=False)
        if result is False:
            raise RuntimeError("Source download failed (no file saved)")

        downloaded = _pick_downloaded_audio(tmp_dir)
        print(f"  ⬇️  Downloaded: {downloaded.name}")

        clean = _stage_matching_format(downloaded, target_format, tmp_dir)
        tuned = tmp_dir / f"tuned{path.suffix.lower()}"
        if tuned.exists():
            tuned.unlink()

        print(f"  🎚️ Pitch-shifting {cents:+.2f}¢ (HQ)…")
        tm_audio.pitch_shift_to(clean, tuned, cents, target_format)

        # Keep the original container's tags/blob (title, TM_TUNING, cues…).
        tm_audio.copy_all_tags(path, tuned)
        try:
            _refresh_blob_audio(tuned)
        except Exception as e:
            print(f"  ⚠️ Blob audio refresh failed: {e}", file=sys.stderr)

        os.replace(tuned, path)
        print(f"  ✅ Replaced audio in place: {path.name}")
    finally:
        # Restore downloader output_dir so the next track doesn't inherit
        # a deleted temp path.
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
        elif keep_temp:
            pass
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run and args.yes:
        print("❌ Use either --dry-run or --yes, not both", file=sys.stderr)
        return 1
    if not args.dry_run and not args.yes:
        print(
            "❌ Refusing to modify library without --yes (or pass -n for dry-run)",
            file=sys.stderr,
        )
        return 1

    config = Config()
    library = args.library
    if library is None:
        library = config.output_dir
    library = library.expanduser().resolve()

    jobs = discover_jobs(
        library,
        paths=args.path,
        include_blob_notes=args.include_blob_notes,
    )
    if not jobs:
        print("No tuned tracks with a re-downloadable URL found.")
        return 0

    print(f"📁 Library: {library}")
    print(f"🎚️ Jobs: {len(jobs)}")
    print("─" * 60)
    for path, label, cents, bpm_percent, url in jobs:
        pct = f"  ({bpm_percent:+g}%)" if bpm_percent is not None else ""
        print(f"  {cents:+7.2f}¢{pct}  {path.name}")
        print(f"           tag={label!r}")
        print(f"           url={url}")
    print("─" * 60)

    if args.dry_run:
        print("ℹ️ Dry run — no downloads or writes")
        return 0

    # One shared Downloader so Spotify/etc clients initialise once.
    downloader = Downloader(config, output_dir=library)
    ok = 0
    failed: list[tuple[Path, str]] = []

    for i, (path, label, cents, bpm_percent, url) in enumerate(jobs, 1):
        print()
        print(f"[{i}/{len(jobs)}] {path.name}")
        print(f"  🏷️  {label} → {cents:+.2f}¢")
        try:
            retune_one(
                path,
                cents=cents,
                track_url=url,
                config=config,
                downloader=downloader,
                keep_temp=args.keep_temp,
            )
            ok += 1
        except Exception as e:
            print(f"  ❌ Failed: {e}", file=sys.stderr)
            failed.append((path, str(e)))
        finally:
            downloader.output_dir = library

    print()
    print(f"✅ Replaced: {ok}")
    print(f"❌ Failed:   {len(failed)}")
    if failed:
        for path, err in failed:
            print(f"  {path.name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
