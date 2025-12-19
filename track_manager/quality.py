"""Audio quality analysis for track library."""

from pathlib import Path
from typing import Dict, List, Optional

from mutagen import File as MutagenFile


def get_audio_info(file_path: Path) -> Optional[Dict]:
    """Extract audio quality information from file.

    Args:
        file_path: Path to audio file

    Returns:
        Dict with quality info or None if error
    """
    try:
        audio = MutagenFile(str(file_path))
        if not audio:
            return None

        # Get encoded bitrate as fallback
        encoded_bitrate = getattr(audio.info, "bitrate", 0)
        
        # Check for provenance metadata (true source quality)
        original_bitrate = None
        if hasattr(audio, "tags") and audio.tags:
            # Check for ORIGINAL_BITRATE tag (set by track-manager)
            original_bitrate_tag = audio.tags.get("ORIGINAL_BITRATE") or audio.tags.get("----:com.apple.iTunes:ORIGINAL_BITRATE")
            if original_bitrate_tag:
                try:
                    # Convert from "129.86" kbps string to bps integer
                    if isinstance(original_bitrate_tag, (list, tuple)):
                        original_bitrate_tag = original_bitrate_tag[0]
                    if hasattr(original_bitrate_tag, 'decode'):
                        original_bitrate_tag = original_bitrate_tag.decode('utf-8')
                    original_bitrate = int(float(str(original_bitrate_tag)) * 1000)
                except (ValueError, AttributeError):
                    pass

        # Use original bitrate if available (true quality), otherwise use encoded
        bitrate = original_bitrate if original_bitrate else encoded_bitrate
        
        info = {
            "file": file_path.name,
            "format": file_path.suffix.upper()[1:],  # .mp3 -> MP3
            "duration": getattr(audio.info, "length", 0),
            "bitrate": bitrate,
            "encoded_bitrate": encoded_bitrate,  # Keep for comparison
            "is_upsampled": original_bitrate is not None and original_bitrate < encoded_bitrate,
            "sample_rate": getattr(audio.info, "sample_rate", 0),
            "channels": getattr(audio.info, "channels", 0),
        }

        # Calculate approximate file size per minute for comparison
        if info["duration"] > 0:
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            info["size_mb"] = file_size_mb
            info["mb_per_min"] = file_size_mb / (info["duration"] / 60)

        return info

    except Exception as e:
        print(f"⚠️ Error reading {file_path.name}: {e}")
        return None


def format_bitrate(bitrate: int) -> str:
    """Format bitrate in kbps."""
    if bitrate >= 1000000:
        return f"{bitrate / 1000000:.1f} Mbps"
    elif bitrate >= 1000:
        return f"{bitrate // 1000} kbps"
    else:
        return f"{bitrate} bps"


def format_sample_rate(sample_rate: int) -> str:
    """Format sample rate in kHz."""
    if sample_rate >= 1000:
        return f"{sample_rate / 1000:.1f} kHz"
    else:
        return f"{sample_rate} Hz"


def format_duration(seconds: float) -> str:
    """Format duration as MM:SS."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def analyze_library(output_dir: Path, detailed: bool = False, verbose: bool = False) -> Dict:
    """Analyze audio quality across library.

    Args:
        output_dir: Library directory
        detailed: Show detailed info for each file
        verbose: Show outlier tracks (worst/best quality)

    Returns:
        Dict with analysis results
    """
    print(f"🔍 Analyzing audio quality in {output_dir}...\n")

    files_info = []

    # Scan audio files
    for pattern in ["*.m4a", "*.M4A", "*.mp3", "*.MP3", "*.flac", "*.FLAC"]:
        for file_path in output_dir.glob(pattern):
            info = get_audio_info(file_path)
            if info:
                # Store full path for verbose output
                info["path"] = file_path
                files_info.append(info)

    if not files_info:
        print("No audio files found")
        return {}

    # Group by format
    by_format = {}
    for info in files_info:
        fmt = info["format"]
        if fmt not in by_format:
            by_format[fmt] = []
        by_format[fmt].append(info)

    # Print summary statistics
    print(f"📊 Summary ({len(files_info)} files)\n")

    for fmt, files in sorted(by_format.items()):
        print(f"{'=' * 60}")
        print(f"{fmt} - {len(files)} files")
        print(f"{'=' * 60}")

        bitrates = [f["bitrate"] for f in files if f["bitrate"] > 0]
        if bitrates:
            avg_bitrate = sum(bitrates) / len(bitrates)
            min_bitrate = min(bitrates)
            max_bitrate = max(bitrates)

            print(f"  Bitrate:")
            print(f"    Average: {format_bitrate(int(avg_bitrate))}")
            print(
                f"    Range: {format_bitrate(min_bitrate)} - {format_bitrate(max_bitrate)}"
            )

        sample_rates = [f["sample_rate"] for f in files if f["sample_rate"] > 0]
        if sample_rates:
            # Get unique sample rates
            unique_rates = sorted(set(sample_rates))
            print(
                f"  Sample Rate: {', '.join(format_sample_rate(sr) for sr in unique_rates)}"
            )

        # Quality categories
        if bitrates:
            low_quality = [f for f in files if 0 < f["bitrate"] < 128000]
            med_quality = [f for f in files if 128000 <= f["bitrate"] < 256000]
            high_quality = [f for f in files if f["bitrate"] >= 256000]

            if low_quality or med_quality or high_quality:
                print(f"  Quality Distribution:")
                if low_quality:
                    print(f"    Low (<128 kbps): {len(low_quality)} files")
                if med_quality:
                    print(f"    Medium (128-256 kbps): {len(med_quality)} files")
                if high_quality:
                    print(f"    High (≥256 kbps): {len(high_quality)} files")

        print()

    # Show outliers if verbose
    if verbose and files_info:
        print(f"\n{'=' * 80}")
        print("QUALITY OUTLIERS")
        print(f"{'=' * 80}\n")

        # Get files with valid bitrates
        files_with_bitrate = [f for f in files_info if f["bitrate"] > 0]
        
        if files_with_bitrate:
            # Sort by bitrate
            sorted_by_bitrate = sorted(files_with_bitrate, key=lambda x: x["bitrate"])
            
            # Show worst quality (lowest bitrate)
            print("📉 Lowest Quality Tracks (5 worst):")
            print(f"{'-' * 80}")
            for info in sorted_by_bitrate[:5]:
                bitrate_str = format_bitrate(info['bitrate'])
                if info.get('is_upsampled'):
                    bitrate_str += f" (encoded as {format_bitrate(info['encoded_bitrate'])})"
                print(f"  {bitrate_str:>30} - {info['path']}")
            print()
            
            # Show best quality (highest bitrate)
            print("📈 Highest Quality Tracks (5 best):")
            print(f"{'-' * 80}")
            for info in sorted_by_bitrate[-5:][::-1]:  # Reverse to show highest first
                bitrate_str = format_bitrate(info['bitrate'])
                if info.get('is_upsampled'):
                    bitrate_str += f" (encoded as {format_bitrate(info['encoded_bitrate'])})"
                print(f"  {bitrate_str:>30} - {info['path']}")
            print()
            
            # Show upsampled tracks
            upsampled = [f for f in files_with_bitrate if f.get('is_upsampled')]
            if upsampled:
                print("⚠️  Upsampled Tracks (encoded higher than source):")
                print(f"{'-' * 80}")
                # Sort by difference (most upsampled first)
                upsampled_sorted = sorted(
                    upsampled,
                    key=lambda x: x['encoded_bitrate'] - x['bitrate'],
                    reverse=True
                )
                for info in upsampled_sorted[:10]:  # Show top 10
                    source_rate = format_bitrate(info['bitrate'])
                    encoded_rate = format_bitrate(info['encoded_bitrate'])
                    print(f"  {source_rate:>10} → {encoded_rate:>10} - {info['path']}")
                if len(upsampled) > 10:
                    print(f"  ... and {len(upsampled) - 10} more upsampled tracks")
                print()

    # Show detailed file list if requested
    if detailed:
        print(f"\n{'=' * 80}")
        print("DETAILED FILE LIST")
        print(f"{'=' * 80}\n")

        for info in sorted(files_info, key=lambda x: x["bitrate"], reverse=True):
            print(f"{info['file']}")
            print(f"  Format: {info['format']}")
            if info["bitrate"] > 0:
                print(f"  Bitrate: {format_bitrate(info['bitrate'])}")
            if info["sample_rate"] > 0:
                print(f"  Sample Rate: {format_sample_rate(info['sample_rate'])}")
            if info["duration"] > 0:
                print(f"  Duration: {format_duration(info['duration'])}")
            if "size_mb" in info:
                print(
                    f"  Size: {info['size_mb']:.1f} MB ({info['mb_per_min']:.1f} MB/min)"
                )
            print()

    # Flag low-quality files
    low_quality_files = [
        f for f in files_info if f["bitrate"] > 0 and f["bitrate"] < 128000
    ]

    if low_quality_files:
        print(f"\n⚠️ {len(low_quality_files)} low-quality files detected (<128 kbps):")
        for info in low_quality_files[:10]:
            print(f"  {info['file']} - {format_bitrate(info['bitrate'])}")
        if len(low_quality_files) > 10:
            print(f"  ... and {len(low_quality_files) - 10} more")
        print()

    return {
        "total_files": len(files_info),
        "by_format": {fmt: len(files) for fmt, files in by_format.items()},
        "low_quality": len(low_quality_files),
    }
