"""Key-free catalogue lookup for tracks that song.link can't resolve.

Searches Spotify (when API credentials exist) and Deezer's public API with
an artist/title guess, and only accepts a result that agrees on duration,
version (remix/edit/VIP…), title and artist. A search hit for the original
when the upload is a remix is worse than no hit: it would mislabel the file
and poison ISRC-based dedup.
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional, Sequence

import requests

from .rate_limiter import spotify_rate_limit
from .title_parse import base_title, similar, simplify, version_markers

DEEZER_API = "https://api.deezer.com"
DURATION_TOLERANCE_S = 4.0
_SEARCH_LIMIT = 5


def search_query(guess: dict) -> str:
    """Plain-text query; field-scoped Deezer queries miss too often."""
    return " ".join([*guess["artists"], guess["title"]])


def is_match(candidate: dict, guess: dict) -> bool:
    """True when ``candidate`` is very likely the same recording as ``guess``."""
    expected = guess.get("duration_seconds")
    actual = candidate.get("duration_seconds")
    if not expected or not actual or abs(expected - actual) > DURATION_TOLERANCE_S:
        return False

    if version_markers(candidate["title"]) != version_markers(guess["title"]):
        return False

    if not similar(base_title(candidate["title"]), base_title(guess["title"])):
        return False

    # Remix uploads often credit only the remixer, so accept a catalogue
    # artist that appears anywhere in the guess, not just its artist list.
    haystack = simplify(" ".join([*guess["artists"], guess["title"]]))
    return any(
        _matches_any(name, guess["artists"])
        or (len(simplify(name)) >= 3 and simplify(name) in haystack)
        for name in candidate["artists"]
        if simplify(name)
    )


def _matches_any(name: str, others: Sequence[str]) -> bool:
    return any(similar(name, other) for other in others)


def search_spotify(sp: Any, query: str) -> List[dict]:
    """Spotify track search → normalised candidates (ISRC included)."""
    spotify_rate_limit()
    result = sp.search(q=query, type="track", limit=_SEARCH_LIMIT)
    items = (result or {}).get("tracks", {}).get("items") or []
    return [
        {
            "artists": [a["name"] for a in item.get("artists", [])],
            "title": item.get("name") or "",
            "album": (item.get("album") or {}).get("name"),
            "duration_seconds": (item.get("duration_ms") or 0) / 1000.0 or None,
            "isrc": (item.get("external_ids") or {}).get("isrc"),
            "source": "spotify",
        }
        for item in items
    ]


def search_deezer(query: str, timeout: float = 10.0) -> List[dict]:
    """Deezer public search → normalised candidates (ISRC fetched later)."""
    resp = requests.get(
        f"{DEEZER_API}/search",
        params={"q": query, "limit": _SEARCH_LIMIT},
        timeout=timeout,
    )
    resp.raise_for_status()
    return [
        {
            "artists": [(item.get("artist") or {}).get("name") or ""],
            "title": item.get("title") or "",
            "album": (item.get("album") or {}).get("title"),
            "duration_seconds": float(item.get("duration") or 0) or None,
            "isrc": None,
            "deezer_id": item.get("id"),
            "source": "deezer",
        }
        for item in resp.json().get("data") or []
    ]


def deezer_isrc(track_id: Any, timeout: float = 10.0) -> Optional[str]:
    """ISRC for one Deezer track id (search results omit it)."""
    resp = requests.get(f"{DEEZER_API}/track/{track_id}", timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("isrc") or None


def _first_match(candidates: List[dict], guesses: Sequence[dict]) -> Optional[dict]:
    for candidate in candidates:
        if any(is_match(candidate, guess) for guess in guesses):
            return candidate
    return None


def find_match(guesses: Sequence[dict], spotify: Any = None) -> Optional[dict]:
    """Search Spotify then Deezer with the best guess; return a verified hit.

    The returned dict has the same shape as Spotify metadata elsewhere
    (``artists``, ``title``, ``album``, ``duration_seconds``) plus ``isrc``
    and ``source``. Network errors are reported and treated as "no match".
    """
    if not guesses:
        return None
    query = search_query(guesses[0])

    if spotify is not None:
        try:
            match = _first_match(search_spotify(spotify, query), guesses)
        except Exception as e:
            print(f"⚠️ Spotify search error: {e}", file=sys.stderr)
            match = None
        if match:
            return match

    try:
        match = _first_match(search_deezer(query), guesses)
    except Exception as e:
        print(f"⚠️ Deezer search error: {e}", file=sys.stderr)
        return None
    if match and match.get("deezer_id") is not None:
        try:
            match["isrc"] = deezer_isrc(match["deezer_id"])
        except Exception as e:
            print(f"⚠️ Deezer ISRC lookup error: {e}", file=sys.stderr)
    return match
