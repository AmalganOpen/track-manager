"""Pioneer USB-player playability classifier.

Rekordbox will import files that some Pioneer USB players then refuse.
``check-compat`` probes each file once (codec, FAT filename, embedded cover)
and scores it against a small set of club decks:

  CDJ-2000NXS   (2012)  MP3/AAC/WAV/AIFF, PCM 44.1/48 kHz. No FLAC/ALAC.
  XDJ-1000MK2   (2016)  same + FLAC/ALAC at 44.1/48 kHz. Huge/progressive
                        embedded covers make it walk past AIFF PCM at EOT.
  CDJ-2000NXS2  (2016)  FLAC/ALAC + PCM up to 96 kHz.
  CDJ-3000      (2020)  same USB format envelope as NXS2.

CDJ-900NXS and the original XDJ-1000 share the NXS decoder. XDJ-XZ,
OPUS-QUAD, and CDJ-TOUR1 share the NXS2/3000 envelope. WAVE_FORMAT_EXTENSIBLE
WAVs, 32-bit float PCM, and compressed AIFF-C are rejected on every listed
player. FAT/USB filename rules apply to all of them.

Default: a track is compatible only if every listed player can play it.
``--gear`` restricts the set. Cover size is an XDJ-1000MK2 check; other
players are not flagged for APIC dimensions.

The classifier is conservative: anything it cannot positively identify is
unknown so the user can eyeball it rather than have a track silently fail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

# FAT32 / Windows / Pioneer USB filename rules. Matches ``sanitize_filename``
# in metadata.py so check-compat and download naming stay aligned.
_FAT_UNSAFE_CHARS = frozenset('\\/:*?"<>|')
# Pioneer / PRO DJ LINK path ceiling commonly cited for CDJs.
_MAX_PATH_CHARS = 255

# Uncompressed integer PCM codecs ffprobe reports for WAV/AIFF. Float PCM
# (pcm_f32*/pcm_f64*) and compressed AIFF-C codecs are deliberately absent.
_PCM_CODECS = {
    "pcm_s16be",
    "pcm_s16le",
    "pcm_s24be",
    "pcm_s24le",
}

# WAVE_FORMAT_EXTENSIBLE format tag. ffprobe surfaces it as codec_tag 0xfffe.
_WAV_EXTENSIBLE_TAG = 0xFFFE

_PCM_EXTS = {".wav", ".aiff", ".aif"}
_AAC_EXTS = {".m4a", ".mp4", ".aac"}
_PCM_48 = frozenset({44100, 48000})
_PCM_96 = frozenset({44100, 48000, 88200, 96000})
_COMPRESSED_MAX_HZ = 48000


class DeviceSpec(NamedTuple):
    """USB format envelope for one Pioneer player (or a same-decoder alias)."""

    id: str
    name: str
    pcm_rates: frozenset[int]
    lossless: bool
    cover_max_side: Optional[int]


def _devices() -> tuple[DeviceSpec, ...]:
    from .cover import COVER_MAX_SIDE

    return (
        DeviceSpec("cdj-2000nxs", "CDJ-2000NXS", _PCM_48, False, None),
        DeviceSpec("xdj-1000mk2", "XDJ-1000MK2", _PCM_48, True, COVER_MAX_SIDE),
        DeviceSpec("cdj-2000nxs2", "CDJ-2000NXS2", _PCM_96, True, None),
        DeviceSpec("cdj-3000", "CDJ-3000", _PCM_96, True, None),
    )


DEVICES: tuple[DeviceSpec, ...] = _devices()

# Canonical id + common nicknames / same-decoder models.
_DEVICE_ALIASES: dict[str, str] = {
    "cdj-2000nxs": "cdj-2000nxs",
    "nxs": "cdj-2000nxs",
    "nxs1": "cdj-2000nxs",
    "cdj-900nxs": "cdj-2000nxs",
    "xdj-1000": "cdj-2000nxs",
    "xdj-1000mk2": "xdj-1000mk2",
    "xdj-1000-mk2": "xdj-1000mk2",
    "xdj1000mk2": "xdj-1000mk2",
    "cdj-2000nxs2": "cdj-2000nxs2",
    "nxs2": "cdj-2000nxs2",
    "cdj-tour1": "cdj-2000nxs2",
    "cdj-3000": "cdj-3000",
    "3000": "cdj-3000",
    "xdj-xz": "cdj-3000",
    "xdj-rx3": "cdj-3000",
    "opus-quad": "cdj-3000",
}


class CompatResult(NamedTuple):
    """Outcome of classifying one file against the selected Pioneer players."""

    compatible: bool
    reason: str
    # ``unknown`` flags files we could not positively classify (missing
    # ffprobe, unreadable, or an extension we don't model). These are not
    # counted as hard incompatibilities — they need a manual look.
    unknown: bool = False
    # Display-name, reason pairs (one entry per issue per failing player).
    failures: tuple[tuple[str, str], ...] = ()


def _probe(path: Path) -> Optional[dict]:
    """Return ffprobe's first audio stream as a dict, or None on failure.

    Includes ``codec_tag`` (needed to detect WAVE_FORMAT_EXTENSIBLE) and
    ``sample_fmt`` (a secondary float-PCM signal) alongside the basics.
    """
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,codec_tag,sample_fmt,sample_rate,"
                "bits_per_raw_sample,bits_per_sample,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(out.stdout)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return None

    streams = data.get("streams") or []
    return streams[0] if streams else None


def _sample_rate(stream: dict) -> Optional[int]:
    try:
        return int(stream.get("sample_rate"))
    except (TypeError, ValueError):
        return None


def _codec_tag(stream: dict) -> Optional[int]:
    """Parse ffprobe's codec_tag (e.g. "0xfffe") into an int, or None."""
    raw = stream.get("codec_tag")
    if raw is None:
        return None
    try:
        return int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
    except (TypeError, ValueError):
        return None


def _norm_gear(name: str) -> str:
    return name.strip().lower().replace(" ", "").replace("_", "-")


def resolve_devices(gear: Optional[Sequence[str]] = None) -> tuple[DeviceSpec, ...]:
    """Return the DeviceSpec set for ``gear``, or every listed player.

    ``gear`` entries are canonical ids or aliases (``nxs``, ``xdj-1000mk2``).
    Unknown names raise ``ValueError``.
    """
    if not gear:
        return DEVICES
    by_id = {d.id: d for d in DEVICES}
    seen: list[DeviceSpec] = []
    seen_ids: set[str] = set()
    known = ", ".join(d.id for d in DEVICES)
    for raw in gear:
        key = _norm_gear(raw)
        canonical = _DEVICE_ALIASES.get(key)
        if canonical is None:
            raise ValueError(f"unknown player {raw!r}; choose from: {known}")
        if canonical in seen_ids:
            continue
        seen_ids.add(canonical)
        seen.append(by_id[canonical])
    if not seen:
        raise ValueError(f"no players selected; choose from: {known}")
    return tuple(seen)


def device_summary(specs: Sequence[DeviceSpec] | None = None) -> str:
    """Comma-separated display names, e.g. ``CDJ-2000NXS, XDJ-1000MK2``."""
    chosen = tuple(specs) if specs is not None else DEVICES
    return ", ".join(d.name for d in chosen)


def classify_filename(name: str) -> Optional[str]:
    """Return a FAT/USB filename incompatibility reason, or None if fine.

    Checks the basename only (what lands on a flat rekordbox USB export).
    Non-ASCII (Chinese, Japanese, etc.) is allowed — FAT32 LFN and HFS+
    both support it; Pioneer USB export fails on the reserved ASCII set
    and path length, not on Unicode.
    """
    if not name or name in {".", ".."}:
        return "empty or reserved filename"

    issues: list[str] = []

    illegal = sorted({c for c in name if c in _FAT_UNSAFE_CHARS})
    if illegal:
        shown = ", ".join(repr(c) for c in illegal)
        issues.append(f"FAT-illegal character(s): {shown}")

    controls = [c for c in name if ord(c) < 32]
    if controls:
        issues.append("contains control character(s)")

    # Trailing space/dot are legal on APFS but rejected on FAT32/Windows.
    if name.endswith(" ") or name.endswith("."):
        issues.append("trailing space or '.' (rejected by FAT32)")

    if len(name) > _MAX_PATH_CHARS:
        issues.append(f"filename length {len(name)} > {_MAX_PATH_CHARS} chars")

    if not issues:
        return None
    return "; ".join(issues)


def _rate_over_ceiling(
    sample_rate: Optional[int], ceiling_hz: int, kind: str
) -> Optional[str]:
    if sample_rate is None or sample_rate <= ceiling_hz:
        return None
    return f"{kind} sample rate {sample_rate} Hz > {ceiling_hz // 1000} kHz"


def _pcm_rate_issue(
    sample_rate: Optional[int], allowed: frozenset[int]
) -> Optional[str]:
    if sample_rate in allowed:
        return None
    ceiling = max(allowed)
    shown = "unknown" if sample_rate is None else str(sample_rate)
    return f"sample rate {shown} Hz > {ceiling // 1000} kHz"


def _format_issue(
    spec: DeviceSpec, path: Path, stream: dict
) -> tuple[Optional[str], bool]:
    """Return ``(reason, unknown)`` for this player. ``reason`` is None if OK."""
    ext = path.suffix.lower()
    codec = (stream.get("codec_name") or "").lower()
    sample_rate = _sample_rate(stream)
    sample_fmt = (stream.get("sample_fmt") or "").lower()

    if codec == "flac":
        if not spec.lossless:
            return "FLAC not supported", False
        return _pcm_rate_issue(sample_rate, spec.pcm_rates), False
    if codec == "alac":
        if not spec.lossless:
            return "Apple Lossless (ALAC) not supported", False
        return _pcm_rate_issue(sample_rate, spec.pcm_rates), False

    if codec == "mp3":
        return _rate_over_ceiling(sample_rate, _COMPRESSED_MAX_HZ, "MP3"), False

    if codec == "aac":
        return _rate_over_ceiling(sample_rate, _COMPRESSED_MAX_HZ, "AAC"), False

    if ext in _PCM_EXTS or codec.startswith("pcm_"):
        if codec.startswith("pcm_f"):
            return f"32-bit float PCM ({codec}) not supported", False
        if "flt" in sample_fmt or "dbl" in sample_fmt:
            return f"float PCM (sample_fmt={sample_fmt}) not supported", False
        if codec not in _PCM_CODECS:
            return (
                f"non-PCM/compressed codec ({codec or 'unknown'}) not supported",
                False,
            )
        rate_issue = _pcm_rate_issue(sample_rate, spec.pcm_rates)
        if rate_issue:
            return rate_issue, False
        if ext == ".wav" and _codec_tag(stream) == _WAV_EXTENSIBLE_TAG:
            return "WAVE_FORMAT_EXTENSIBLE header not supported", False
        return None, False

    if ext in _AAC_EXTS:
        kind = codec.upper() or "AAC"
        return _rate_over_ceiling(sample_rate, _COMPRESSED_MAX_HZ, kind), False

    return f"unrecognised format ({codec or ext or 'no extension'})", True


def _cover_issue(raw: Optional[bytes], max_side: int) -> Optional[str]:
    """Return an embedded-cover incompatibility reason, or None if fine."""
    from .cover import jpeg_info, needs_scale

    if not raw:
        return None
    info = jpeg_info(raw)
    if info is None:
        return f"non-JPEG cover ({len(raw)} bytes); run tm scale-covers"
    if not needs_scale(info, max_side):
        return None
    parts: list[str] = []
    if max(info.width, info.height) > max_side:
        parts.append(f"{info.width}×{info.height} > {max_side}px")
    if info.progressive:
        parts.append("progressive JPEG")
    return f"{'; '.join(parts)}; run tm scale-covers"


def classify_cover(path: Path, max_side: Optional[int] = None) -> Optional[str]:
    """Return an embedded-cover incompatibility reason, or None if fine.

    No cover is OK. Progressive JPEG or a long side above ``max_side``
    (default: XDJ-1000MK2 cap) is not — see ``track_manager.cover``.
    """
    from .cover import COVER_MAX_SIDE, extract_embedded_cover

    cap = COVER_MAX_SIDE if max_side is None else max_side
    return _cover_issue(extract_embedded_cover(path), cap)


def _summarize_failures(failures: Sequence[tuple[str, str]], n_devices: int) -> str:
    """Collapse per-player reasons: attach ``[players]`` when not universal."""
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for name, reason in failures:
        grouped.setdefault(reason, []).append(name)
    parts: list[str] = []
    for reason, names in grouped.items():
        if len(names) == n_devices:
            parts.append(reason)
        else:
            parts.append(f"{reason} [{', '.join(names)}]")
    return "; ".join(parts)


def classify(path: Path, *, gear: Optional[Sequence[str]] = None) -> CompatResult:
    """Decide whether the selected Pioneer USB players can play ``path``.

    Default ``gear`` is every listed player: a track must pass all of them.
    Filename problems apply to every player. Cover size is only checked on
    players that declare a cap (XDJ-1000MK2).
    """
    specs = resolve_devices(gear)
    name_issue = classify_filename(path.name)

    need_cover = any(s.cover_max_side for s in specs)
    cover_raw: Optional[bytes] = None
    if need_cover:
        from .cover import extract_embedded_cover

        cover_raw = extract_embedded_cover(path)

    probe_available = bool(shutil.which("ffprobe"))
    stream: Optional[dict] = _probe(path) if probe_available else None
    if not probe_available:
        probe_reason, probe_unknown = "ffprobe unavailable - cannot verify", True
    elif stream is None:
        probe_reason, probe_unknown = "unreadable or no audio stream", True
    else:
        probe_reason, probe_unknown = None, False

    failures: list[tuple[str, str]] = []
    any_hard = False
    any_unknown = False

    for spec in specs:
        reasons: list[str] = []
        unknown_here = False
        if probe_reason:
            reasons.append(probe_reason)
            unknown_here = probe_unknown
        else:
            assert stream is not None
            fmt_reason, fmt_unknown = _format_issue(spec, path, stream)
            if fmt_reason:
                reasons.append(fmt_reason)
                unknown_here = fmt_unknown
        if name_issue:
            reasons.append(f"filename: {name_issue}")
        cover_reason = None
        if spec.cover_max_side:
            cover_reason = _cover_issue(cover_raw, spec.cover_max_side)
            if cover_reason:
                reasons.append(f"cover: {cover_reason}")
        if not reasons:
            continue
        for reason in reasons:
            failures.append((spec.name, reason))
        if unknown_here and not name_issue and not cover_reason:
            any_unknown = True
        else:
            any_hard = True

    if not failures:
        if stream is None:
            return CompatResult(False, probe_reason or "unreadable", unknown=True)
        codec = (stream.get("codec_name") or "").lower()
        sample_rate = _sample_rate(stream)
        if codec.startswith("pcm_"):
            depth = "24-bit" if "24" in codec else "16-bit"
            label = f"PCM {depth} {sample_rate // 1000}kHz" if sample_rate else "PCM"
        else:
            label = codec.upper() or path.suffix.lower().lstrip(".") or "audio"
        return CompatResult(True, label)

    unknown = any_unknown and not any_hard
    reason = _summarize_failures(failures, len(specs))
    return CompatResult(False, reason, unknown=unknown, failures=tuple(failures))


# Audio extensions we attempt to classify when scanning a directory.
_AUDIO_EXTS = _PCM_EXTS | _AAC_EXTS | {".mp3", ".flac", ".ogg", ".opus"}


def scan_dir(
    library_dir: Path, *, gear: Optional[Sequence[str]] = None
) -> list[tuple[Path, CompatResult]]:
    """Classify every audio file directly in ``library_dir`` (non-recursive).

    Hidden files and directories (including ``.tm-migration-backup/``) are
    skipped so backups don't pollute the audit.
    """
    results: list[tuple[Path, CompatResult]] = []
    for p in sorted(library_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() not in _AUDIO_EXTS:
            continue
        results.append((p, classify(p, gear=gear)))
    return results
