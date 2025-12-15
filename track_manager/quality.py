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

        info = {
            "file": file_path.name,
            "format": file_path.suffix.upper()[1:],  # .mp3 -> MP3
            "duration": getattr(audio.info, "length", 0),
            "bitrate": getattr(audio.info, "bitrate", 0),
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


def analyze_library(output_dir: Path, detailed: bool = False) -> Dict:
    """Analyze audio quality across library.

    Args:
        output_dir: Library directory
        detailed: Show detailed info for each file

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
