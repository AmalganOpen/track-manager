# CLI Output Style Guide

This document defines consistent patterns for all CLI output in track-manager.

## Emoji Usage

### Standard Emoji Set

Use these emoji consistently across all output:

- **Success:** ✅ (U+2705) - Always use this, not ✓
- **Error:** ❌ (U+274C)
- **Warning:** ⚠️ (U+26A0 + FE0F)
- **Info:** ℹ️ (U+2139 + FE0F)
- **Progress:** ⬇️ (U+2B07 + FE0F) for downloads
- **Search:** 🔍 (U+1F50D)
- **Music:** 🎵 (U+1F3B5)
- **Folder:** 📁 (U+1F4C1)
- **Link:** 🔗 (U+1F517)
- **Stats:** 📊 (U+1F4CA)
- **Skip:** ⏭️ (U+23ED + FE0F)
- **Party:** 🎉 (U+1F389) for completion

### Emoji Spacing

Always include exactly one space after emoji:

```python
print("✅ Success")      # Correct
print("✅Success")       # Wrong
print("✅  Success")     # Wrong (two spaces)
```

## Message Categories

### Success Messages

```python
print("✅ Downloaded successfully")
print("✅ Metadata updated")
print("✅ All checks passed")
```

### Error Messages

Always write to stderr:

```python
print("❌ Download failed: connection timeout", file=sys.stderr)
print("❌ File not found: track.mp3", file=sys.stderr)
```

### Warning Messages

```python
print("⚠️ No metadata found")
print("⚠️ Duplicate detected")
```

### Info Messages

```python
print("ℹ️ Using format: M4A")
print("ℹ️ Keeping original format")
```

### Progress Messages

Use flush for dynamic updates:

```python
print(f"⬇️ Downloading... {progress}%", end="\r", flush=True)
print()  # Clear line after completion
```

## Formatting Rules

### Capitalization

- Capitalize first word after emoji
- Use sentence case for messages
- Don't end with periods unless multiple sentences

```python
print("✅ Download complete")           # Correct
print("✅ download complete")           # Wrong
print("✅ Download Complete")           # Wrong
print("✅ Download complete.")          # Wrong (single sentence)
print("✅ Download complete. Ready!")   # Correct (multiple sentences)
```

### Line Breaks

- Add blank line before major sections
- No blank line between related messages
- Add blank line after completion messages

```python
print()  # Blank line before section
print("🎵 Detected source: Spotify")
print("📁 Output directory: /path/to/dir")
print()  # Blank line after section

print("⬇️ Downloading...")
# No blank line here - related to download
print("✅ Download complete")
print()  # Blank line after completion
```

### Section Headers

Use Unicode box drawing characters for major sections:

```python
# Major header (60 chars)
print("━" * 60)
print("SECTION TITLE")
print("━" * 60)

# Sub-header (60 chars)
print("─" * 60)
print("Subsection")
print("─" * 60)
```

### Lists

Use consistent indentation (2 spaces):

```python
print("Found 3 files:")
print("  - file1.mp3")
print("  - file2.mp3")
print("  - file3.mp3")
```

## Interactive Prompts

### Format

```python
print("What would you like to do?")
print("  [s] Skip")
print("  [k] Keep")
print("  [r] Replace")
choice = input("Choice [s/k/r]: ").lower().strip()
```

### Rules

- Always provide options with brackets
- Show default in square brackets
- Use flush=True before input()

## Special Cases

### File Operations

```python
print("✅ Saved: filename.mp3")
print("✅ Renamed: old.mp3 → new.mp3")
print("⏭️ Skipped: filename.mp3 (duplicate)")
```

### Statistics

```python
print("📊 Summary")
print("  Total: 10 files")
print("  Success: 8")
print("  Failed: 2")
```

### URLs

```python
print("🔗 Looking up track on song.link...")
print("✅ Found on Spotify")
```

## Error Reporting

### Exceptions

Always include context:

```python
except Exception as e:
    print(f"❌ Download failed: {e}", file=sys.stderr)
```

### Validation Errors

```python
print("❌ Invalid format: must be 'auto', 'm4a', or 'mp3'", file=sys.stderr)
```

### Network Errors

```python
print("❌ Connection failed: check network", file=sys.stderr)
```

## Examples

### Good

```python
print("🎵 Detected source: Spotify")
print("📁 Output directory: ~/Music")
print()
print("🔍 Found ISRC: US1234567890")
print("🎵 Searching DAB Music...")
print("✅ Found on DAB Music")
print()
print("⬇️ Downloading FLAC...")
print("✅ Downloaded successfully")
print()
print("📊 Summary")
print("  Format: FLAC")
print("  Quality: Lossless")
print("  Size: 45.2 MB")
```

### Bad

```python
print("🎵 Detected source: spotify")  # Missing space, lowercase
print("Output directory: ~/Music")   # Missing emoji
print("Found ISRC: US1234567890")    # Missing emoji, inconsistent
print()
print()  # Double blank line
print("Searching DAB Music...")      # Missing emoji
print("✓ Found")                     # Wrong emoji, too terse
print("Downloading FLAC...")         # Missing emoji
print("✅ Downloaded.")              # Unnecessary period
print("SUMMARY:")                    # All caps, missing emoji
print("Format: FLAC")                # Missing indentation
```

## Testing

When adding new output:

1. Check emoji consistency
2. Verify stderr for errors
3. Test line breaks and spacing
4. Ensure capitalization follows rules
5. Test interactive prompts
