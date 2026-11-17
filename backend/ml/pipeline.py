"""Person segmentation in oriented source-image pixel coordinates."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps


@dataclass
class Person:
    confidence: float
    bbox: tuple[float, float, float, float]
    mask: np.ndarray


def load_image(path: Path) -> Image.Image:
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Image exceeds the 25 MiB limit")
    with Image.open(path) as image:
        if image.format not in {"JPEG", "PNG"}:
            raise ValueError("Provide a PNG or JPEG image")
        if image.width * image.height > 40_000_000:
            raise ValueError("Image exceeds the 40 megapixel limit")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("Provide a still image, not an animated image")
        return ImageOps.exif_transpose(image).convert("RGBA")


class Segmenter:
    def __init__(self, model_path: Path):
        from ultralytics import YOLO

        model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model = YOLO(str(model_path))

    def segment(self, image: Image.Image) -> list[Person]:
        result = self.model.predict(
            image.convert("RGB"), classes=[0], conf=0.25,
            retina_masks=True, device="cpu", verbose=False,
        )[0]
        if len(result.boxes) == 0:
            return []
        if result.masks is None:
            raise RuntimeError("The model returned detections without segmentation masks")
        people = []
        for box, confidence, mask in zip(
            result.boxes.xyxy.cpu().numpy(),
            result.boxes.conf.cpu().numpy(),
            result.masks.data.cpu().numpy(),
            strict=True,
        ):
            if mask.shape != (image.height, image.width):
                raise RuntimeError("Model masks do not match the source image dimensions")
            x1, y1, x2, y2 = map(float, box)
            people.append(Person(float(confidence), (x1, y1, x2 - x1, y2 - y1), mask > 0.5))
        return people


def isolate(image: Image.Image, masks: list[np.ndarray]) -> tuple[Image.Image, Image.Image]:
    union = np.zeros((image.height, image.width), dtype=bool)
    for mask in masks:
        if mask.shape != union.shape or mask.dtype != bool:
            raise ValueError("Masks must be boolean arrays matching the source image dimensions")
        union |= mask
    pixels = np.array(image.convert("RGBA"))
    pixels[~union] = 0
    return Image.fromarray(union.astype(np.uint8) * 255), Image.fromarray(pixels)


def preview(image: Image.Image, people: list[Person]) -> Image.Image:
    colors = [(255, 90, 90), (40, 180, 255), (80, 220, 120), (220, 130, 255)]
    pixels = np.array(image.convert("RGB"))
    for index, person in enumerate(people):
        color = np.array(colors[index % len(colors)])
        pixels[person.mask] = (pixels[person.mask] * 0.6 + color * 0.4).astype(np.uint8)
    output = Image.fromarray(pixels)
    draw = ImageDraw.Draw(output)
    for index, person in enumerate(people, 1):
        x, y, width, height = person.bbox
        color = colors[(index - 1) % len(colors)]
        draw.rectangle((x, y, x + width, y + height), outline=color, width=3)
        draw.text((x + 3, y + 3), f"Person {index}: {person.confidence:.0%}", fill="white", stroke_width=2, stroke_fill="black")
    return output
