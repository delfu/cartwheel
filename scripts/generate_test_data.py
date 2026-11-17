"""Download reference photos and optionally extract still frames from local clips."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from PIL import Image

from extract_frames import extract_frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-dir", type=Path)
    args = parser.parse_args()
    destination = Path("samples")
    destination.mkdir(exist_ok=True)
    sources = []
    for name in ("bus", "zidane"):
        url = f"https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/{name}.jpg"
        path = destination / f"{name}.jpg"
        if not path.exists():
            with urlopen(url, timeout=30) as response:
                path.write_bytes(response.read())
        sources.append({"image": path.name, "source": url})
    Image.new("RGB", (640, 480), (100, 130, 160)).save(destination / "no-people.png")
    sources.append({"image": "no-people.png", "source": "Generated solid-color negative control"})
    if args.video_dir:
        videos = sorted(args.video_dir.glob("*.mp4"))
        if not videos:
            parser.error("No MP4 clips found in the video directory")
        for video in videos:
            sources.extend(extract_frames(video, destination, overwrite=True))
    for source in sources:
        path = destination / source["image"]
        source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            source["width"], source["height"] = image.size
    (destination / "sources.json").write_text(json.dumps(sources, indent=2) + "\n")
    print(f"Prepared {len(sources)} images in {destination.resolve()}")


if __name__ == "__main__":
    main()
