"""Run with: uv run python -m backend.ml image.jpg --output output/example"""

import argparse
import json
from pathlib import Path
from time import perf_counter

from PIL import UnidentifiedImageError

from .pipeline import Segmenter, isolate, load_image, preview


def main() -> None:
    parser = argparse.ArgumentParser(description="Segment people and export full-resolution PNG masks")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="A new output directory; existing directories are never overwritten")
    parser.add_argument("--model", default="yolov8n-seg.pt", choices=["yolov8n-seg.pt", "yolov8s-seg.pt"])
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory already exists; choose a new directory")
    try:
        image = load_image(args.image)
        started = perf_counter()
        segmenter = Segmenter(Path("models") / args.model)
        loaded = perf_counter()
        people = segmenter.segment(image)
        finished = perf_counter()
        args.output.mkdir(parents=True, exist_ok=False)
        image.save(args.output / "original.png")
        preview(image, people).save(args.output / "preview.png")
        mask, isolated = isolate(image, [person.mask for person in people])
        mask.save(args.output / "mask.png")
        isolated.save(args.output / "isolated.png")
        detections = []
        for index, person in enumerate(people, 1):
            mask_name = f"person-{index:03d}-mask.png"
            isolated_name = f"person-{index:03d}-isolated.png"
            mask, isolated = isolate(image, [person.mask])
            mask.save(args.output / mask_name)
            isolated.save(args.output / isolated_name)
            x, y, width, height = person.bbox
            detections.append({
                "index": index, "confidence": person.confidence,
                "bbox": {"x": x, "y": y, "width": width, "height": height},
                "mask": mask_name, "isolated": isolated_name,
            })
        metadata = {
            "source": str(args.image), "model": args.model,
            "width": image.width, "height": image.height,
            "model_load_seconds": round(loaded - started, 3),
            "inference_seconds": round(finished - loaded, 3),
            "original": "original.png", "preview": "preview.png",
            "mask": "mask.png", "isolated": "isolated.png", "people": detections,
        }
        (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, UnidentifiedImageError) as error:
        parser.exit(1, f"Segmentation failed: {error}\n")
    print(f"Detected {len(people)} people. Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
