# Track Quality Strategy

This document explains the technical decisions behind track-manager's quality preservation approach.

## Core Principle

**Preserve actual source quality honestly** - no upsampling, no false advertising, no unnecessary file bloat.

## Understanding Audio Formats

### YouTube Format Options

YouTube provides multiple audio formats with different characteristics:

| Format | Codec | Bitrate | Frequency Range | Notes |
|--------|-------|---------|-----------------|-------|
| **251** | Opus | ~160kbps | **20kHz** | Best quality, preferred |
| **140** | AAC (M4A) | ~128kbps | 16kHz | Standard quality, fallback |
| 250 | Opus | ~70kbps | ~15kHz | Lower quality |
| 249 | Opus | ~50kbps | ~12kHz | Lowest quality |

**Key insight:** Format 251 preserves frequencies up to **20kHz** (full audible spectrum), while format 140 cuts off at **16kHz**.

### Why Frequency Range Matters

The human hearing range extends to approximately 20kHz. Audio formats with higher frequency cutoffs preserve:
- Better high-frequency detail (cymbals, hi-hats, brightness)
- More "air" and spaciousness
- Better stereo imaging
- Overall more faithful reproduction

**Example from testing:**
- Format 251 → 166kbps M4A: 20kHz range ✓
- Format 140 → 162kbps M4A: 16kHz cutoff ❌

**The 166kbps file sounds better** despite similar bitrate, because frequency range matters more than bitrate alone.

## Why We Prefer Format 251 (Opus)

### Technical Advantages

1. **Full Frequency Range**
   - Preserves up to 20kHz
   - Complete audible spectrum
   - Better for critical listening

2. **Higher Source Quality**
   - ~160kbps vs ~128kbps (format 140)
   - More audio information to work with
   - Better transcoding results

3. **Modern Codec**
   - Opus is state-of-the-art
   - Better efficiency than AAC
   - Designed for transparency

### Format Preference Order

```python
"format": "251/140/bestaudio/best"
```

**Explanation:**
1. **Try format 251 first** - Get best quality (20kHz)
2. **Fallback to 140** - If 251 unavailable (rare)
3. **bestaudio** - Safety fallback
4. **best** - Last resort

## Why We Convert to M4A

### Why Not Keep Opus?

Opus files use WebM containers, which have poor compatibility:
- ❌ Not supported by many DJ software
- ❌ Poor metadata support
- ❌ Limited tool compatibility
- ❌ Playback issues on some devices

### Why M4A (AAC)?

**M4A offers the best balance:**

1. **Excellent Compression**
   - Smaller files than MP3 at same quality
   - ~30% smaller for equivalent perceptual quality
   - More efficient than most codecs

2. **Universal Compatibility**
   - Supported by all modern devices
   - Works in all DJ software
   - Native support in macOS, iOS
   - Good support on Windows, Android

3. **Good Metadata Support**
   - Rich tagging capabilities
   - Album art support
   - Industry standard

4. **Modern Format**
   - Still actively developed
   - Part of MPEG-4 standard
   - Better than legacy formats

### Conversion Quality

**Opus → M4A conversion:**
- Minimal quality loss (both modern codecs)
- Slight bitrate increase acceptable (160→192kbps)
- Preserves frequency range (20kHz maintained)

**Why slight bitrate increase is acceptable:**
- Ensures 20kHz preservation during transcoding
- Small overhead (~20%) is normal for lossy→lossy
- Still vastly better than upsampling to 400kbps

## Why We Hardcode Bitrate for YouTube

### The Problem with `preferredquality="0"`

When using yt-dlp with FFmpeg postprocessing:

```python
"preferredquality": "0"  # What we initially tried
```

**Result:** Massive upsampling!
- Source: Format 251 Opus ~160kbps
- Output: M4A **400+ kbps** ❌
- Problem: 2.5x upsampling, fake quality, wasted space

### What `preferredquality="0"` Actually Means

In FFmpeg's VBR quality scale:
- `0` = "highest quality VBR"
- NOT "match source quality"
- FFmpeg encodes at maximum bitrate it thinks is "lossless"
- Ignores that source is already lossy at 160kbps

**This violates our core principle:** honest quality representation.

### The Solution: Hardcode `192kbps`

```python
"preferredquality": "192"  # Explicit bitrate
```

**Why 192kbps:**

1. **High Enough for 20kHz**
   - Preserves full frequency range from format 251
   - No quality loss in audible spectrum
   - Maintains source fidelity

2. **Avoids Upsampling**
   - 160kbps source → 192kbps output
   - Only ~20% overhead (acceptable for transcoding)
   - Not excessive like 400kbps

3. **Transparent Quality**
   - Difference from source is imperceptible
   - Transcoding artifacts minimized
   - Professional quality output

4. **Reasonable File Size**
   - ~1.4MB per minute
   - Efficient storage usage
   - Appropriate for the quality level

### Why Not Lower?

**Testing 160kbps or 128kbps targets:**
- Risk losing 20kHz range during Opus→AAC conversion
- Transcoding needs headroom to preserve quality
- Better to have slight overhead than cut quality

**The ~192kbps is the sweet spot** for preserving format 251's quality without waste.

## Spotify: Why `bitrate="0"` Works Differently

### The Spotify Exception

For Spotify (via spotdl), we use:

```python
downloader_settings["bitrate"] = "0"
```

**Result:** ~166kbps M4A @ 20kHz ✓

### Why This Is Different

spotdl interprets `bitrate="0"` differently than yt-dlp's `preferredquality`:
- spotdl: "match source intelligently"
- yt-dlp: "maximum quality VBR"

**Empirical evidence:**
- Spotify with `bitrate="0"`: 166kbps (good) ✓
- YouTube with `preferredquality="0"`: 400+ kbps (bad) ❌

### Why Keep It This Way

**Honesty in quality representation:**
- Source: ~160kbps Opus from YouTube
- Output: 166kbps M4A
- **The 166kbps accurately reflects source quality**
- Minimal overhead (~4%) for conversion
- Not inflated like YouTube's 192kbps

**Alternative (align with YouTube):**
```python
downloader_settings["bitrate"] = "192"
```

**Why we don't:**
- Would inflate bitrate beyond source quality
- 192kbps suggests better quality than source provides
- Less honest representation to users
- Current 166kbps is more truthful

### The Trade-off

**Consistency vs Honesty:**
- YouTube: 192kbps (slight inflation, needed to avoid 400kbps)
- Spotify: 166kbps (honest representation of source)

We choose **honesty** - users seeing file bitrate get accurate information about actual quality.

## Summary: Quality Strategy

### What We Do

1. **Prefer format 251** (Opus, 20kHz) over 140 (AAC, 16kHz)
2. **Convert to M4A** for compatibility and efficiency
3. **YouTube: 192kbps** to preserve quality without excessive upsampling
4. **Spotify: ~166kbps** for honest representation of source quality

### What We Avoid

1. ❌ **Upsampling** - No 160kbps → 400kbps inflation
2. ❌ **False advertising** - Bitrates reflect actual quality
3. ❌ **Quality loss** - Preserve 20kHz frequency range
4. ❌ **Waste** - No unnecessarily large files

### Quality Metrics That Matter

**In order of importance:**
1. **Frequency range** (20kHz > 16kHz)
2. **Source format** (Opus 160kbps > AAC 128kbps)
3. **Output format** (M4A for compatibility)
4. **Bitrate** (only after above factors)

**Key insight:** 166kbps M4A @ 20kHz > 162kbps M4A @ 16kHz

The frequency range and source quality matter more than the bitrate number alone.

## For Users

**What you get:**
- Best available quality from YouTube (format 251 when available)
- M4A format (universal compatibility, efficient)
- Honest bitrate (reflects actual source quality)
- Preserved frequency range (20kHz full spectrum)
- No fake upsampling or quality inflation

**File sizes:**
- YouTube: ~1.4 MB/minute (192kbps M4A)
- Spotify: ~1.2 MB/minute (166kbps M4A)
- Both preserve 20kHz frequency range

**Quality assurance:**
- Test files with spectrum analyzer
- Verify 20kHz preservation
- Confirm no unnecessary upsampling
- Ensure honest quality representation
