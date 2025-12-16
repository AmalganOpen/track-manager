# Track Download Process

This document explains the complete download workflow in track-manager, from URL input to final file placement.

## Overview

track-manager uses a quality-first approach with automatic fallback:

1. **ISRC Lookup** - Find universal track identifier
2. **DAB Music Search** - Try to get lossless FLAC
3. **FLAC Conversion** - Convert to efficient M4A 256kbps
4. **Fallback** - Use original source if DAB unavailable
5. **Metadata** - Apply comprehensive tags + cover art
6. **Organization** - Clean naming and placement

## Command Flow

### Basic Usage

```bash
track-manager download <url>
```

**Supported Sources:**

- Spotify (best ISRC success rate)
- YouTube (via song.link → Spotify)
- SoundCloud (via song.link → Spotify)
- Direct audio URLs (no ISRC)

## Complete Download Flow

### High-Level Flow

**1. Input**

- URL from any supported platform

**2. Source Detection**

- Detect: Spotify / YouTube / SoundCloud / Direct

**3. ISRC Lookup**

- Spotify → Direct Spotify API
- Others → song.link → Spotify API
- Result: ISRC code (or None)

**4a. DAB Music Path (if ISRC found)**

- Search DAB Music by ISRC
- If found: Download FLAC → Convert to M4A 256kbps → Apply metadata → Delete FLAC
- If not found: Continue to fallback

**4b. Fallback Path (if no ISRC or not on DAB)**

- YouTube → yt-dlp (M4A ~160kbps, format 251/140)
- Spotify → spotdl (M4A ~160kbps, format 251/140)
- SoundCloud → yt-dlp (M4A ~128kbps)
- Direct → requests (preserve original)

**5. Final Output**

- File: `Artist - Title.m4a`
- Location: Output directory (flat structure)
- Quality: Best available (DAB Music preferred, fallback acceptable)

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

### 2. ISRC Lookup

#### Tier 1: Direct from Spotify

**For Spotify URLs:**

```python
# Extract Spotify track ID
spotify_id = extract_from_url("https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp")
# spotify_id = "3n3Ppam7vgaVa1iaRUc9Lp"

# Query Spotify API
track_data = spotify_api.track(spotify_id)
isrc = track_data['external_ids']['isrc']
# isrc = "GBFFP0300052"
```

**Requirements:**

- Spotify API credentials (client_id, client_secret)
- Set via environment variables or config file

#### Tier 2: Via song.link

**For Non-Spotify URLs:**

```python
# Query song.link to find Spotify match
songlink_data = songlink_api.find_platforms(youtube_url)
spotify_url = songlink_data.get('spotify')

if spotify_url:
    # Extract Spotify ID and get ISRC (same as Tier 1)
    spotify_id = extract_from_url(spotify_url)
    isrc = spotify_api.track(spotify_id)['external_ids']['isrc']
```

(requires song.link to find Spotify match)

**How it works:**

1. song.link searches for track across platforms
2. Returns Spotify URL if found
3. We query Spotify API for ISRC
4. Works for YouTube, SoundCloud, Apple Music, etc.

#### Tier 3: No ISRC

**When ISRC lookup fails:**

- No Spotify match found via song.link
- Track not on Spotify
- No ISRC in Spotify metadata (rare)

**Result:** Skip DAB Music, go directly to original source fallback

### 3. DAB Music Integration

#### Authentication

```python
# Check credentials
email = config.dabmusic_email
password = config.dabmusic_password

if not email or password:
    print("ℹ️ DAB Music credentials not configured, skipping")
    return False  # Fall back to original source

# Login
client = DABMusicClient(email, password)
# Session cookie stored automatically
```

#### Search by ISRC

```python
# Search DAB Music for track with this ISRC
track_data = client.search_by_isrc(isrc)

if not track_data:
    print("ℹ️ Track not found on DAB Music")
    return False  # Fall back to original source

# Verify ISRC matches (safety check)
if track_data['isrc'] != isrc:
    print("⚠️ ISRC mismatch")
    return False
```

**What we get:**

```python
{
    'id': 40398780,
    'title': 'Mr. Brightside',
    'artist': 'The Killers',
    'albumTitle': 'Hot Fuss',
    'albumCover': 'https://...',
    'releaseDate': '2004-09-27',
    'isrc': 'GBFFP0300052',
    'upc': '...',
    'label': '...'
}
```

#### Download FLAC

```python
# Generate output path
output_path = output_dir / f"{artist} - {title}.flac"

# Download FLAC (quality=27 for FLAC, quality=5 for MP3)
success = client.download_track(
    track_id=track_data['id'],
    output_path=output_path,
    quality=27  # FLAC
)
```

**Result:** FLAC file (~30-40MB)

- Format: FLAC lossless
- Bitrate: ~1411kbps (CD quality)
- Sample rate: 44.1kHz
- Bit depth: 16-bit

### 4. Metadata Application

**Applied to FLAC before conversion:**

```python
from mutagen.flac import FLAC, Picture

audio = FLAC(flac_path)

# Text metadata
audio['TITLE'] = 'Mr. Brightside'
audio['ARTIST'] = 'The Killers'
audio['ALBUM'] = 'Hot Fuss'
audio['DATE'] = '2004-09-27'
audio['ISRC'] = 'GBFFP0300052'
audio['BARCODE'] = '...'  # UPC/EAN
audio['LABEL'] = 'Island Records'

# Cover art
cover_data = download(track_data['albumCover'])
picture = Picture()
picture.type = 3  # Cover (front)
picture.data = cover_data
picture.mime = 'image/jpeg'
audio.add_picture(picture)

audio.save()
```

### 5. FLAC → M4A Conversion

#### Extract Cover Art

```python
# Read FLAC and extract cover art
flac_audio = FLAC(flac_path)
cover_data = flac_audio.pictures[0].data if flac_audio.pictures else None
```

#### Convert Audio

```python
# FFmpeg conversion
ffmpeg -i input.flac \
    -vn \                    # Skip video/cover art
    -c:a aac \               # AAC codec
    -b:a 256k \              # 256kbps bitrate
    -movflags +faststart \   # Optimize for streaming
    -map_metadata 0 \        # Copy metadata
    -y \                     # Overwrite
    output.m4a
```

**Settings:**

- Codec: AAC (Advanced Audio Coding)
- Bitrate: 256kbps constant
- Sample rate: 44.1kHz (preserved from FLAC)
- Channels: Stereo

**Result:** M4A file (~6-7MB)

- 80% smaller than FLAC
- Transparent quality
- Universal compatibility

#### Re-embed Cover Art

```python
# Embed cover art into M4A
from mutagen.mp4 import MP4, MP4Cover

m4a_audio = MP4(m4a_path)
m4a_audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
m4a_audio.save()
```

**Why re-embed:**

- FFmpeg's `-vn` flag skips cover art (prevents H.264 encoding issues)
- We extract cover art before conversion
- Re-embed after conversion using mutagen
- Ensures cover art is properly stored in M4A container

#### Cleanup

```python
# Delete FLAC file
flac_path.unlink()

# Keep only M4A
print(f"✅ Downloaded and converted to M4A: {m4a_path}")
```

### 6. Fallback to Original Source

**When DAB Music fails (no ISRC, not found, or error):**

```python
# Route to appropriate handler
if source_type == "spotify":
    handler = SpotifyDownloader(config, output_dir)
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

- Downloads directly from YouTube
- Format preference: 251 (Opus) → 140 (M4A)
- Converts to M4A if needed
- Output: M4A ~160kbps (251) or ~128kbps (140)

#### Spotify Fallback (via spotdl)

**Process:**

- Same as for YouTube

#### SoundCloud Fallback (via yt-dlp)

**Process:**

- Downloads from SoundCloud
- Free tier quality only
- Output: M4A ~128kbps

## File Naming & Organization

### Filename Format

Artist - Title.m4a

**Sanitization:**

- Unsafe characters replaced with `-`
- Characters: `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`
- Leading/trailing spaces and dots removed
- Maximum length: 255 characters

**Examples:**
Original: "Artist / Name: Song (Official)"
Sanitized: "Artist - Name- Song (Official).m4a"

Original: "Track: The Song"
Sanitized: "Track- The Song.m4a"

### All Files Saved to Same Directory

**No subfolders:**

- All tracks in single output directory
- Flat structure for DJ library
- Easy to browse and search
- Consistent with DJ software expectations

**Output Location:**

- Default: From config (`output_dir`)
- Override: `--output <path>` flag

## Duplicate Detection

### Detection Strategy

**Compares:**

- Artist name (normalized, case-insensitive)
- Track title (normalized, case-insensitive)

**Works across formats:**

- M4A vs MP3
- FLAC vs M4A
- Any combination

**Example:**
Existing: "The Killers - Mr. Brightside.m4a"
New download: "The Killers - Mr. Brightside.flac"
Result: Detected as duplicate ✓

### Handling Duplicates

When duplicate detected:

- System prompts for action
- Options: Skip, Keep both, Replace

**Remixes and versions NOT flagged:**

- "Song (Original Mix)" vs "Song (Remix)"
- Different titles = different tracks
- Correct behavior for DJ library

## Error Handling

### Failed Downloads

**Logged to:** `failed-downloads.txt`

**Format:**
2025-12-15 15:45 | https://... | Error message

**Process continues:**

- Individual failures don't stop batch downloads
- Each track processed independently
- Summary shows success/failed counts

### Common Errors

**1. No ISRC Found**

- Message: "ℹ️ No ISRC found, downloading from original source"
- Behavior: Falls back to Spotify/YouTube/SoundCloud
- Not a failure - system works as designed

**2. Track Not on DAB Music**

- Message: "ℹ️ Track not found on DAB Music"
- Behavior: Falls back to original source
- Common for new releases or obscure tracks

**3. DAB Music Credentials Not Configured**

- Message: "ℹ️ DAB Music credentials not configured, skipping"
- Behavior: Falls back to original source immediately
- Solution: Configure credentials in config.yaml

**4. FFmpeg Conversion Failed**

- Message: "⚠️ FFmpeg conversion failed"
- Behavior: Keeps FLAC file, doesn't delete
- Check: Ensure FFmpeg is installed

**5. Spotify API Credentials Missing**

- Message: ISRC lookup will fail for Spotify URLs
- Behavior: Falls back to original source
- Solution: Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET

### Retry Failed Downloads

```bash
# View failed downloads
cat ~/path/to/failed-downloads.txt

# Retry specific URL
track-manager download <url>
```

## Summary

### Complete Workflow

1. **Detect source** → Spotify, YouTube, SoundCloud, or direct
2. **Find ISRC** → Spotify API or song.link → Spotify
3. **Try DAB Music** → Search by ISRC, download FLAC
4. **Convert** → FLAC → M4A 256kbps with FFmpeg
5. **Apply metadata** → Title, artist, album, date, ISRC, cover art
6. **Cleanup** → Delete FLAC, keep M4A
7. **Fallback** → Original source if DAB unavailable
8. **Organize** → Clean filename, single directory
9. **Check duplicates** → Prevent redundant storage

### Quality Results

**With DAB Music:**

- Format: M4A 256kbps (from FLAC lossless)
- Size: ~6.3 MB per track
- Quality: Transparent (imperceptible from lossless)
- Coverage: ~80% of tracks (with ISRC)

**Without DAB Music:**

- Format: M4A 128-192kbps (source-dependent)
- Size: ~4-5 MB per track
- Quality: Good streaming quality
- Coverage: 100% (always works)

### Key Benefits

1. **Automatic Quality Upgrade** - ISRC-based FLAC sourcing
2. **Universal Compatibility** - M4A works everywhere
3. **Efficient Storage** - 80% smaller than FLAC
4. **Reliable Fallback** - Always gets the track
5. **Clean Organization** - Professional file naming
6. **No Manual Work** - Fully automatic process

### For Users

**What you provide:**

- URL from any supported platform
- DAB Music credentials (optional but recommended)

**What you get:**

- Best quality available (FLAC → M4A 256kbps when possible)
- Comprehensive metadata + cover art
- Clean filename: `Artist - Title.m4a`
- Automatic fallback if needed
- Duplicate detection
- Error logging for failed downloads

**Just works** - The system handles all complexity automatically.

## Special Case: Direct URLs

For direct audio URLs (e.g., `https://example.com/audio.mp3`), the system skips ISRC lookup and DAB Music search:

**Why?**

- User has already found the specific file they want
- Direct URLs rarely have ISRC metadata
- No point searching for alternative sources
- Faster download process

**Behavior:**

- Direct URLs → Download immediately as-is
- No ISRC lookup
- No DAB Music search
- No format conversion
- Original quality preserved exactly
