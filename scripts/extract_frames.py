"""Extract two full-resolution PNG frames at 25% and 60% of a video's duration."""

import argparse
import math
from pathlib import Path
import subprocess


def extract_frames(video: Path, output: Path, *, overwrite: bool = False,
                   timestamps: list[float] | None = None) -> list[dict]:
    if not video.is_file():
        raise ValueError(f"Video does not exist: {video}")
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video.resolve()),
    ], text=True).strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video must have a finite, positive duration")
    if timestamps is None:
        timestamps = [round(duration * fraction, 3) for fraction in (0.25, 0.6)]
    if any(not math.isfinite(timestamp) or not 0 <= timestamp < duration for timestamp in timestamps):
        raise ValueError("Frame timestamps must be within the video's duration")
    paths = [output / f"{video.stem}-frame-{index}.png" for index in range(1, len(timestamps) + 1)]
    if not overwrite and any(path.exists() for path in paths):
        raise ValueError("Output frames already exist; choose another folder or use --overwrite")
    output.mkdir(parents=True, exist_ok=True)
    frames = []
    for timestamp, path in zip(timestamps, paths, strict=True):
        subprocess.run([
            "ffmpeg", "-v", "error", "-y" if overwrite else "-n",
            "-ss", str(timestamp), "-i", str(video.resolve()),
            "-map", "0:v:0", "-frames:v", "1", "-update", "1", str(path.resolve()),
        ], check=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"No frame was produced at {timestamp} seconds")
        frames.append({"image": path.name, "source": video.name, "timestamp_seconds": timestamp})
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true", help="Replace previously extracted frames")
    args = parser.parse_args()
    try:
        frames = extract_frames(args.video, args.output, overwrite=args.overwrite)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Frame extraction failed: {error}\n")
    print(f"Extracted {len(frames)} frames to {args.output.resolve()}")


if __name__ == "__main__":
    main()
