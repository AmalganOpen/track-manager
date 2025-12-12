# Track Download Process

This document provides a detailed explanation of how track downloading works in track-manager, from command execution to final file placement.

## Overview

The track-manager download system is designed to:

- Download audio from multiple sources (Spotify, YouTube, SoundCloud, direct URLs)
- Preserve audio quality without unnecessary upscaling
- Extract and clean metadata
- Detect and handle duplicates
- Flag problematic files for manual review

## Command Flow

### Basic Usage

```bash
track-manager download <url> [--format {auto,m4a,mp3}]
```

### Process Steps

1. **Command Entry** → CLI (`cli.py`)
2. **Source Detection** → Downloader (`downloader.py`)
3. **Handler Routing** → Source-specific handler
4. **Download** → yt-dlp/spotdl/requests
5. **Post-processing** → FFmpeg conversion (if needed)
6. **Quality Verification** → Check bitrate matches expected
7. **Metadata Extraction** → Read ID3/M4A tags
8. **Metadata Cleaning** → Remove junk patterns
9. **Duplicate Detection** → Compare with existing library
10. **File Organization** → Rename and move to final location

## Source Detection

The system automatically detects the source type based on the URL domain:

```python
def detect_source(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if "spotify.com" in domain:
        return "spotify"
    elif "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"
    elif "soundcloud.com" in domain:
        return "soundcloud"
    else:
        return "direct"  # Assume direct audio file
```

## Format Selection

### Output Format: M4A vs MP3

The output format is controlled by the `--format` flag:

```bash
# Default: M4A
track-manager download <url>

# Explicit M4A
track-manager download <url> --format m4a

# MP3
track-manager download <url> --format mp3
```

**Default is M4A because:**
- Smaller file size at same quality
- YouTube's format 140 is native M4A (no conversion)
- Modern format with better compression
- Supported by all modern devices and software

**Choose MP3 if:**
- Compatibility with older devices required
- Specific DJ software requires MP3
- Personal preference

### YouTube Format Preference Order

When downloading from YouTube, yt-dlp tries formats in this order:

```python
"format": "251/140/bestaudio/best"
```

**1. Format 251 (Preferred)**
- Codec: Opus
- Bitrate: ~160kbps VBR
- Quality: Best available
- Requires: Transcoding Opus → AAC (M4A) or MP3
- Why preferred: Highest quality (~25% better than 140)

**2. Format 140 (Fallback)**
- Codec: AAC (M4A)
- Bitrate: ~128kbps
- Quality: Standard
- Requires: No conversion for M4A (extract only)
- When used: Format 251 not available (rare)

**3. bestaudio (Fallback)**
- Selects best available audio format
- May be format 250 (Opus 70kbps), 249 (Opus 50kbps), or others
- Rarely needed (251/140 almost always available)

**4. best (Last Resort)**
- Best quality overall (may include video)
- Audio extracted from video+audio stream
- Rarely used in practice

### What Determines Final Format?

**Flowchart:**

YouTube Download
     ↓
User specifies format? --format {auto|m4a|mp3}
     ↓
┌────────────────┬─────────────┬──────────────┐
│   auto/m4a     │     mp3     │              │
└────────────────┴─────────────┴──────────────┘
     ↓                  ↓
Try format 251    Try format 251
(Opus ~160kbps)   (Opus ~160kbps)
     ↓                  ↓
Available?        Available?
     │                  │
     ├─Yes──→ Convert   ├─Yes──→ Convert
     │        Opus→M4A   │        Opus→MP3
     │        VBR ~160   │        VBR ~160
     │                   │
     ├─No───→ Try 140    ├─No───→ Try 140
     │        (M4A 128)  │        (M4A 128)
     │                   │
     ├─Yes──→ Extract    ├─Yes──→ Convert
     │        as M4A     │        M4A→MP3
     │        ~128kbps   │        ~128kbps
     │                   │
     ├─No───→ Try        ├─No───→ Try
     │        bestaudio  │        bestaudio
     │                   │
     └────→ Final M4A    └────→ Final MP3

### Quality Implications

**M4A Output:**
- Format 251 → M4A: ~160kbps VBR (best quality)
- Format 140 → M4A: ~128kbps (extract, no loss)

**MP3 Output:**
- Format 251 → MP3: ~160kbps VBR (transcode from Opus)
- Format 140 → MP3: ~128kbps (transcode from M4A)
- Note: MP3 files are larger than M4A at same quality

**Transcoding Quality Loss:**
- Opus → AAC (M4A): Minimal loss (both modern codecs)
- Opus → MP3: Slightly more loss (MP3 is older codec)
- M4A → MP3: Some loss (lossy → lossy conversion)

**Recommendation:**
- Use M4A (default) unless you specifically need MP3
- M4A preserves quality better and creates smaller files

### Examples

**Best quality (default):**
```bash
track-manager download "https://youtube.com/watch?v=..."
# Result: M4A ~160kbps (if format 251 available)
```

**Compatibility mode:**
```bash
track-manager download "https://youtube.com/watch?v=..." --format mp3
# Result: MP3 ~160kbps (if format 251 available)
```

**Rare case (format 251 unavailable):**
```bash
track-manager download "https://youtube.com/watch?v=old_video"
# Result: M4A ~128kbps (falls back to format 140)
```
## Handler-Specific Processing

### 1. YouTube Handler (`sources/youtube.py`)

**Audio Quality Strategy:**

- **Source Quality:** YouTube's maximum audio quality is ~130kbps (M4A or Opus)
- **Target Format:** M4A (auto) or MP3 (explicit)
- **Target Bitrate:** 128kbps

**Quality Preservation Strategy:**

YouTube offers different audio formats with varying bitrates:
- Format 140 (M4A): ~128kbps - Extracted without re-encoding (lossless)
- Format 251 (Opus): ~160kbps - Converted to M4A using VBR at source quality

We use `preferredquality: "0"` which tells FFmpeg to use VBR matching source quality:
- Preserves format 140 at ~128kbps (extraction, no loss)
- Preserves format 251 at ~160kbps (conversion with minimal loss)
- No unnecessary upsampling or downsampling
- Files reflect actual source quality

**Format Selection:**

```python
# yt-dlp format preference
"format": "251/140/bestaudio/best"

# 251: Opus ~160kbps (best quality, requires conversion to M4A)
# 140: M4A ~128kbps (native, no conversion needed)
```

**Why prefer 251 over 140?**
- Format 251 offers ~160kbps (higher quality than 140's ~128kbps)
- With VBR quality matching, we preserve this higher quality
- Worth the transcoding cost for better audio quality
- Falls back to 140 if 251 unavailable

**Post-processing:**

```python
"postprocessors": [{
    "key": "FFmpegExtractAudio",
    "preferredcodec": "m4a",  # or "mp3"
    "preferredquality": "0"  # Use VBR matching source quality
}]
```

**Processing Flow:**

1. Check if URL is playlist (ask for confirmation if >5 videos)
2. Download with yt-dlp using format preference
3. FFmpeg extracts/converts audio:
   - If format 140 (M4A ~128kbps): Extract without re-encoding (lossless)
   - If format 251 (Opus ~160kbps): Convert to M4A using VBR (~160kbps)
   - If MP3 requested: Convert to MP3 using VBR
4. Extract metadata from file
5. Check for duplicates in library
6. Flag for review if metadata is missing/dirty
7. Rename file to `Artist - Title.ext`
8. Move to final location

**Playlist Handling:**

- Processes each video individually
- Shows progress: `[idx/total] Processing: Title`
- Continues on error (logs to `failed-downloads.txt`)
- Shows summary at end

### 2. Spotify Handler (`sources/spotify.py`)

**Audio Quality Strategy:**

- **Underlying Source:** YouTube (via spotdl)
- **Source Quality:** ~128-160kbps (YouTube formats 140/251)
- **Target Bitrate:** VBR matching source

**Quality Preservation:**
spotdl downloads audio from YouTube (using Spotify metadata):

- Spotify provides metadata (artist, title, album, cover art)
- YouTube provides the actual audio stream (~128-160kbps)
- VBR setting preserves source quality (consistent with YouTube handler)
- Can get ~160kbps when YouTube format 251 available

**Configuration:**

```python
downloader_settings = DownloaderOptions()
downloader_settings["output"] = str(output_dir)
downloader_settings["bitrate"] = "0"  # VBR matching source quality
```

**Processing Flow:**

1. Initialize spotdl with Spotify API credentials
2. Extract song(s) from Spotify URL
3. For each song:
   - **Early duplicate check** (before downloading!)
   - Skip if duplicate found
   - Download using spotdl (searches YouTube, downloads audio)
   - Find downloaded file (by video ID and title)
   - Extract metadata from file
   - Use Spotify metadata as fallback if file metadata is poor
   - Check for duplicates again (late check for safety)
   - Flag for review if metadata is problematic
   - Rename to `Artist - Title.ext`
   - Move to final location
4. Show summary (success/failed counts)

**Key Difference from YouTube:**

- Pre-download duplicate checking (saves bandwidth)
- Spotify metadata as authoritative fallback
- Better metadata quality overall

### 3. SoundCloud Handler (`sources/soundcloud.py`)

**Audio Quality Strategy:**

- **Source Quality:** ~128kbps (SoundCloud free tier)
- **Target Bitrate:** 128kbps

**Why 128kbps?**
Without SoundCloud Go+ credentials, yt-dlp can only access free tier quality:

- Free tier: ~128kbps (most tracks)
- Go+: 256kbps (requires paid subscription + credentials)
- We target 128kbps to match actual free tier quality
- No unnecessary upsampling

**Note:** SoundCloud Go+ offers 256kbps streams, but requires:
- Paid subscription ($5-10/month)
- Authentication credentials configured in yt-dlp
- Currently not implemented (would need auth token support)

**Processing:**

```python
"postprocessors": [{
    "key": "FFmpegExtractAudio",
    "preferredcodec": "m4a",
    "preferredquality": "128"  # Match SoundCloud free tier quality
}]
```

**Inheritance:**

- Inherits from `YouTubeDownloader`
- Only overrides bitrate setting
- Otherwise same processing flow

### 4. Direct URL Handler (`sources/direct.py`)

**Processing:**

1. Download file using Python `requests`
2. Preserve original format (no conversion)
3. Extract metadata if possible
4. Check for duplicates
5. Rename and organize

**No Quality Modification:**

- Downloads file as-is
- No post-processing
- No format conversion
- Preserves original bitrate

## Post-Processing & Format Handling

### FFmpeg Conversion

When conversion is needed (e.g., Opus to M4A):

```python
"preferredcodec": "m4a",
"preferredquality": "128"
```

**Key Points:**

- `preferredquality` is a **target**, not a minimum
- FFmpeg won't upsample beyond source quality
- 128kbps setting means "encode at 128kbps when converting"
- If source is already M4A (format 140), no re-encoding occurs

### Format Preservation

**M4A (default):**

- YouTube format 140 is native M4A (~130kbps)
- No conversion needed
- Preserves original quality exactly
- Smaller file size than MP3 at same quality

**MP3 (explicit):**

- Always requires conversion
- Uses 128kbps encoding
- Larger files for same quality
- Better compatibility with older devices

## Metadata Extraction & Cleaning

### Extraction Process

```python
def extract_metadata(file_path: Path) -> Tuple[Optional[str], Optional[str]]:
    audio = MutagenFile(str(file_path), easy=True)
    artist = audio.get("artist", [None])[0]
    title = audio.get("title", [None])[0]
    return artist, title
```

**Supported Formats:**

- MP3: ID3 tags
- M4A: MP4/M4A tags
- FLAC: Vorbis comments

### Junk Pattern Detection

Common junk patterns removed from metadata:

```python
junk_patterns = [
    r"\[official.*?\]",      # [Official Video]
    r"\(official.*?\)",      # (Official Audio)
    r"\[.*?video.*?\]",      # [Music Video]
    r"\(.*?video.*?\)",      # (Lyric Video)
    r"\[.*?audio.*?\]",      # [HD Audio]
    r"\(.*?audio.*?\)",      # (HQ Audio)
    r"\[hd\]",               # [HD]
    r"\(hd\)",               # (HD)
    r"official video",       # Plain text variants
    r"official audio",
    r"music video",
]
```

**Detection:**

- Case-insensitive matching
- Checks both artist and title fields
- Flags files with junk for review

### Metadata Review Workflow

Files are flagged for manual review when:

- Artist or title is missing
- Junk patterns detected
- Conflicting information from source

**Review Process:**

1. Download script creates/updates `tracks-metadata-review.csv`
2. CSV columns: `file_path`, `current_artist`, `current_title`, `suggested_artist`, `suggested_title`, `source_url`, `notes`
3. User edits CSV to fill in correct metadata
4. Run `track-manager apply-metadata` to apply changes
5. Successfully processed rows are removed from CSV
6. Incomplete rows remain for continued review

## Duplicate Detection

### Detection Strategy

Duplicates are detected by comparing:

- **Artist** (normalized)
- **Title** (normalized)

**Normalization:**

```python
# Case-insensitive
artist.lower() == existing_artist.lower()

# Removes junk patterns
normalize(title) == normalize(existing_title)

# Handles variations
"feat." → "featuring"
"[Official Video]" → removed
```

**Cross-format Detection:**

- Compares metadata, not filenames
- Works across formats (M4A vs MP3)
- Example: `song.m4a` vs `song.mp3` detected as duplicate

### Duplicate Handling

When duplicate found:
**Interactive Prompt:**
Duplicate found: Artist - Title.ext

1. Skip new file (keep existing)
2. Keep both files
3. Replace existing with new file

Choice [1-3]:

**Spotify Pre-download Check:**

- Checks for duplicates BEFORE downloading
- Saves bandwidth and time
- Skips download if duplicate exists

**Post-download Safety Check:**

- Double-checks after download
- Handles edge cases (concurrent downloads, etc.)

### Remixes and Versions

Different versions are NOT flagged as duplicates:

- "Song (Original Mix)" vs "Song (Club Mix)"
- "Song" vs "Song (Remix)"
- Different titles = different tracks (correct for DJ library)

## Quality Verification

### Bitrate Analysis

After download, the system can verify audio quality:

```bash
track-manager check-quality [--detailed]
```

**Checks:**

- Actual bitrate of downloaded files
- Warns about files below acceptable thresholds
- Groups by format (MP3, M4A, FLAC)
- Shows quality distribution

**Quality Categories:**

- **Low:** < 128 kbps (should be rare)
- **Medium:** 128-256 kbps (YouTube, Spotify)
- **High:** ≥ 256 kbps (SoundCloud Go+, FLAC)

### Expected Bitrates by Source

| Source                | Expected Bitrate     | Format  | Notes                            |
| --------------------- | -------------------- | ------- | -------------------------------- |
| YouTube               | ~128-160 kbps (VBR)  | M4A/MP3 | Format 140: ~128kbps, Format 251: ~160kbps |
| Spotify (via YouTube) | ~128-160 kbps (VBR)  | M4A/MP3 | YouTube source with VBR          |
| SoundCloud            | ~128 kbps            | M4A/MP3 | Free tier (Go+ not implemented)  |
| Direct URL            | Varies               | As-is   | No modification                  |

### Quality Verification Example

```bash
$ track-manager check-quality

🔍 Analyzing audio quality in /path/to/tracks...

📊 Summary (150 files)

============================================================
M4A - 120 files
============================================================
  Bitrate:
    Average: 145 kbps
    Range: 128 kbps - 160 kbps
  Sample Rate: 44.1 kHz
  Quality Distribution:
    Medium (128-256 kbps): 120 files

============================================================
MP3 - 30 files
============================================================
  Bitrate:
    Average: 256 kbps
    Range: 128 kbps - 320 kbps
  Sample Rate: 44.1 kHz, 48.0 kHz
  Quality Distribution:
    Medium (128-256 kbps): 20 files
    High (≥256 kbps): 10 files
```

## File Organization

### Filename Format

Artist - Title.ext

**Sanitization:**

- Unsafe characters replaced with `-`
- Characters: `/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`
- Leading/trailing whitespace and dots removed

**Examples:**
"Artist / Name" → "Artist - Name.m4a"
"Song: The Title" → "Song- The Title.m4a"
"Track (Official Video)" → "Track (Official Video).m4a" # Junk in metadata, flagged

### Temporary Files

During download, files use temporary names:
.tmp\_{video_id}.{ext}

**Purpose:**

- Prevent conflicts with existing files
- Allow processing before final placement
- Enable atomic operations (process fully, then rename)

**Cleanup:**

- Automatically deleted on duplicate skip
- Automatically renamed on success
- Left behind on error (for debugging)

### Final Placement

After all processing completes:

1. Metadata extracted and cleaned
2. Duplicate check passed (or user chose to keep)
3. Filename created: `Artist - Title.ext`
4. File moved from `.tmp_*` to final name
5. Success message printed

**Output:**
✓ Saved: Artist - Title.m4a

## Complete Flow Diagrams

### YouTube Download Flow

┌─────────────────────────────────────────────────────────────┐
│ track-manager download "https://youtube.com/watch?v=..." │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Detect Source: YouTube │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Initialize YouTubeDownloader │
│ - Configure yt-dlp options │
│ - Set format: 140/251/bestaudio │
│ - Set postprocessor: FFmpeg extract audio │
│ - Set target: M4A @ 128kbps │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Check if Playlist │
│ - Extract info (flat, no download) │
│ - Count videos │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Playlist (>5) Single/Small
│ │
▼ │
┌───────────────────────┐ │
│ Ask for confirmation │ │
│ Continue? [y/N] │ │
└───────────────────────┘ │
│ │
No ─────┤ │
│ Yes │
└───────┬───────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Download with yt-dlp │
│ 1. Request format 140 (M4A ~130kbps native) │
│ 2. Fallback to 251 (Opus ~129kbps) │
│ 3. Fallback to bestaudio/best │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Post-process Audio │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ If Format 140 (M4A): │ │
│ │ - Extract without re-encoding (lossless) │ │
│ │ - Preserve ~130kbps bitrate │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ If Format 251 (Opus): │ │
│ │ - Convert to M4A @ 128kbps with FFmpeg │ │
│ │ - Match source quality (~129kbps) │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ If MP3 Requested: │ │
│ │ - Convert any format to MP3 @ 128kbps │ │
│ │ - Match source quality │ │
│ └─────────────────────────────────────────────────────────┘ │
│ Result: .tmp*{video_id}.m4a │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Extract Metadata (mutagen) │
│ - Read artist from ID3/M4A tags │
│ - Read title from ID3/M4A tags │
│ - Fallback to video title if empty │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Check Metadata Quality │
│ - Missing artist or title? │
│ - Junk patterns present? │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Missing/Junk Clean
│ │
▼ │
┌──────────────────────────┐ │
│ Flag for Review │ │
│ - Add to CSV │ │
│ - Note: missing/junk │ │
│ - Include source URL │ │
└──────────────────────────┘ │
│ │
└───────┬───────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Check for Duplicates │
│ - Normalize artist and title │
│ - Compare with existing library │
│ - Cross-format detection (M4A vs MP3) │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Duplicate New
│ │
▼ │
┌──────────────────────────┐ │
│ Interactive Prompt │ │
│ 1. Skip (keep existing) │ │
│ 2. Keep both │ │
│ 3. Replace existing │ │
└──────────────────────────┘ │
│ │
Skip ───┤ │
│ Keep/Replace │
└───────┬───────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Create Final Filename │
│ - Format: "Artist - Title.ext" │
│ - Sanitize: replace unsafe characters │
│ - Handle missing metadata with fallback │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Move to Final Location │
│ - Rename: .tmp*{id}.m4a → Artist - Title.m4a │
│ - Place in output directory │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ ✓ Saved: Artist - Title.m4a │
└─────────────────────────────────────────────────────────────┘

### Spotify Download Flow

┌─────────────────────────────────────────────────────────────┐
│ track-manager download "https://open.spotify.com/track/..." │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Detect Source: Spotify │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Initialize SpotifyDownloader │
│ - Check API credentials (client*id, client_secret) │
│ - Initialize spotdl with credentials │
│ - Configure: bitrate=128k (match YouTube quality) │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Fetch Track Info from Spotify API │
│ - Get artist, title, album metadata │
│ - Get cover art URL │
│ - Determine if track/playlist/album │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Playlist/Album Single
(>5 tracks) │
│ │
▼ │
┌───────────────────────┐ │
│ Ask for confirmation │ │
│ Continue? [y/N] │ │
└───────────────────────┘ │
│ │
No ─────┤ │
│ Yes │
└───────┬───────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ PRE-DOWNLOAD Duplicate Check ⚡ │
│ - Use Spotify metadata (artist, title) │
│ - Compare with existing library │
│ - Skip download if duplicate found │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Duplicate New
│ │
▼ │
┌──────────────────────────┐ │
│ ⏭️ Skip Download │ │
│ (Save bandwidth!) │ │
└──────────────────────────┘ │
│ │
│ └─────┐
│ │
└─────────────────────┤
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Download with spotdl │
│ 1. Search YouTube for track (using Spotify metadata) │
│ 2. Download from YouTube (best match) │
│ 3. Embed Spotify metadata into file │
│ 4. Set bitrate to 128kbps (match YouTube source) │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Find Downloaded File │
│ - Search for .tmp*\* or recently created files │
│ - Match by title substring │
│ - Support M4A and MP3 formats │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Extract Metadata │
│ - Read embedded metadata from file │
│ - Fallback to Spotify metadata if incomplete │
│ - Spotify metadata is authoritative │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Check Metadata Quality │
│ - Verify artist and title present │
│ - Check for junk patterns │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Missing/Junk Clean
│ │
▼ │
┌──────────────────────────┐ │
│ Flag for Review │ │
│ - Use Spotify metadata │ │
│ - Include Spotify URL │ │
└──────────────────────────┘ │
│ │
└───────┬───────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ POST-DOWNLOAD Duplicate Check (Safety) │
│ - Double-check in case of race conditions │
│ - Same logic as pre-download check │
└─────────────────────────────────────────────────────────────┘
│
┌───────┴───────┐
│ │
Duplicate New
│ │
▼ │
┌──────────────────────────┐ │
│ Interactive Prompt │ │
│ (same as YouTube) │ │
└──────────────────────────┘ │
│ │
Skip ───┤ │
│ Keep/Replace │
└───────┬───────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Create Final Filename & Move │
│ - Format: "Artist - Title.ext" │
│ - Sanitize filename │
│ - Move to output directory │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ ✓ Saved: Artist - Title.m4a │
└─────────────────────────────────────────────────────────────┘

**Key Difference:** Pre-download duplicate check saves bandwidth!

### SoundCloud Download Flow

┌─────────────────────────────────────────────────────────────┐
│ track-manager download "https://soundcloud.com/artist/..." │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Detect Source: SoundCloud │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Initialize SoundCloudDownloader │
│ - Inherits from YouTubeDownloader │
│ - Configure yt-dlp with higher quality settings │
│ - Set target: M4A @ 256kbps (SoundCloud Go+ quality) │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Download with yt-dlp │
│ - Request bestaudio format │
│ - SoundCloud offers up to 256kbps (Go+) │
│ - Standard accounts: ~128kbps │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Post-process Audio │
│ - Convert to M4A @ 256kbps with FFmpeg │
│ - Preserves Go+ quality (256kbps) │
│ - Minimal upsampling for standard quality (128→256) │
│ - Result: .tmp\_{track_id}.m4a │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ [Same as YouTube from here] │
│ - Extract metadata │
│ - Check quality │
│ - Check duplicates │
│ - Create filename │
│ - Move to final location │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ ✓ Saved: Artist - Title.m4a │
└─────────────────────────────────────────────────────────────┘

**Key Point:** Higher quality target (256kbps) to preserve SoundCloud Go+

### Direct URL Download Flow

┌─────────────────────────────────────────────────────────────┐
│ track-manager download "https://example.com/audio.mp3" │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Detect Source: Direct URL │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Initialize DirectDownloader │
│ - No special configuration needed │
│ - Uses Python requests library │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Download File │
│ - HTTP GET request with requests library │
│ - Stream download (progress bar) │
│ - Save to temporary file │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ NO Post-processing │
│ - File preserved as-is │
│ - No format conversion │
│ - No bitrate modification │
│ - Original quality maintained exactly │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Extract Metadata (if possible) │
│ - Try to read ID3/M4A tags │
│ - May fail if file has no tags │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Check for Duplicates │
│ - If metadata available, compare as usual │
│ - If no metadata, use filename │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Create Filename │
│ - Use metadata if available: "Artist - Title.ext" │
│ - Fallback to original filename if no metadata │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ Move to Final Location │
│ - Preserve original file exactly │
│ - No quality loss │
└─────────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ ✓ Saved: Artist - Title.mp3 (or original name) │
└─────────────────────────────────────────────────────────────┘

**Key Point:** Zero modifications - preserves original file quality

## Error Handling

### Failed Downloads

When a download fails:

1. **Error Logged** to `~/Documents/projects/DJ/failed-downloads.txt`

   ```
   2025-12-11 22:30 | https://youtube.com/... | Error message
   ```

2. **Process Continues** for playlists/albums

   - Individual failures don't stop the whole batch
   - Summary shows success/failed counts

3. **Retry Manually**

   ```bash
   # Check failed downloads
   cat ~/Documents/projects/DJ/failed-downloads.txt

   # Retry specific URL
   track-manager download <url>
   ```

### Common Errors

**1. Missing Spotify Credentials**
Error: Spotify API credentials not found

**Solution:**

```bash
# Option 1: Use config file
track-manager init
# Edit ~/.config/track-manager/config.yaml

# Option 2: Environment variables
export SPOTIPY_CLIENT_ID='your_id'
export SPOTIPY_CLIENT_SECRET='your_secret'
```

**2. yt-dlp Format Not Available**
Error: Requested format not available

**Solution:**

- Falls back to next format in preference list
- Should handle automatically in most cases
- If persistent, try updating yt-dlp: `pip install -U yt-dlp`

**3. Age-Restricted YouTube Videos**
Error: Sign in to confirm your age

**Solution:**

- Enable remote components in yt-dlp (already configured)
- Some videos may still fail due to YouTube restrictions
- Try finding alternate upload of same track

**4. Metadata Extraction Failed**
Warning: Could not read metadata from file

**Solution:**

- File is flagged for review in CSV
- Manually edit CSV with correct metadata
- Run `track-manager apply-metadata` to fix

**5. Duplicate File Found**
Interactive prompt: Duplicate found

**Solution:**

- Choose option based on need:
  - 1: Skip new file (most common)
  - 2: Keep both (if intentional duplicate)
  - 3: Replace existing (if new has better quality/metadata)

## Troubleshooting

### Downloads Are Slow

**Possible Causes:**

- Network speed limitations
- YouTube rate limiting
- Large playlists

**Solutions:**

```bash
# Download one at a time for large playlists
track-manager download <url>

# Check network connection
ping youtube.com
```

### Low Quality Files

**Check Quality:**

```bash
track-manager check-quality --detailed
```

**Expected Results:**

- YouTube: ~128-160 kbps (VBR, depends on format) ✓
- Spotify: ~128-160 kbps (VBR, via YouTube) ✓
- SoundCloud: ~128 kbps ✓
- Files <128 kbps: Investigate source

**Investigation:**

```bash
# Check specific file
ffprobe -hide_banner "filename.m4a"
```

### Metadata Issues

**Check Pending Reviews:**

```bash
track-manager apply-metadata --show
```

**Fix Metadata:**

1. Edit CSV with correct information
2. Run `track-manager apply-metadata`
3. Verify with `track-manager verify-metadata`

### Duplicate Detection Not Working

**Possible Causes:**

- Metadata missing (filenames used instead)
- Extreme variations in naming
- Tags not properly written

**Solutions:**

```bash
# Verify all files have metadata
track-manager verify-metadata

# Check specific file
ffprobe -hide_banner -show_entries format_tags "filename.m4a"
```

## Key Takeaways

### Quality Preservation Strategy

The track-manager system is designed to **preserve audio quality without unnecessary upscaling**:

#### 1. Match Source Quality

**YouTube (Format 140/251):**

- Source: Format 140 ~128kbps, Format 251 ~160kbps
- Target: VBR matching source (preferredquality: "0")
- Result: Preserves actual source quality, no upsampling or downsampling

**Spotify (via YouTube):**

- Source: ~128-160 kbps (YouTube formats 140/251)
- Target: VBR matching source (bitrate: "0")
- Result: Preserves YouTube source quality (consistent with YouTube handler)

**SoundCloud:**

- Source: ~128 kbps (free tier, no credentials)
- Target: 128 kbps
- Result: Matches actual source quality, no upsampling

#### 2. Avoid Deceptive Upscaling

**Problem:** Many downloaders upscale unnecessarily

- Download 130 kbps from YouTube
- Encode at 320 kbps MP3
- Result: 320 kbps file with 130 kbps quality
- 2.5x larger file size, zero quality gain
- Users think they have "high quality" but don't

**Our Approach:**

- Match target bitrate to source quality
- YouTube: VBR ~128-160 kbps (preserves format 140/251 quality)
- Spotify: VBR ~128-160 kbps (preserves YouTube source quality)
- SoundCloud: 128 kbps (matches free tier)
- No misleading file sizes
- Honest quality representation

#### 3. Format Preservation

**M4A (Default):**

- YouTube format 140 is native M4A
- Zero conversion when possible
- Lossless extraction from container
- Preserves exact source quality

**MP3 (Explicit):**

- Always requires conversion
- Uses appropriate bitrate (128 kbps)
- Transparent to user

#### 4. Post-Processing Intelligence

**FFmpeg Configuration:**

```python
{
    "key": "FFmpegExtractAudio",
    "preferredcodec": "m4a",
    "preferredquality": "128"  # This is a TARGET, not upsampling
}
```

**What This Does:**

- If source is ≤128 kbps: Preserves as-is (no upsampling)
- If conversion needed: Encodes at 128 kbps
- If source is native M4A: Extracts without re-encoding

**What This Doesn't Do:**

- Never upsamples 130 kbps → 320 kbps
- Never creates artificially large files
- Never misleads about quality

### Summary: Download Process

1. **Detect source** → Route to appropriate handler
2. **Request best format** → Prefer native containers
3. **Download** → Get highest quality available from source
4. **Post-process** → Match target to actual source quality
5. **Extract metadata** → Read from file, clean junk
6. **Check duplicates** → Prevent redundant storage
7. **Organize** → Rename, move to library
8. **Verify** → Tools available to check final quality

### Why This Matters

**For Users:**

- Honest quality representation
- Efficient storage usage
- No misleading bitrates
- Trust in the library

**For DJs:**

- Know true quality of tracks
- Make informed decisions
- Efficient library management
- Professional standards

**Technical Integrity:**

- Source quality respected
- No fake upsampling
- Transparent processing
- Verifiable results

### Quality Verification

Users can always verify the approach is working:

```bash
# Check library quality
track-manager check-quality

# Expected results:
# - YouTube files: ~128-160 kbps (VBR, depends on format)
# - Spotify files: ~128-130 kbps
# - SoundCloud files: ~128 kbps
# - No unnecessary upsampling
```

### Future Considerations

**If Source Quality Changes:**

- YouTube increases max quality → Update target bitrate
- New format becomes standard → Add to preference list
- Better codec available → Consider migration

**Current State (2025):**

- YouTube: Format 140 ~128kbps, Format 251 ~160kbps (stable for years)
- Spotify: Uses YouTube (via spotdl) with VBR matching source quality
- SoundCloud: ~128 kbps free tier (Go+ credentials not implemented)
- These formats and quality levels are stable and unlikely to change significantly

---

## Conclusion

This download process ensures:

1. **Quality preservation** - Match source, don't upscale
2. **Efficient storage** - No unnecessarily large files
3. **Clean metadata** - Professional organization
4. **Duplicate prevention** - No redundant downloads
5. **Transparency** - Verifiable quality claims

The system prioritizes **honesty over marketing** - users get exactly what the source provides, no more, no less.
