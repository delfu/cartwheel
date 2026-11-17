"""Restore missing test images from the checked-in source manifest."""

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from PIL import Image, ImageDraw

from extract_frames import extract_frames


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-dir", type=Path, default=project / "data")
    args = parser.parse_args()
    destination = project / "data/test-images"
    destination.mkdir(parents=True, exist_ok=True)
    sources = json.loads((project / "data/test-images-sources.json").read_text())
    for source in sources:
        path = destination / source["image"]
        if not path.exists():
            origin = source["source"]
            if origin.startswith("https://"):
                with urlopen(origin, timeout=30) as response:
                    content = response.read()
                if hashlib.sha256(content).hexdigest() != source["sha256"]:
                    raise ValueError(f"Downloaded image changed: {path.name}")
                path.write_bytes(content)
            elif "timestamp_seconds" in source:
                with TemporaryDirectory() as temporary:
                    frames = extract_frames(args.video_dir / origin, Path(temporary),
                                            timestamps=[source["timestamp_seconds"]])
                    (Path(temporary) / frames[0]["image"]).replace(path)
            elif source["image"] == "negative-no-people.png":
                Image.new("RGB", (640, 480), (100, 130, 160)).save(path)
            elif source["image"] == "negative-checkerboard.png":
                image = Image.new("RGB", (640, 480), "white")
                draw = ImageDraw.Draw(image)
                for y in range(0, image.height, 40):
                    for x in range(0, image.width, 40):
                        if (x // 40 + y // 40) % 2:
                            draw.rectangle((x, y, x + 39, y + 39), fill="#303030")
                image.save(path)
            else:
                raise ValueError(f"Unknown image source: {origin}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Image checksum mismatch: {path.name}")
        with Image.open(path) as image:
            image.load()
            assert image.size == (source["width"], source["height"])
    print(f"Verified {len(sources)} images in {destination}")


if __name__ == "__main__":
    main()
