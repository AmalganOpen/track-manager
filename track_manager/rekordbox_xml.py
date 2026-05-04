"""Rewrite a Rekordbox XML export to point at migrated AIFFs.

After ``tm migrate-to-aiff`` your library on disk holds AIFFs but
Rekordbox still has the old M4A/MP3 paths recorded for every track.
Pioneer's XML import flow is the supported way to bulk-update those
paths while preserving cue points, beat grids, loops, and all per-track
metadata — those live inside the XML and are keyed by ``TrackID``, not
by file content.

Workflow:

    1. tm migrate-to-aiff
    2. In Rekordbox: Preferences → Advanced → enable "rekordbox xml".
    3. File → Export Collection in xml format → save as ``collection.xml``.
    4. tm rekordbox-rewrite collection.xml
    5. In Rekordbox: File → Library → Import Library → pick the rewritten
       ``collection.aiff.xml``.
    6. Verify a few tracks: cue points + beat grid intact, audio plays
       from the AIFF.

The rewriter only modifies ``<TRACK>`` elements whose ``Location``
resolves inside the configured library directory. Anything else (demo
content, tracks imported from other folders, etc.) is preserved
verbatim. The output is written to a new file; the input XML is never
modified.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple, Optional

# Rekordbox writes file URLs in macOS NSURL form with the explicit
# "localhost" host (e.g. file://localhost/Users/...).  Some older exports
# use the spec-correct file:/// form; we accept both on read and emit the
# Rekordbox-preferred form on write so re-imports look native.
_RB_URL_PREFIX = "file://localhost"

# Rekordbox's user-facing "Kind" attribute strings, keyed by extension.
_KIND_BY_EXT = {
    ".aiff": "AIFF File",
    ".aif": "AIFF File",
    ".m4a": "M4A File",
    ".mp3": "MP3 File",
    ".wav": "WAV File",
    ".flac": "FLAC File",
}


class TrackUpdate(NamedTuple):
    track_id: str
    old_path: Path
    new_path: Path


class RewriteResult(NamedTuple):
    updated: list[TrackUpdate]
    skipped_outside_library: list[tuple[str, Path]]
    skipped_no_aiff: list[tuple[str, Path]]
    skipped_already_aiff: list[tuple[str, Path]]
    parse_errors: list[str]


def rewrite_xml(
    input_path: Path,
    output_path: Path,
    library_dir: Path,
) -> RewriteResult:
    """Read `input_path`, rewrite library tracks to AIFF, write `output_path`.

    Pure: never modifies the input file. Categorises every track in the
    XML so the caller can report exactly what changed.
    """
    library_dir = library_dir.resolve()

    parser = ET.XMLParser(encoding="utf-8")
    tree = ET.parse(str(input_path), parser=parser)
    root = tree.getroot()

    updated: list[TrackUpdate] = []
    outside: list[tuple[str, Path]] = []
    no_aiff: list[tuple[str, Path]] = []
    already_aiff: list[tuple[str, Path]] = []
    parse_errors: list[str] = []

    for track in root.iter("TRACK"):
        location = track.get("Location")
        if not location:
            # Inside <PLAYLISTS> Rekordbox uses <TRACK Key="123"/> entries
            # that reference a TrackID without a Location attribute.
            # Those are pure references; we leave them alone.
            continue

        track_id = track.get("TrackID", "")
        try:
            old_path = _decode_rb_url(location)
        except ValueError as e:
            parse_errors.append(f"TrackID={track_id}: {e}")
            continue

        if not _is_inside(old_path, library_dir):
            outside.append((track_id, old_path))
            continue

        if old_path.suffix.lower() in (".aiff", ".aif"):
            already_aiff.append((track_id, old_path))
            continue

        new_path = old_path.with_suffix(".aiff")
        if not new_path.exists():
            no_aiff.append((track_id, old_path))
            continue

        _update_track(track, new_path)
        updated.append(TrackUpdate(track_id, old_path, new_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), encoding="utf-8", xml_declaration=True)

    return RewriteResult(
        updated=updated,
        skipped_outside_library=outside,
        skipped_no_aiff=no_aiff,
        skipped_already_aiff=already_aiff,
        parse_errors=parse_errors,
    )


# ---------------------------------------------------------------------------
# Per-track update
# ---------------------------------------------------------------------------


def _update_track(track: ET.Element, new_path: Path) -> None:
    """Point a <TRACK> at `new_path` and refresh its audio attributes.

    Cue points (<POSITION_MARK>), beat grid (<TEMPO>), and tonality
    (<KEY>) are not touched — they are time-based and remain valid as
    long as the new file's duration matches the old one (the migration
    enforces this with a 50 ms tolerance).
    """
    track.set("Location", _encode_rb_url(new_path))
    track.set("Kind", _KIND_BY_EXT.get(new_path.suffix.lower(), "AIFF File"))

    try:
        track.set("Size", str(new_path.stat().st_size))
    except OSError:
        pass

    info = _probe_for_xml(new_path)
    if info.get("bitrate_kbps") is not None:
        track.set("BitRate", str(info["bitrate_kbps"]))
    if info.get("sample_rate") is not None:
        track.set("SampleRate", str(info["sample_rate"]))


def _probe_for_xml(path: Path) -> dict:
    """Probe `path` for the audio attributes Rekordbox stores."""
    from . import audio as tm_audio

    info = tm_audio.probe_audio(path)
    return {
        "bitrate_kbps": info.get("bitrate_kbps"),
        "sample_rate": info.get("sample_rate"),
    }


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _decode_rb_url(url: str) -> Path:
    """Decode a Rekordbox file URL into a Path. Accepts both forms."""
    if url.startswith(_RB_URL_PREFIX):
        rest = url[len(_RB_URL_PREFIX):]
    elif url.startswith("file:///"):
        # spec-correct form: file:///Users/...
        rest = url[len("file://"):]
    else:
        raise ValueError(f"unrecognised URL scheme: {url[:40]!r}")

    decoded = urllib.parse.unquote(rest)
    if not decoded.startswith("/"):
        raise ValueError(f"path is not absolute after decode: {decoded!r}")
    return Path(decoded)


def _encode_rb_url(path: Path) -> str:
    """Encode a Path into Rekordbox's file URL form."""
    # Keep '/' literal so the path stays human-readable; everything else
    # gets percent-encoded the same way Rekordbox does it.
    encoded = urllib.parse.quote(str(path), safe="/")
    return f"{_RB_URL_PREFIX}{encoded}"


def _is_inside(child: Path, parent: Path) -> bool:
    """Return True if `child` is `parent` or a descendant of it."""
    try:
        # Don't resolve `child` because the file may not exist (we expect
        # the old M4A/MP3 to be moved to .tm-migration-backup/ already).
        # Rely on lexical containment after both are absolute.
        child_abs = child if child.is_absolute() else (Path.cwd() / child).resolve()
        child_abs.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def default_output_path(input_path: Path) -> Path:
    """Pick a sensible default output filename next to `input_path`."""
    return input_path.with_name(f"{input_path.stem}.aiff{input_path.suffix}")
