"""Check exported masks, coordinates and transparency against source pixels."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def check(directory: Path) -> int:
    metadata = json.loads((directory / "metadata.json").read_text())
    with Image.open(directory / metadata["original"]) as image:
        original = np.array(image.convert("RGBA"))
    assert original.shape[:2] == (metadata["height"], metadata["width"])
    union = np.zeros(original.shape[:2], dtype=bool)
    for person in metadata["people"]:
        with Image.open(directory / person["mask"]) as image:
            assert image.mode == "L"
            mask = np.array(image)
        assert mask.shape == union.shape
        assert set(np.unique(mask)) <= {0, 255}
        assert np.any(mask), "Detected person has an empty mask"
        x, y, width, height = (person["bbox"][key] for key in ("x", "y", "width", "height"))
        assert 0 <= x < x + width <= metadata["width"] + 0.001
        assert 0 <= y < y + height <= metadata["height"] + 0.001
        assert 0 <= person["confidence"] <= 1
        check_cutout(directory / person["isolated"], original, mask > 0)
        union |= mask > 0
    with Image.open(directory / metadata["mask"]) as image:
        assert np.array_equal(np.array(image), union.astype(np.uint8) * 255)
    check_cutout(directory / metadata["isolated"], original, union)
    with Image.open(directory / metadata["preview"]) as image:
        assert image.size == (metadata["width"], metadata["height"])
    return len(metadata["people"])


def check_cutout(path: Path, original: np.ndarray, mask: np.ndarray) -> None:
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        cutout = np.array(image)
    assert cutout.shape == original.shape
    assert np.array_equal(cutout[mask], original[mask])
    assert np.all(cutout[~mask] == 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    args = parser.parse_args()
    for directory in args.directories:
        print(f"{directory}: {check(directory)} people, output checks passed")
