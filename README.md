# Track Manager

Universal music downloader with smart duplicate detection and metadata management.

Download tracks from **Spotify, YouTube, SoundCloud, or direct URLs** with automatic source detection, duplicate prevention, and metadata handling.

## Features

- 🎯 **Universal Source Support** - Spotify, YouTube, SoundCloud, direct URLs
- 🎵 **High-Quality Downloads** - Automatic FLAC from TIDAL (no credentials required)
- 🔍 **Smart Duplicate Detection** - Works across formats (M4A vs MP3)
- 📝 **Metadata Management** - CSV-based review and correction workflow
- 🤝 **Interactive Prompts** - Asks what to do when duplicates found
- 📊 **Playlist Support** - Handles playlists/albums with progress tracking
- 🔄 **Error Resilience** - Logs failed downloads, continues on errors
- 🎚️ **Best Quality** - Lossless FLAC (16-bit/44.1kHz) when available, converted to M4A 256kbps
- 🌍 **Cross-Platform** - Works on macOS, Linux, Windows

## How It Works

Track Manager uses a **smart download system** to get the best quality audio:

1. **Any URL** (Spotify, YouTube, etc.) → song.link lookup
2. **TIDAL Public API** → Downloads lossless FLAC (16-bit/44.1kHz)
   - ✅ No credentials required
   - ✅ Works with community-hosted endpoints
   - ✅ Includes full metadata and cover art
3. **Automatic conversion** → M4A 256kbps AAC (preserves quality, better compatibility)
4. **Fallback** → If not on TIDAL, downloads from original source

**Quality comparison:**
- TIDAL FLAC: 1411 kbps lossless → converted to M4A 256 kbps
- YouTube: ~130 kbps → M4A 128 kbps (matches source quality)
- SoundCloud: ~128 kbps → M4A 256 kbps

**Legacy note:** DAB Music was previously used for high-quality downloads but is currently unavailable. The configuration is kept for potential future use if the service returns.

## Installation

### From Source (For Development)

```bash
# Navigate to the track-manager directory
cd track-manager

# Install the package
pip install -e .
# or
pip3 install -e .
```

## Setup

### Basic Setup (No Credentials Required)

Track Manager works immediately for most sources:

- ✅ **YouTube** - No setup needed
- ✅ **SoundCloud** - No setup needed  
- ✅ **Direct URLs** - No setup needed
- ✅ **Spotify tracks** - No setup needed (downloads via TIDAL)
- ⚠️ **Spotify playlists/albums** - Requires API credentials (see below)

### Spotify API Setup (Optional - Only for Playlists)

**Spotify API credentials are optional:**
- ✅ **Individual Spotify track URLs work without credentials** (downloaded via TIDAL)
- ⚠️ **Playlist/album URLs require Spotify API** to enumerate tracks

**To enable Spotify playlist support:**

1. Get credentials from: https://developer.spotify.com/dashboard
   (Create an app → Copy Client ID & Secret)

2. Set environment variables:
   ```bash
   export SPOTIPY_CLIENT_ID="your_client_id"
   export SPOTIPY_CLIENT_SECRET="your_client_secret"
   ```

3. Or add to `config.yaml`:
   ```yaml
   spotdl:
     client_id: "your_client_id"
     client_secret: "your_client_secret"
   ```

## Configuration

Track Manager uses a config file at `config.yaml` in the project root. Copy `config.example.yaml` to `config.yaml` and customize it.

You can customize:

- Output directory
- Download format preferences (M4A, MP3)
- Duplicate handling behavior
- Spotify credentials (optional)
- And more...

See `config.example.yaml` for all available options.

## Quick Start

### Download Tracks

```bash
# Download from Spotify
track-manager download "https://open.spotify.com/track/..."
tm "https://open.spotify.com/track/..."

# Download from YouTube
track-manager download "https://www.youtube.com/watch?v=..."
tm "https://www.youtube.com/watch?v=..."

# Download from SoundCloud
track-manager download "https://soundcloud.com/artist/track"
tm "https://soundcloud.com/artist/track"

# Download from direct URL
track-manager download "https://example.com/audio.mp3"
tm "https://example.com/audio.mp3"
```

### Manage Your Library

```bash
# Check for duplicate tracks
track-manager check-duplicates

# Verify installation and setup
track-manager check-setup

# Get help
track-manager --help
```

## Supported Sources

### Spotify

- Tracks: `https://open.spotify.com/track/...`
- Playlists: `https://open.spotify.com/playlist/...`
- Albums: `https://open.spotify.com/album/...`

### YouTube

- Videos: `https://www.youtube.com/watch?v=...`
- Playlists: `https://www.youtube.com/playlist?list=...`

### SoundCloud

- Tracks: `https://soundcloud.com/artist/track`
- Sets: `https://soundcloud.com/artist/sets/playlist`

### Direct Audio URLs

- Any direct audio file URL: `https://example.com/audio.mp3`

## Audio Quality

Track Manager always downloads at the **best available quality** - no configuration needed.

### Quality by Source

| Source          | Format   | Bitrate               | Notes                              |
| --------------- | -------- | --------------------- | ---------------------------------- |
| **Spotify**     | MP3/M4A  | 320 kbps / 256 kbps   | Highest quality available          |
| **YouTube**     | Opus/M4A | ~160 kbps / ~128 kbps | Best audio stream (format 251/140) |
| **SoundCloud**  | Various  | Best available        | Uses same settings as YouTube      |
| **Direct URLs** | Original | Preserved             | No re-encoding                     |

### Technical Details

- **Spotify**: Uses spotdl with `bitrate: "320k"` setting
  - MP3: 320 kbps constant bitrate
  - M4A/AAC: 256 kbps (comparable quality to 320 kbps MP3)

- **YouTube**: Explicitly requests high-quality audio formats
  - Format 251: Opus codec at ~160 kbps (preferred)
  - Format 140: M4A/AAC at ~128 kbps (fallback)
  - Avoids low-quality formats (249: ~50 kbps)

- **SoundCloud**: Inherits YouTube's format selection
  - Best available quality from source

### Why These Quality Levels?

- **320 kbps MP3** is considered "transparent" (indistinguishable from source)
- **256 kbps AAC/M4A** is roughly equivalent to 320 kbps MP3 due to better compression
- **160 kbps Opus** is highly efficient, comparable to 256 kbps MP3
- Higher bitrates waste storage without audible quality improvement

### Quality vs File Size

Approximate file sizes for a 4-minute track:

- 320 kbps MP3: ~10 MB
- 256 kbps M4A: ~8 MB
- 160 kbps Opus: ~5 MB
- 128 kbps M4A: ~4 MB

## Duplicate Detection

Track Manager intelligently detects duplicates by:

- Comparing artist + title from ID3/M4A tags (not filenames)
- Normalizing metadata (removes "[Official Video]", handles "feat." variations)
- Working across formats (finds M4A duplicates of MP3 files)
- Case-insensitive matching

When a duplicate is found, you'll be prompted to:

- Skip new file (keep existing)
- Keep both files
- Replace existing with new file

## Metadata Management

When metadata is missing or problematic, tracks are flagged for manual review:

1. Download script flags tracks with issues
2. Edit `tracks-metadata-review.csv` (in project directory) to fill in correct metadata
3. Run `track-manager apply-metadata` to update files

## Error Handling

Failed downloads are logged to `failed-downloads.txt` with timestamps and error messages. You can retry failed URLs later.

## Troubleshooting

### Spotify Downloads

**Problem:** "Error: No Spotify credentials found"

**Solution:** Spotify downloads require API credentials. See the [Spotify Setup](#spotify-setup-optional) section above for detailed instructions.

Get credentials from: https://developer.spotify.com/dashboard

### Low Quality Downloads

**Problem:** Old tracks downloaded at 128 kbps or lower

**Solution:** The quality fix was implemented in version 0.2.0. If you have old low-quality tracks:

1. Check library quality: Look for tracks < 128 kbps using your audio player's metadata view
2. Re-download those tracks - they'll now download at best quality
3. Remove old low-quality versions

**Note:** Quality is now always best by default. You don't need to configure anything.

### YouTube Download Issues

**Problem:** "Error: Unable to extract video info"

**Possible causes:**

- Video is private or removed
- Video is age-restricted
- Geo-restricted content
- YouTube rate limiting

**Solution:**

- Verify the URL is correct and accessible in a browser
- Wait a few minutes and retry (rate limiting)
- Check `failed-downloads.txt` for specific error messages

### SoundCloud Issues

**Problem:** Downloads fail or get low quality

**Solution:**

- SoundCloud requires the track to be publicly accessible
- Private tracks or sets cannot be downloaded
- Some tracks may have download disabled by the artist

### Metadata Issues

**Problem:** Tracks have incorrect or missing metadata

**Solution:**

1. Check `tracks-metadata-review.csv` in the track-manager directory
2. Fill in correct artist and title for flagged tracks
3. Run: `track-manager apply-metadata`

### Duplicate Detection

**Problem:** Duplicate detection not working

**Possible causes:**

- Files have no metadata (artist/title tags missing)
- Metadata is very different between files

**Solution:**

- Ensure files have proper ID3/M4A tags
- Use `track-manager apply-metadata` to fix metadata first
- Duplicate detection compares artist + title from tags, not filenames

### Installation Issues

**Problem:** "Command not found: track-manager"

**Solution:**

```bash
# Ensure installation directory is in PATH
pip show track-manager  # Check installation location

# Or use as module
python -m track_manager download <url>
```

**Problem:** Missing dependencies

**Solution:**

```bash
# Run setup check
track-manager check-setup

# Install missing dependencies
pip install track-manager[dev]  # Includes all optional deps
```

### Getting Help

If you encounter other issues:

1. Check `failed-downloads.txt` for error details
2. Run `track-manager check-setup` to verify installation
3. Check the documentation for known issues
4. Open a new issue with:
   - Command you ran
   - Full error message
   - Output of `track-manager check-setup`

## Development

For development, install with dev dependencies:

```bash
cd track-manager
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=track_manager
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
