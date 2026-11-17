"""Extract two full-resolution PNG frames at 25% and 60% of a video's duration."""

import argparse
import math
from pathlib import Path
import subprocess


def extract_frames(video: Path, output: Path, *, overwrite: bool = False) -> list[dict]:
    if not video.is_file():
        raise ValueError(f"Video does not exist: {video}")
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video.resolve()),
    ], text=True).strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video must have a finite, positive duration")
    paths = [output / f"{video.stem}-frame-{index}.png" for index in (1, 2)]
    if not overwrite and any(path.exists() for path in paths):
        raise ValueError("Output frames already exist; choose another folder or use --overwrite")
    output.mkdir(parents=True, exist_ok=True)
    frames = []
    for fraction, path in zip((0.25, 0.6), paths, strict=True):
        timestamp = round(duration * fraction, 3)
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
