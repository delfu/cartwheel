"""Write person segmentation artifacts for CLI runs and queued jobs."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
from tempfile import TemporaryFile

from PIL import Image

from backend.contracts import SegmentationResult
from .pipeline import Person, Segmenter, isolate, load_image, preview


def process_image(source: Path, output: Path, segmenter: Segmenter) -> SegmentationResult:
    image = load_image(source)
    return export_segmentation(image, segmenter.segment(image), output)


def export_segmentation(image: Image.Image, people: list[Person], output: Path) -> SegmentationResult:
    output.mkdir(parents=True, exist_ok=False)
    preview(image, people).save(output / "preview.png")
    mask, isolated = isolate(image, [person.mask for person in people])
    mask.save(output / "mask.png")
    isolated.save(output / "isolated.png")
    result: SegmentationResult = {
        "width": image.width, "height": image.height,
        "preview": "preview.png", "mask": "mask.png", "isolated": "isolated.png", "people": [],
    }
    for index, person in enumerate(people, 1):
        mask_name = f"person-{index:03d}-mask.png"
        isolated_name = f"person-{index:03d}-isolated.png"
        mask, isolated = isolate(image, [person.mask])
        mask.save(output / mask_name)
        isolated.save(output / isolated_name)
        x, y, width, height = person.bbox
        result["people"].append({
            "index": index, "confidence": person.confidence,
            "bbox": {"x": x, "y": y, "width": width, "height": height},
            "mask": mask_name, "isolated": isolated_name,
        })
    return result


@contextmanager
def encode_video(path: Path, width: int, height: int, fps: float, pixel_format: str):
    with TemporaryFile() as errors:
        process = subprocess.Popen([
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pixel_format", pixel_format,
            "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(path),
        ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
        try:
            yield process.stdin
            process.stdin.close()
            if process.wait(timeout=60) != 0:
                errors.seek(0)
                raise RuntimeError(f"Video encoding failed: {errors.read(2000).decode(errors='replace')}")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if not process.stdin.closed:
                process.stdin.close()
