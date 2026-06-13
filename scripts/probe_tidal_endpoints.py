#!/usr/bin/env python3
"""Probe TIDAL hifi-api endpoints to classify their current health.

The community-hosted hifi-api ecosystem is volatile: operators rotate, OAuth
refresh tokens expire, hosts get retired. The canonical endpoint list is
maintained by the monochrome.tf operators at:

    https://monochrome.tf/instances.json

split into "api" hosts (for /info/, /search/, /album/, /artist/, /lyrics/)
and "streaming" hosts (for /track/, which needs the operator's TIDAL OAuth
refresh token to be valid). Our client (track_manager.tidal_public) fetches
that list at runtime, so usually you don't need to touch ENDPOINTS at all.
Use this script to *diagnose* which hosts in each pool are currently healthy.

Usage:
    python scripts/probe_tidal_endpoints.py
    python scripts/probe_tidal_endpoints.py --track-id 85905134
    python scripts/probe_tidal_endpoints.py --include-historic

Classification (per endpoint):
    ✅ DOWNLOAD WORKS  /track/?quality=LOSSLESS returns a BTS manifest with
                       at least one stream URL (the operator has a fresh
                       upstream TIDAL OAuth token). Real downloads succeed.
    🟡 METADATA ONLY   /info/?id=X returns 200 with a `data.title`, but
                       /track/ returns 401/403/PREVIEW. Operator's OAuth
                       token is revoked; only public TIDAL metadata works.
    🔴 DEAD            /info/ fails (timeout, 5xx, connection refused) or
                       returns no parseable data.

How to use the output:
    Most of the time, just confirm "Streaming hosts that serve full /track/"
    is non-empty. If it is, the client will find a working host on its own.
    If it's empty across the entire ecosystem, downloads can't work right
    now and you should wait for an operator to rotate their TIDAL OAuth.

Why we send quality=LOSSLESS (not the spec default HI_RES_LOSSLESS):
    HI_RES_LOSSLESS returns 200 even when OAuth is broken, but the manifest
    is a 30-second PREVIEW (assetPresentation=PREVIEW,
    mediaPresentationDuration=PT29.907S). LOSSLESS fails loudly with 401/403
    so we never silently ship truncated audio.
"""

import argparse
import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

INSTANCES_URL = "https://monochrome.tf/instances.json"

# Historic / community-mentioned hosts that aren't (yet) in instances.json,
# probed only with --include-historic to detect recoveries we'd otherwise miss.
HISTORIC_HOSTS = [
    "https://api.monochrome.tf",
    "https://monochrome-api.samidy.com",
    "https://katze.qqdl.site",
    "https://tidal-api.binimum.org",
    "https://hifi.geeked.wtf",
]

# Coldplay - Paradise. 222s, broadly available across TIDAL regions.
# Avoid 77640617 (Daft Punk) — region-blocked on most hosts.
DEFAULT_TRACK_ID = "85905134"

TIMEOUT = 6  # seconds per request — fast enough that a full probe runs in <15s


def probe(endpoint: str, track_id: str) -> dict:
    """Hit /info/ and /track/ on one endpoint, classify the response."""
    out: dict = {"ep": endpoint}

    # /info/ — metadata, doesn't need TIDAL OAuth on the server side
    t0 = time.time()
    try:
        r = requests.get(f"{endpoint}/info/", params={"id": track_id}, timeout=TIMEOUT)
        out["info_status"] = f"HTTP {r.status_code}"
        out["info_time"] = time.time() - t0
        out["info_ok"] = r.ok and (r.json().get("data") or {}).get("title") is not None
    except requests.RequestException as e:
        out["info_status"] = type(e).__name__
        out["info_time"] = time.time() - t0
        out["info_ok"] = False

    # /track/ — manifest. Verify it's a FULL track, not a 30s preview clip.
    t0 = time.time()
    out["track_ok"] = False
    try:
        r = requests.get(
            f"{endpoint}/track/",
            params={"id": track_id, "quality": "LOSSLESS"},
            timeout=TIMEOUT,
        )
        out["track_status"] = f"HTTP {r.status_code}"
        out["track_time"] = time.time() - t0
        if r.ok:
            d = r.json().get("data", {})
            if d.get("assetPresentation") == "PREVIEW":
                out["track_status"] += " PREVIEW(30s)"
            elif "dash" in (d.get("manifestMimeType", "") or ""):
                # MPD manifests are valid but our downloader doesn't support them
                out["track_status"] += " MPD"
            elif d.get("manifest"):
                try:
                    decoded = json.loads(base64.b64decode(d["manifest"]))
                    urls = decoded.get("urls", [])
                    codec = decoded.get("codecs", "?")
                    if urls:
                        out["track_status"] += f" {codec}({len(urls)}u)"
                        out["track_ok"] = True
                    else:
                        out["track_status"] += " no-urls"
                except Exception:
                    out["track_status"] += " bad-manifest"
            else:
                out["track_status"] += " no-manifest"
    except requests.RequestException as e:
        out["track_status"] = type(e).__name__
        out["track_time"] = time.time() - t0

    return out


def classify(r: dict) -> tuple[int, float]:
    """Sort key: (group, time). Group 0 = ✅, 1 = 🟡, 2 = 🔴."""
    if r.get("track_ok"):
        return (0, r["track_time"])
    if r.get("info_ok"):
        return (1, r["info_time"])
    return (2, r["info_time"])


def marker(r: dict) -> str:
    if r.get("track_ok"):
        return "✅"
    if r.get("info_ok"):
        return "🟡"
    return "🔴"


def fetch_master_list(include_historic: bool) -> tuple[list[str], list[str]]:
    """Return (api_hosts, streaming_hosts) from monochrome.tf, plus optional historic."""
    try:
        r = requests.get(INSTANCES_URL, timeout=8)
        r.raise_for_status()
        data = r.json()
        api = [u.rstrip("/") for u in (data.get("api") or [])]
        streaming = [u.rstrip("/") for u in (data.get("streaming") or [])]
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"⚠️ Failed to fetch {INSTANCES_URL}: {e}", file=sys.stderr)
        api, streaming = [], []

    if include_historic:
        for host in HISTORIC_HOSTS:
            host = host.rstrip("/")
            if host not in api:
                api.append(host)
            if host not in streaming:
                streaming.append(host)
    return api, streaming


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--track-id",
        default=DEFAULT_TRACK_ID,
        help=f"TIDAL track id to probe with (default: {DEFAULT_TRACK_ID})",
    )
    parser.add_argument(
        "--endpoints",
        nargs="+",
        help="Probe a custom flat list (no api/streaming split). Overrides instances.json.",
    )
    parser.add_argument(
        "--include-historic",
        action="store_true",
        help="Also probe historic/community endpoints not in instances.json",
    )
    args = parser.parse_args()

    if args.endpoints:
        api_hosts = streaming_hosts = [e.rstrip("/") for e in args.endpoints]
        print(
            f"Probing {len(api_hosts)} custom endpoints with track_id={args.track_id}…\n"
        )
    else:
        api_hosts, streaming_hosts = fetch_master_list(args.include_historic)
        print(
            f"Loaded from {INSTANCES_URL}: {len(api_hosts)} api, "
            f"{len(streaming_hosts)} streaming"
            + (" (+historic)" if args.include_historic else "")
            + f"\nProbing with track_id={args.track_id} ({TIMEOUT}s timeout each)…\n"
        )

    t0 = time.time()
    # Probe each unique endpoint once; we'll classify against both pools below.
    all_hosts = list({*api_hosts, *streaming_hosts})
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda e: probe(e, args.track_id), all_hosts))
    by_ep = {r["ep"]: r for r in results}

    def section(title: str, hosts: list[str], focus: str) -> None:
        if not hosts:
            return
        print(f"\n=== {title} ===")
        rows = sorted(
            (by_ep[h] for h in hosts if h in by_ep),
            key=lambda r: (
                classify(r)
                if focus == "track"
                else (
                    (0, r.get("info_time", 0))
                    if r.get("info_ok")
                    else (1, r.get("info_time", 0))
                )
            ),
        )
        # Both columns are always shown; left is /info/ (metadata), right is /track/ (audio).
        print(
            f"  {'endpoint':<38s} {'/info/':<22s} {'time':<6s}  "
            f"{'/track/?quality=LOSSLESS':<35s} {'time':<6s}"
        )
        for r in rows:
            print(
                f"  {marker(r)} {r['ep']:<36s} {r['info_status']:<22s} "
                f"{r.get('info_time', 0):.1f}s   "
                f"{r['track_status']:<35s} {r.get('track_time', 0):.1f}s"
            )

    section("api pool — /info/ matters", api_hosts, focus="info")
    section("streaming pool — /track/ matters", streaming_hosts, focus="track")

    streaming_ok = [
        r["ep"] for r in results if r.get("track_ok") and r["ep"] in streaming_hosts
    ]
    print(f"\n✅ Streaming hosts that serve full /track/: {len(streaming_ok)}")
    for ep in streaming_ok:
        print(f"     {ep}")
    if not streaming_ok:
        print(
            "     (none — TIDAL OAuth blackout across the ecosystem; wait for re-issue)"
        )
    print(f"\nTotal probe time: {time.time() - t0:.1f}s")
    return 0 if streaming_ok else 1


if __name__ == "__main__":
    sys.exit(main())
