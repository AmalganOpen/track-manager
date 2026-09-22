# Track Manager

Universal music downloader with smart duplicate detection and metadata management.

## Features

- 🎯 **Universal Platform Support** - Works with ANY music platform (Spotify, Apple Music, YouTube, SoundCloud, Instagram, Deezer, Amazon Music, TIDAL, etc.)
- 🎵 **High-Quality Downloads** - Automatic FLAC from proxy
- 🔍 **Smart Duplicate Detection** - Works across formats (M4A vs MP3)
- 📝 **Metadata Management** - CSV-based review and correction workflow
- 🤝 **Interactive Prompts** - Asks what to do when duplicates found
- 📊 **Playlist Support** - YouTube, SoundCloud, and Spotify playlists (Spotify requires API credentials)
- 🔄 **Error Resilience** - Logs failed downloads, continues on errors
- 🎚️ **Best Quality** - Lossless FLAC (16-bit/44.1kHz) when available, converted to M4A 256kbps
- 🎛️ **DJ library tools** - In-place pitch tune, bar-boundary pad from the Rekordbox grid, tuning checks
- 🌍 **Cross-Platform** - Works on macOS, Linux, Windows

## How It Works

Track Manager uses a **smart download system** to get the best quality audio:

1. **Any URL** (Spotify, YouTube, etc.) → song.link API lookup
2. **Proxy API** → Downloads lossless FLAC (16-bit/44.1kHz)
   - ✅ No credentials required
   - ✅ Includes full metadata and cover art
3. **Automatic conversion** → M4A 256kbps AAC (preserves quality, better compatibility)
4. **Fallback** → If not on proxy, downloads from YouTube, SoundCloud, or Instagram

**Quality comparison:**

- FLAC: 1411 kbps lossless → converted to M4A 256 kbps
- YouTube: ~128 kbps M4A
- SoundCloud: ~128 kbps → M4A 256 kbps (for less lossy conversion)

**Legacy note:** DAB Music was previously used for high-quality downloads but is currently unavailable. The configuration is kept for potential future use if the service returns.

## Installation

### From Source (For Development)

```bash
# Navigate to the track-manager directory
cd track-manager

# Install the package
pip install -e .
# Optional: tuning analysis (tm check-tuning)
pip install -e ".[tune]"
# or
pip3 install -e .
```

## Setup

### FFmpeg (required)

Track Manager requires FFmpeg (the `ffmpeg` and `ffprobe` binaries) to be installed and available on your PATH. FFmpeg is used to probe audio, perform format conversions (e.g. FLAC → AIFF/M4A/MP3), and embed cover art. Without FFmpeg, lossless downloads and post-download processing will fail with an error such as "ffmpeg not found on PATH".

Install:

Check out the [FFmpeg installation guide](https://www.ffmpeg.org/download.html) for your platform. Alternatively, use your platform's package manager (e.g. Homebrew, apt, dnf) to install FFmpeg.

- macOS (Homebrew):

  ```bash
  brew install ffmpeg
  ```

- Debian/Ubuntu:

  ```bash
  sudo apt-get update && sudo apt-get install -y ffmpeg
  ```

- Windows:

  Download a build from https://ffmpeg.org/download.html and add `ffmpeg.exe` to your PATH, or use Chocolatey:

  ```powershell
  choco install ffmpeg
  ```

Restart terminal && verify the installation:

```bash
ffmpeg -version
ffprobe -version
```

### Basic Setup

**Individual tracks** work for ANY platform (Apple Music, YouTube, SoundCloud, Instagram, Deezer, Amazon Music, TIDAL, etc.):

- ✅ Converted via song.link → Proxy for high-quality FLAC
- **Instagram** is fetched directly (not on song.link). A logged-in browser
  session is usually required — see Instagram setup below.
- song.link auth is optional (higher rate limits). Email `developers@song.link`
  for a key, then set `songlink.api_key` or `SONGLINK_API_KEY`. It is sent as
  the `key` query param. If the live API returns 401, remaining lookups this
  run are skipped and downloads fall back to YouTube / SoundCloud.

**Playlists** only work for:

- ✅ **YouTube playlists** - No setup needed
- ✅ **SoundCloud playlists** - No setup needed
- ✅ **Instagram carousels** - Multi-video posts; profile URLs are not supported
- ⚠️ **Spotify playlists** - Requires API credentials (see below)

### Spotify API Setup (Optional - Only for Playlists)

**Spotify API credentials are optional:**

- ✅ **Individual Spotify track URLs work without credentials**
- ⚠️ **Playlist/album URLs require Spotify API** to enumerate tracks

**To enable Spotify playlist support:**

1. Copy `config.example.yaml` to `config.yaml`

2. Get credentials from: https://developer.spotify.com/dashboard
   (Create an app → Copy Client ID & Secret)

3. add to `config.yaml`:
   ```yaml
   spotdl:
     client_id: "your_client_id"
     client_secret: "your_client_secret"
   ```

### Instagram Setup (Optional — usually required)

Instagram reels and most posts need a logged-in session. Public posts
sometimes work without cookies until Instagram's anonymous rate limit hits.

1. Log in to Instagram in Chrome, Firefox, Safari, Brave, or Edge.
2. Add to `config.yaml`:

   ```yaml
   instagram:
     cookies_from_browser: "firefox"   # or chrome, safari, brave, edge
     # cookies_file: ""                # Netscape cookies.txt instead
   ```

If `instagram.cookies_from_browser` is empty, `youtube.cookies_from_browser`
is reused (browser cookies include Instagram when you are logged in there).
Profile URLs are not supported — pass a `/reel/`, `/p/`, `/tv/`, or `/share/`
link.

### Setup (Optional - Lossless Fallback)

When public lossless proxies (Qobuz / TIDAL) are unavailable, Track Manager can
fall back to [Soulseek](https://www.slsknet.org/) via the
[sockseek](https://github.com/fiso64/sockseek) CLI (formerly `sldl`) before
YouTube/spotdl.

1. Install `sockseek` and put it on your `PATH`
   (download a binary from [releases](https://github.com/fiso64/sockseek/releases);
   no need to clone).
2. Pick a unique Soulseek username and password. There is no signup page —
   the first successful login creates the account. If the name is taken, login
   fails and you should choose another. Add them to `config.yaml`:

   ```yaml
   soulseek:
     username: "your-soulseek-username"
     password: "your-soulseek-password"
     # Optional:
     # binary: ""                # default: find sockseek or sldl on PATH
     # timeout_seconds: 180
     # length_tol_seconds: 3
   ```

Soulseek is enabled only when both username and password are set. Matching uses
artist + title + duration (ISRC is kept for provenance only).

**Etiquette:** sockseek does not share files back to the network. If you already
run Nicotine+ or [slskd](https://github.com/slskd/slskd), use a _separate_
Soulseek account for Track Manager to avoid connection conflicts, and keep
sharing from your regular client.

## Configuration

Track Manager uses a config file at `config.yaml` in the project root.

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
```

or just

```bash
tm "https://open.spotify.com/track/..."
```

### Manage Your Library

```bash
# Check for duplicate tracks
track-manager check-duplicates

# Verify installation and setup
track-manager check-setup

# Pitch-tune a track in place (pitch only; BPM stays the same)
tm tune "midnight" 2.5          # +2.5% pitch interval — same file, tags updated
tm tune "midnight" -3           # -3% (inverse of +3%)
tm tune "midnight" 50 -c        # +50 cents
tm tune ~/Music/track.aiff 2 -a # absolute filesystem path

# Pad to bar boundaries using the Rekordbox beat grid (Rekordbox must be quit)
tm pad "midnight"               # pad start/end onto the "1" when past the "3"
tm pad "midnight" -n            # dry-run: show the plan only
tm pad "drop" --no-start        # end pad only
tm pad "outro" --end-tail silence   # dry silence instead of quiet reverb wash
tm pad "midnight" --undo        # reverse recorded pads
# Prefer AIFF — padding M4A/MP3 re-encodes the whole file

# Estimate tuning vs A440
tm check-tuning "stayed together"
tm check-tuning ~/Music/track.aiff -a
# Key/chroma from one slice only (default averages several windows):
tm check-tuning "drop" --offset 90 --duration 20 --key-scope window --key-source estimated

# Get help
track-manager --help
tm pad --help
```

## Audio Quality

Track Manager always downloads at the **best available quality** - no configuration needed.
Some download sources like spotdl will encode at higher bit rate that source in order to prevent loss.
In order for you to keep track of the real quality of your tracks, Track Manager add the true bit rate to the metadata.

```bash
# list quality of all tracks grouped into high, medium and low
tm check-quality
```

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

Failed downloads are logged to `failed-downloads.txt` with timestamps and error messages.

```bash
# List unique failed URLs (newest first)
tm retry-failed --list

# Retry everything in the log
tm retry-failed

# Preview without downloading
tm retry-failed --dry-run

# Clear the log
tm retry-failed --clear
```

## Shell Completion

```bash
tm completions zsh
```

This writes an untracked script to `completions/` in the repo (gitignored)
and adds a small marked block to `~/.zshrc` that sources it. Re-run after
pulling CLI changes. Use `--print` to dump the script to stdout instead.

Supported shells: `bash`, `zsh`, `fish`.

## Troubleshooting

### Spotify Downloads

**Problem:** "Error: No Spotify credentials found"

**Solution:** Spotify playlist downloads require API credentials. See the [Spotify Setup](#spotify-setup-optional) section above for detailed instructions.

Get credentials from: https://developer.spotify.com/dashboard

### Low Quality Downloads

**Problem:** Old tracks downloaded at 128 kbps or lower

**Solution:** The quality fix was implemented in version 0.2.0. If you have old low-quality tracks:

1. Check library quality: Look for tracks < 128 kbps using your audio player's metadata view
2. Re-download those tracks - they'll now download at best quality
3. Remove old low-quality versions

### YouTube Download Issues

**Problem:** `HTTP Error 403: Forbidden` or "unable to download video data"

YouTube breaks extractors often. This is almost always an outdated yt-dlp, stale cookies, or a missing JS runtime — not a private video.

**Solution:**

1. Update yt-dlp: `pip install -U 'yt-dlp[default]'` (needs 2026.8.19+)
2. Install Deno if you don't have it: `brew install deno` (solves YouTube JS challenges)
3. If `youtube.cookies_from_browser` is set, re-login to YouTube in that browser, or clear the setting unless the video is age-restricted. Stale cookies make 403s _more_ likely.
4. Leave `youtube.player_clients` empty so yt-dlp can pick current defaults
5. Run `tm check-setup` to confirm versions

**Problem:** "Error: Unable to extract video info"

**Possible causes:**

- Video is private or removed
- Video is age-restricted (needs fresh cookies)
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

### Instagram Issues

**Problem:** "login required" / empty media / redirected to the login page

**Solution:** Instagram blocks most anonymous downloads. Set
`instagram.cookies_from_browser` (or `instagram.cookies_file`) and log in to
Instagram in that browser. Re-login if cookies go stale.

**Problem:** Profile URL is rejected

**Solution:** Pass a reel or post URL (`/reel/SHORTCODE` or `/p/SHORTCODE`),
not `instagram.com/username`.

**Problem:** Artist/title tags look like a caption

**Solution:** Instagram has no ISRC or track metadata. Downloads are flagged
for `tracks-metadata-review.csv`. Edit and run `track-manager apply-metadata`.

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

### Pad / Rekordbox

**Problem:** `tm pad` refuses to run or DB writes fail

**Solution:** Quit Rekordbox completely (including background agents if needed), then retry. Pad shifts ANLZ/cue times for start pads — do **not** re-analyse the track afterward or loops and cues will drift.

**Problem:** End pad sounds empty / too wet

**Solution:** Default end fill is a quiet pad-only reverb wash (`--end-tail reverb`). Use `--end-tail silence` for dry silence. The original body is never faded.

**Problem:** Worried about lossy re-encode

**Solution:** Prefer AIFF (`tm migrate-to-aiff`). Padding M4A/MP3 re-encodes the whole file. Use `tm pad … -n` to preview without writing.

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

See **[docs/development.md](docs/development.md)** for setup, testing, code style, CI, and architecture notes.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! See **[docs/development.md](docs/development.md)** for setup, testing, and PR workflow.
