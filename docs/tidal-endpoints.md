# TIDAL public endpoints — how it works

`track_manager.tidal_public.TidalPublicClient` does **not** maintain a
hand-curated list of public TIDAL hifi-api endpoints. Instead it fetches the
canonical list from the most active frontend in the ecosystem:

```
https://monochrome.tf/instances.json
```

That JSON is published and refreshed by the monochrome.tf operators (the
same group that runs several of the backend instances). It splits hosts
into two pools:

```jsonc
{
  "api":       [ /* /info/, /search/, /album/, /artist/, /lyrics/ */ ],
  "streaming": [ /* /track/ — needs operator's TIDAL OAuth refresh token */ ]
}
```

Why two pools: the `/track/` endpoint requires the operator's paid TIDAL
OAuth refresh token to return full audio. Most hosts only have OAuth on
some of their backend boxes, so they advertise themselves as "api-only"
or "streaming-only". Calling `/track/` on an api-only host always returns
`401 Token refresh failed` — wasted rotation budget.

## How the client uses this

On `TidalPublicClient.__init__`, we:

1. Read `~/.cache/track-manager/monochrome_instances.json` if present
   and younger than 6 hours, **or** fetch fresh from monochrome.tf and
   cache it.
2. If both fail (offline + no cache), fall back to a tiny hardcoded set
   in `tidal_public.py::_HARDCODED_FALLBACK`.
3. Populate `self.api_endpoints` (used by `get_track_info`) and
   `self.streaming_endpoints` (used by `download_track`) from the result.

Each method rotates through its respective pool and pins the first working
host as `self.endpoint` / `self.streaming_endpoint` for the rest of the
process. Subsequent calls in the same process skip rotation entirely.

## When to investigate

The client should "just work". Investigate only if:

- Downloads consistently fail across the ecosystem (every streaming host
  401/403/timeout). This usually means TIDAL itself rotated some keys
  upstream and operators haven't caught up — wait an hour, retry.
- First-call latency for downloads is suspiciously long (>30s). Probably
  the streaming pool has multiple slow-failing hosts before a working
  one is reached.
- A user reports a regression after the upstream `instances.json` updated.

## How to run the probe

```bash
python scripts/probe_tidal_endpoints.py
```

Optional flags:

- `--track-id <id>` — use a different test track (default `85905134`,
  Coldplay - Paradise; widely available, 222s).
- `--include-historic` — also probe community-mentioned endpoints not in
  `instances.json` (useful for detecting recoveries).
- `--endpoints <url> ...` — flat custom list, ignores api/streaming split.

The script fetches `instances.json`, then probes every unique endpoint
in parallel against `/info/?id=<track>` and `/track/?id=<track>&quality=LOSSLESS`.
It returns exit code `0` if any streaming host serves full audio, `1`
otherwise.

## Classification

| Marker | Meaning |
|---|---|
| ✅ | `/track/?quality=LOSSLESS` returns a valid BTS manifest with stream URLs — real downloads work |
| 🟡 | `/info/` returns 200 with a track title, but `/track/` fails (401/403/PREVIEW) |
| 🔴 | `/info/` also fails (timeout, 5xx, connection refused) |

A host can be ✅ for `/track/` while being 🔴 for `/info/`, or vice versa
(some operators run separate metadata vs. streaming clusters). The probe
output sorts each pool by what *that pool* cares about.

## Why we send `quality=LOSSLESS`

The OpenAPI default for `/track/?quality=` is `HI_RES_LOSSLESS`. Using the
default returns HTTP 200 even when the upstream OAuth flow is broken,
because a non-subscription preview path serves up a 30-second DASH/FLAC
manifest:

```
"assetPresentation": "PREVIEW"
"mediaPresentationDuration": "PT29.907S"
"previewReason": "FULL_REQUIRES_SUBSCRIPTION"
```

That would silently truncate every download to 30s. By pinning
`quality=LOSSLESS` we trigger the subscription-required code path — so the
server *fails loudly* (401/403) when it can't authenticate, and we can
recover with proper error handling instead of shipping previews as full
tracks.

## Common failure shapes

| Symptom | Means |
|---|---|
| `HTTP 401 Token refresh failed: 403 Forbidden for url 'https://auth.tidal.com/v1/oauth2/token'` | Operator's TIDAL OAuth refresh token is dead. Move on. |
| `HTTP 401 Tidal Auth Error: The token has expired. (Bad subject token version)` | Same as above, different fork's wording. |
| `HTTP 403 Upstream API error` | Operator hit a TIDAL API rate limit or geo-block, not the OAuth issue. Often endpoint-specific. |
| `ReadTimeout` after 5–8s | Backend is hung. Typically operator is mid-recovery or has a half-broken auth chain. |
| `502 / 503` | Cloudflare/host-level outage. Try later. |
| `ConnectionError` / NXDOMAIN | Endpoint retired permanently. |

## Important caveats

- **Don't trust `/health` on monochrome hosts.** It probes a metadata path
  that doesn't need OAuth, so it advertises backends as "healthy" even
  when downloads are broken. Always test `/track/` directly.
- **State changes hourly.** A working endpoint can disappear within a
  few hours. The dynamic list from monochrome.tf is the only thing
  worth trusting; our hardcoded `_HARDCODED_FALLBACK` is just a
  last-resort backstop.
- **`instances.json` is best-effort, not authoritative.** monochrome.tf
  operators add/remove hosts as they hear about them. New community
  hosts may not show up for hours/days. Use `--include-historic` if
  you suspect a known host should be working but isn't listed.

## Recent state log

| Date | ✅ streaming working | 🟡 metadata only | Notes |
|---|---|---|---|
| 2026-04-26 | `katze`, `hund`.qqdl.site | (rest broken) | Both serve LOSSLESS but downgrade to AAC `HIGH` silently |
| 2026-04-27 | `katze.qqdl.site` only | 4× monochrome | Brief recovery; full 222s real download confirmed |
| 2026-05-04 (early) | none | 3× monochrome (`api`, `eu-central`, `us-west`) | `katze`/`hund` regressed to read-timeout |
| 2026-05-04 (late) | `hifi.p1nkhamster.xyz` | 4× monochrome + 5× qqdl (api pool) | Discovered `instances.json`; refactored client to use it dynamically. p1nkhamster.xyz was new — never in our hardcoded list |
