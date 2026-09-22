"""Shared post-download audio validation helpers.

Used by public lossless proxies (Qobuz, TIDAL, …) and Soulseek to refuse
truncated or free-tier preview files before they enter the library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

# Free-tier / sample clips are consistently ~30s. Reject anything at or
# under this length when catalogue metadata says the track is longer.
PREVIEW_DURATION_MAX_S = 35.0
# Also reject when downloaded audio is less than half the expected length
# (catches truncated non-30s samples / partial transfers).
DURATION_RATIO_MIN = 0.5

_LOSSLESS_CODECS = frozenset({"flac", "alac", "pcm_s16le", "pcm_s24le"})


def is_preview_audio(
    path: Path,
    expected_duration: Optional[float],
) -> Tuple[bool, Optional[float]]:
    """Probe ``path`` and decide whether it looks like a preview/truncation.

    Returns ``(is_preview, actual_duration_seconds)``. ``actual_duration``
    may be None when ffprobe is unavailable or the file is unreadable —
    in that case we return ``(False, None)`` and let the caller decide
    (we never reject on incomplete evidence).

    A file is a preview when:
      * its duration is ≤ ``PREVIEW_DURATION_MAX_S`` (~30s sample) AND
        the catalogue duration is clearly longer, OR
      * its duration is < ``DURATION_RATIO_MIN`` of the expected length
        (expected must be known and positive).
    """
    from . import audio as tm_audio

    probed = tm_audio.probe_audio(path)
    actual = probed.get("duration_seconds")
    if actual is None or actual <= 0:
        return False, actual

    if expected_duration is not None and expected_duration > 0:
        # Classic ~30s sample of a longer track.
        if (
            actual <= PREVIEW_DURATION_MAX_S
            and expected_duration > PREVIEW_DURATION_MAX_S + 5
        ):
            return True, actual
        # Truncated / partial download relative to catalogue length.
        if actual < expected_duration * DURATION_RATIO_MIN:
            return True, actual
    elif actual <= PREVIEW_DURATION_MAX_S:
        # No catalogue duration to compare against — still treat a
        # ≤30s file as suspicious when we asked for a full track, but
        # only if we *also* got a non-FLAC codec (preview path is MP3).
        codec = (probed.get("codec") or "").lower()
        if codec and codec not in _LOSSLESS_CODECS:
            return True, actual

    return False, actual
