[Use a markdown previewer for a more enjoyable reading experience]: #

# Junk Patterns

This document lists all current and implemented junk patterns for metadata normalization in duplicate detection.

---

## Current Patterns (Already Implemented)

### Official/Video/Audio Patterns

| Pattern           | Example Match           | Result |
| ----------------- | ----------------------- | ------ |
| `\[official.*?\]` | "Song [Official Video]" | "song" |
| `\(official.*?\)` | "Song (Official Audio)" | "song" |
| `\[.*?video.*?\]` | "Song [Music Video]"    | "song" |
| `\(.*?video.*?\)` | "Song (Video)"          | "song" |
| `\[.*?audio.*?\]` | "Song [Audio]"          | "song" |
| `\(.*?audio.*?\)` | "Song (Audio)"          | "song" |
| `\[hd\]`          | "Song [HD]"             | "song" |
| `\(hd\)`          | "Song (HD)"             | "song" |

### Featuring Variations (Normalization)

| Pattern                 | Example Match           | Result              |
| ----------------------- | ----------------------- | ------------------- |
| `\s*\(?\s*ft\.?\s+`     | "Song ft. Artist"       | "song feat. artist" |
| `\s*\(?\s*feat\.?\s+`   | "Song feat Artist"      | "song feat. artist" |
| `\s*\(?\s*featuring\s+` | "Song featuring Artist" | "song feat. artist" |

### Artist Separators (Normalization) - REMOVED

These patterns were removed because they broke artist names like "Excision".

| Pattern           | Example Match            | Result                    |
| ----------------- | ------------------------ | ------------------------- |
| ~~`\s*[x&]\s*`~~  | ~~"Artist1 & Artist2"~~  | ~~"artist1 vs. artist2"~~ |
| ~~`\s+vs\.?\s+`~~ | ~~"Artist1 VS Artist2"~~ | ~~"artist1 vs. artist2"~~ |

---

## New Patterns (Implemented)

### Video/Lyrics Patterns

| Pattern                        | Example Match        | Result |
| ------------------------------ | -------------------- | ------ |
| `[\[\(].*?lyric.*?s?.*?[\]\)]` | "Song [Lyrics]"      | "song" |
| `[\[\(].*?lyric.*?s?.*?[\]\)]` | "Song (Lyric Video)" | "song" |
| `[\[\(]visuali[sz]er.*?[\]\)]` | "Song [Visualizer]"  | "song" |

### Quality/Resolution Patterns

| Pattern                     | Example Match         | Result |
| --------------------------- | --------------------- | ------ |
| `[\[\(]4k[\]\)]`            | "Song [4K]"           | "song" |
| `[\[\(]8k[\]\)]`            | "Song [8K]"           | "song" |
| `[\[\(]uhd[\]\)]`           | "Song [UHD]"          | "song" |
| `[\[\(]hq[\]\)]`            | "Song [HQ]"           | "song" |
| `[\[\(]lq[\]\)]`            | "Song [LQ]"           | "song" |
| `[\[\(].*?quality.*?[\]\)]` | "Song (High Quality)" | "song" |

### Platform/Source Patterns

| Pattern               | Example Match    | Result   |
| --------------------- | ---------------- | -------- |
| `\s*-\s*topic\s*$`    | "Artist - Topic" | "artist" |
| `[\[\(]premium[\]\)]` | "Song [Premium]" | "song"   |

### Misc Patterns

| Pattern                       | Example Match          | Result |
| ----------------------------- | ---------------------- | ------ |
| `[\[\(]free\s*download[\]\)]` | "Song [Free Download]" | "song" |
| `[\[\(]download.*?[\]\)]`     | "Song (Download Link)" | "song" |
| `[\[\(]out\s*now[\]\)]`       | "Song [Out Now]"       | "song" |
| `[\[\(]new[\]\)]`             | "Song [NEW]"           | "song" |

---

## Patterns NOT Implemented

These patterns were considered but NOT implemented because they represent meaningful distinctions for DJ libraries.

### Content Type/Rating Patterns

| Pattern                    | Example Match          | Result              |
| -------------------------- | ---------------------- | ------------------- |
| `[\[\(]explicit[\]\)]`     | "Song (Explicit)"      | "song"              |
| `[\[\(]clean[\]\)]`        | "Song (Clean)"         | "song"              |
| `[\[\(]radio\s*edit[\]\)]` | "Song (Radio Edit)"    | "song (radio edit)" |
| `[\[\(].*?edit[\]\)]`      | "Song [Extended Edit]" | "song"              |
| `[\[\(].*?version[\]\)]`   | "Song (Album Version)" | "song"              |

### Remaster/Release Patterns

| Pattern                         | Example Match             | Result |
| ------------------------------- | ------------------------- | ------ |
| `[\[\(].*?remaster.*?[\]\)]`    | "Song (Remastered)"       | "song" |
| `[\[\(].*?anniversary.*?[\]\)]` | "Song (20th Anniversary)" | "song" |
| `[\[\(].*?deluxe.*?[\]\)]`      | "Song [Deluxe Edition]"   | "song" |

### Year Patterns

| Pattern                           | Example Match          | Result |
| --------------------------------- | ---------------------- | ------ |
| `[\[\(]\d{4}[\]\)]`               | "Song (2024)"          | "song" |
| `[\[\(]\d{4}\s*remaster.*?[\]\)]` | "Song (2024 Remaster)" | "song" |

### Platform/Source Patterns

| Pattern                       | Example Match             | Result |
| ----------------------------- | ------------------------- | ------ |
| `[\[\(]spotify.*?[\]\)]`      | "Song [Spotify Sessions]" | "song" |
| `[\[\(].*?session.*?s?[\]\)]` | "Song (Live Session)"     | "song" |

### Performance Type Patterns

| Pattern                       | Example Match         | Result                |
| ----------------------------- | --------------------- | --------------------- |
| `[\[\(]live[\]\)]`            | "Song (Live)"         | "song (live)"         |
| `[\[\(].*?acoustic.*?[\]\)]`  | "Song (Acoustic)"     | "song (acoustic)"     |
| `[\[\(].*?instrumental[\]\)]` | "Song [Instrumental]" | "song (instrumental)" |
| `[\[\(].*?remix.*?[\]\)]`     | "Song (Remix)"        | "song (remix)"        |
| `[\[\(].*?bootleg.*?[\]\)]`   | "Song (Bootleg)"      | "song (bootleg)"      |
| `[\[\(].*?mashup.*?[\]\)]`    | "Song (Mashup)"       | "song (mashup)"       |

---

## Implementation Notes

### Pattern Grouping

Patterns are organized by category in code:

```python
VIDEO_PATTERNS = [...]
QUALITY_PATTERNS = [...]
VERSION_PATTERNS = [...]  # Be careful with these for DJ use!
```

### Test Cases

Each implemented pattern has corresponding test cases in `tests/test_duplicates.py`.

### DJ Library Considerations

**IMPORTANT**: Many patterns that are "junk" for general music libraries might be **meaningful distinctions** for DJ libraries:

- ⚠️ **Radio Edits** - Different length/structure than original
- ⚠️ **Extended Edits** - Longer versions for mixing
- ⚠️ **Live Versions** - Different feel/energy
- ⚠️ **Acoustic Versions** - Completely different arrangement
- ⚠️ **Instrumentals** - No vocals (useful for mixing)
- ⚠️ **Clean/Explicit** - Different content (venue considerations)
- ⚠️ **Bootlegs/Mashups** - Unique versions
- ⚠️ **Sessions (Spotify/BBC/etc)** - Different recordings

**Recommendation**: Be conservative with removal - when in doubt, keep the distinction!
