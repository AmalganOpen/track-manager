# Track Download Process

This document explains the complete download workflow in track-manager, from URL input to final file placement.

## Overview

track-manager uses a quality-first approach with automatic fallback:

1. **Source Detection** - Identify platform (Spotify, YouTube, SoundCloud, etc.)
2. **TIDAL Public API** - Try to get lossless FLAC (no credentials required)
3. **FLAC Conversion** - Convert to efficient M4A 256kbps
4. **Fallback** - Use original source if TIDAL unavailable
5. **Metadata** - Apply comprehensive tags + cover art (including ISRC from TIDAL)
6. **Organization** - Clean naming and placement

**Legacy Note:** Previously, track-manager used DAB Music for high-quality downloads, which required account credentials. DAB Music service became unavailable in early 2026. The system now uses TIDAL's public API, which requires no credentials and provides the same lossless FLAC quality. DAB Music configuration is kept in the config file for potential future use if the service returns.

## Command Flow

### Basic Usage

```bash
track-manager download <url>
```

**Supported Sources:**

- **Spotify tracks** - Work without credentials (via TIDAL)
- **Spotify playlists/albums** - Require Spotify API credentials (optional)
- **YouTube** - No credentials needed (via TIDAL)
- **SoundCloud** - No credentials needed (via TIDAL)
- **Direct audio URLs** - No credentials needed

**No Setup Required** - Works immediately for all sources except Spotify playlists

## Complete Download Flow

### High-Level Flow

**1. Input**
- URL from any supported platform

**2. Source Detection**
- Detect: Spotify / YouTube / SoundCloud / Direct

**3. Smart Download (TIDAL Public API)**
- URL → song.link → TIDAL ID
- TIDAL Public API → Track info (includes ISRC + metadata)
- If found: Download FLAC → Convert to M4A 256kbps → Apply metadata → Delete FLAC
- If not found: Continue to fallback

**4. Fallback Path (if not on TIDAL)**
- YouTube → yt-dlp (M4A ~130kbps, format 140)
- Spotify (with credentials) → spotdl → YouTube (M4A ~130kbps)
- Spotify (without credentials) → Try TIDAL via smart download
- SoundCloud → yt-dlp (M4A ~256kbps)
- Direct → requests (preserve original)

**5. Final Output**
- File: `Artist - Title.m4a`
- Location: Output directory (flat structure)
- Quality: Best available (TIDAL FLAC preferred, fallback acceptable)

### 1. Source Detection

```python
def detect_source(url: str) -> str:
    if "spotify.com" in url:
        return "spotify"
    elif "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    elif "soundcloud.com" in url:
        return "soundcloud"
    else:
        return "direct"
```

**Detected:**
- `spotify` - Spotify URLs
- `youtube` - YouTube URLs (including youtu.be short links)
- `soundcloud` - SoundCloud URLs
- `direct` - Everything else (assumes direct audio URL)

### 2. Spotify API Handling (Optional)

**Spotify API is only required for playlists/albums:**

#### With Spotify Credentials
```python
# For playlists/albums - enumerate tracks
songs = spotdl.search([playlist_url])

# Each song has metadata including ISRC
for song in songs:
    isrc = song.isrc  # Provided directly from Spotify
    # Try TIDAL smart download first, fallback to YouTube
    try_smart_download(song.url, isrc=isrc, spotify_metadata={...})
```

#### Without Spotify Credentials
```python
# Individual tracks - route through TIDAL
if is_playlist:
    print("❌ Spotify playlists require API credentials")
    print("Individual tracks work without credentials (via TIDAL)")
    return
else:
    # Use smart download (song.link → TIDAL)
    success = try_smart_download(url)
    if not success:
        print("❌ Failed via TIDAL, Spotify API needed")
```

**Setup (optional - only for playlists):**
```bash
export SPOTIPY_CLIENT_ID="your_id"
export SPOTIPY_CLIENT_SECRET="your_secret"
```

Or add to `config.yaml`:
```yaml
spotdl:
  client_id: "your_id"
  client_secret: "your_secret"
```

### 3. TIDAL Public API Integration

#### song.link Lookup

**For any URL:**
```python
# Convert any platform URL to TIDAL ID
response = requests.get(f"https://api.song.link/v1-alpha.1/links?url={url}")
data = response.json()
tidal_url = data['linksByPlatform']['tidal']['url']

# Extract TIDAL ID
tidal_id = tidal_url.split('/')[-1]
# Example: "450756447"
```

**Supported conversions:**
- Spotify → TIDAL
- YouTube → TIDAL
- Apple Music → TIDAL
- SoundCloud → TIDAL
- Deezer → TIDAL
- And more...

#### Get Track Info

```python
# Query TIDAL public API (one of multiple endpoints)
endpoint = "https://api.monochrome.tf"  # Primary endpoint

response = requests.get(
    f"{endpoint}/info/",
    params={"id": tidal_id},
    timeout=30
)

track_data = response.json()['data']
```

**What we get:**
```python
{
    'id': 450756447,
    'title': 'Mr. Brightside',
    'artists': [{'name': 'The Killers'}],
    'album': {
        'title': 'Hot Fuss',
        'cover': 'cover-id-string'
    },
    'isrc': 'GBFFP0300052',
    'streamStartDate': '2004-09-27T00:00:00.000+0000'
}
```

**Key benefits over legacy DAB Music:**
- ✅ No credentials required
- ✅ ISRC included in response (no separate lookup needed)
- ✅ Multiple public endpoints (automatic fallback)
- ✅ Works with any platform URL via song.link
- ✅ Community-maintained infrastructure
- ✅ Same lossless FLAC quality

#### Download FLAC

```python
# Download from TIDAL public API
response = requests.get(
    f"{endpoint}/track/",
    params={
        "id": tidal_id,
        "quality": "LOSSLESS"  # 16-bit/44.1kHz, or "HI_RES_LOSSLESS" for 24-bit
    },
    timeout=30
)

manifest = response.json()
download_url = manifest['urls'][0]  # First CDN URL

# Download FLAC file
flac_response = requests.get(download_url, timeout=120)
output_path.write_bytes(flac_response.content)
```

**Result:** FLAC file (~30-40MB per track)
- Format: FLAC lossless
- Bitrate: ~1411kbps (CD quality)
- Sample rate: 44.1kHz
- Bit depth: 16-bit
- Includes all metadata tags

**Public API Endpoints:**
The system uses multiple community-hosted TIDAL endpoints with automatic fallback:
- `https://api.monochrome.tf` (primary)
- `https://triton.squid.wtf`
- `https://wolf.qqdl.site`
- `https://tidal-api.binimum.org`

If one endpoint fails, the system automatically tries the next one.

### 4. Metadata Application

**Applied to FLAC before conversion:**

```python
from mutagen.flac import FLAC, Picture

audio = FLAC(flac_path)

# Prefer Spotify metadata when available (from playlists)
# Spotify has better multi-artist support
if spotify_metadata:
    audio['TITLE'] = spotify_metadata['title']
    audio['ARTIST'] = ', '.join(spotify_metadata['artists'])
    audio['ALBUM'] = spotify_metadata['album']
    audio['DATE'] = spotify_metadata['date']
else:
    # Use TIDAL metadata as fallback
    artists = [a['name'] for a in track_data['artists']]
    audio['TITLE'] = track_data['title']
    audio['ARTIST'] = ', '.join(artists)
    audio['ALBUM'] = track_data['album']['title']
    audio['DATE'] = track_data['streamStartDate'].split('T')[0]

# ISRC always from TIDAL (authoritative source)
audio['ISRC'] = track_data['isrc']

# Cover art from TIDAL CDN
cover_id = track_data['album']['cover']
cover_path = cover_id.replace('-', '/')  # Convert to path format
cover_url = f"https://resources.tidal.com/images/{cover_path}/1280x1280.jpg"

cover_data = requests.get(cover_url).content
picture = Picture()
picture.type = 3  # Cover (front)
picture.data = cover_data
picture.mime = 'image/jpeg'
picture.width = 1280
picture.height = 1280
audio.add_picture(picture)

audio.save()
```

**Metadata priority:**
1. **Spotify metadata** (when from playlist) - Better multi-artist handling, consistent titles
2. **TIDAL metadata** (fallback) - Still high quality and complete
3. **ISRC** - Always from TIDAL (most authoritative)
4. **Cover art** - Always from TIDAL CDN (high resolution)

### 5. FLAC → M4A Conversion

#### Extract Cover Art

```python
# Read FLAC and extract cover art for re-embedding
flac_audio = FLAC(flac_path)
cover_data = flac_audio.pictures[0].data if flac_audio.pictures else None
```

#### Convert Audio with FFmpeg

```python
# FFmpeg conversion preserving quality
subprocess.run([
    'ffmpeg',
    '-i', str(flac_path),
    '-vn',                    # Skip video/cover art (prevents H.264 encoding)
    '-c:a', 'aac',           # AAC codec
    '-b:a', '256k',          # 256kbps bitrate (transparent from FLAC)
    '-movflags', '+faststart', # Optimize for streaming
    '-map_metadata', '0',    # Copy all metadata
    '-y',                    # Overwrite if exists
    str(m4a_path)
], check=True)
```

**Settings:**
- **Codec:** AAC (Advanced Audio Coding) - Universal compatibility
- **Bitrate:** 256kbps constant - Transparent quality from FLAC
- **Sample rate:** 44.1kHz (preserved from FLAC)
- **Channels:** Stereo
- **Optimization:** Fast-start enabled for streaming/DJ software

**Result:** M4A file (~6-7MB per track)
- 80% smaller than FLAC
- Transparent quality (imperceptible from lossless)
- Universal compatibility (all DJ software, players, devices)
- Optimized for streaming and quick loading

#### Re-embed Cover Art

```python
# Embed cover art into M4A
# (FFmpeg's -vn skips cover art to prevent codec issues)
from mutagen.mp4 import MP4, MP4Cover

m4a_audio = MP4(m4a_path)
if cover_data:
    m4a_audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
m4a_audio.save()
```

**Why re-embed:**
- FFmpeg's `-vn` flag skips cover art (prevents H.264 video encoding issues)
- We extract cover art before conversion
- Re-embed after conversion using mutagen
- Ensures cover art is properly stored in M4A container (not as video track)

#### Add Provenance Metadata

```python
# Track source quality for transparency
m4a_audio['----:com.apple.iTunes:ORIGINAL_BITRATE'] = b'1411'
m4a_audio['----:com.apple.iTunes:SOURCE'] = b'TIDAL FLAC'
m4a_audio.save()
```

**Why track provenance:**
- Shows true source quality (1411kbps FLAC, not 256kbps encoding)
- Prevents misleading quality assessments
- Useful for `track-manager check-quality` command
- Documents the download source

#### Cleanup

```python
# Delete FLAC file (keep only optimized M4A)
flac_path.unlink()

print(f"✅ Downloaded and converted to M4A: {m4a_path}")
```

### 6. Fallback to Original Source

**When TIDAL download fails:**
- Track not found on TIDAL (via song.link)
- TIDAL API error (all endpoints failed)
- Conversion error

**Fallback routing:**

```python
# Route to appropriate source handler
if source_type == "spotify":
    if has_spotify_credentials():
        handler = SpotifyDownloader(config, output_dir)
    else:
        print("❌ Track not available via TIDAL")
        print("   Spotify API credentials needed")
        return
elif source_type == "youtube":
    handler = YouTubeDownloader(config, output_dir)
elif source_type == "soundcloud":
    handler = SoundCloudDownloader(config, output_dir)
else:
    handler = DirectDownloader(config, output_dir)

handler.download(url, format)
```

#### YouTube Fallback (via yt-dlp)

**Process:**
```python
# yt-dlp with format preference
ydl_opts = {
    'format': '140/251/bestaudio',  # M4A AAC → Opus → Best
    'outtmpl': str(output_dir / '%(title)s.%(ext)s'),
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])
```

**Quality:**
- Format 140: M4A ~130kbps AAC (YouTube's actual audio quality)
- Format 251: Opus ~160kbps container (~130kbps actual quality)
- Native M4A preferred (no conversion needed)

**Result:** M4A ~130kbps (matches YouTube's source quality)

#### Spotify Fallback (via spotdl)

**Process:**
```python
# spotdl uses Spotify API for metadata, YouTube for audio
from spotdl import Spotdl

spotdl = Spotdl(client_id=..., client_secret=...)
songs = spotdl.search([url])

for song in songs:
    spotdl.download(song)
```

**What happens:**
1. Spotify API → Get track metadata (artist, title, album, ISRC)
2. YouTube search → Find matching audio
3. yt-dlp → Download from YouTube (~130kbps)
4. Apply Spotify metadata to file

**Quality:** M4A ~130kbps (same as YouTube, but better metadata)

**Note:** Spotify doesn't provide direct audio downloads. spotdl searches YouTube for the track and downloads from there. This is why the audio quality matches YouTube's actual quality (~130kbps), not Spotify's streaming quality (320kbps).

#### SoundCloud Fallback (via yt-dlp)

**Process:**
```python
# yt-dlp with SoundCloud extractor
ydl_opts = {
    'format': 'bestaudio',
    'outtmpl': str(output_dir / '%(title)s.%(ext)s'),
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'm4a',
        'preferredquality': '256',
    }],
}
```

**Quality:**
- Free SoundCloud: ~128kbps source
- SoundCloud Go+: ~256kbps source
- Converted to M4A 256kbps (encoding bitrate to avoid double compression)

**Result:** M4A ~256kbps (quality limited by source)

## Duplicate Detection & In-Place Upgrade

track-manager checks whether a track is already in the library so it doesn't
download the same recording twice. *Where* in the flow this happens depends on
the source, and the smart-download path additionally treats an already-owned
**lossy** copy as an opportunity to upgrade rather than a plain duplicate.

### When detection runs (by source)

| Path | Duplicate check |
|------|-----------------|
| Spotify (with API credentials) | **Before** download — by URL → ISRC → artist/title |
| Spotify (no credentials → smart download) | **Before** download — see *Smart-download dedup* below |
| SoundCloud (yt-dlp fallback) | **Before** audio download (cheap metadata-only fetch) |
| YouTube (yt-dlp fallback) | **Before** (metadata-only `extract_info` probe) + after-download backstop |
| Direct URLs | **Before** (by source URL) + after-download backstop |
| Smart download (Qobuz / TIDAL) | **Before** download — see *Smart-download dedup* below |

All of these route their skip/keep decision through the single
`duplicates.handle_duplicates()` helper, which interprets the
`duplicates.handling` config mode (see below).

The yt-dlp paths (YouTube/SoundCloud) do a cheap metadata-only
`extract_info(download=False)` first, and direct downloads match on the source
URL — so a re-download of an already-owned track is caught **before** any audio
bytes are fetched. These pre-checks run even with `--dumb` (where the
smart-download dedup is bypassed); the post-download check remains as a backstop
for tracks whose metadata is only known after download.

### Smart-download dedup (Qobuz / TIDAL)

The smart-download path (`Downloader.try_smart_download`) is the
quality-first path: its whole purpose is to fetch a *better* version (lossless
FLAC) than the original source. So a blunt "already exists → skip" would be
wrong — it would block legitimate quality upgrades. Instead, once the ISRC has
been resolved (the strongest cross-source identity), it makes a
**quality-aware** decision:

1. **Find an owned copy** by strong identity only — **ISRC first, then the
   stored `TRACK_URL`**. Artist/title is intentionally *not* used here, so an
   in-place upgrade can never replace a *different* recording (e.g. a live or
   remix variant) that merely shares a name.
2. **Already own a lossless copy** (source codec `flac`/`alac`/`pcm`/`aiff`/…):
   the smart path can't improve on it, so it's a pure duplicate — defer to the
   configured `duplicates.handling` mode (skip / keep / interactive).
3. **Own only a lossy copy** (e.g. 128 kbps AAC) and under the attempt cap:
   attempt an **in-place upgrade** via the same machinery as `tm upgrade`. The
   file is replaced at its existing path/filename so Rekordbox cue points
   survive.
4. **Own a lossy copy but the attempt cap is reached**: skip. Tracks that these
   sources only ever serve in low quality (e.g. YouTube-only uploads) are not
   retried forever.

**This only runs on the smart-download path.** With `--dumb`,
`try_smart_download` returns immediately, so the in-place upgrade never fires —
direct/dumb downloads keep their original post-download metadata dedup only.

#### Attempt cap

The number of auto-upgrade attempts is capped (`_SMART_UPGRADE_MAX_ATTEMPTS`,
currently **2**). The counter is the same per-file
`provenance.upgrade_attempts` value used by `tm upgrade`, and it is incremented
**before** each attempt — so a crash or a failed attempt still counts, and the
same track can't be retried indefinitely.

#### What if the upgrade is the same or lower quality?

The re-download happens into a temporary directory and `upgrade_track`
compares the new file's bitrate against the owned copy's *source* bitrate
(`provenance.original_bitrate`, not the container bitrate). If the new download
is **not strictly better**:

- the existing file is **left untouched** (no replacement),
- the temporary download is discarded,
- the attempt **still counts** toward the cap (the counter was bumped up
  front), and
- the run prints `⏭️ Kept existing copy (New download (… kbps) is not better
  than source (… kbps))` and reports success (handled), so no duplicate file is
  written alongside the original.

This means a lossy track that these sources can't actually improve is attempted
at most twice and then left alone.

### `duplicates.handling` config

```yaml
# config.yaml
duplicates:
  # interactive | skip | keep
  handling: "interactive"
```

- **skip** — keep the existing file, don't download the new one.
- **keep** — keep both (download proceeds, second file written).
- **interactive** — prompt: `[s]` skip / `[k]` keep both / `[r]` replace
  existing.

For the smart-download path this mode governs the *lossless duplicate* case
(step 2 above). The lossy in-place upgrade (step 3) is a separate, quality-driven
action and always replaces in place when the new download is genuinely better.

## File Naming & Organization

### Filename Format
