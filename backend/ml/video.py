"""Bounded video segmentation with track identities scoped to one processing run."""

import json
import math
from pathlib import Path
import subprocess
from typing import TypedDict

import cv2
from PIL import Image

from backend.contracts import SegmentationResult
from backend.ml.artifacts import export_segmentation
from backend.ml.pipeline import Person, Segmenter


class VideoFrame(SegmentationResult):
    frame_index: int
    timestamp: float
    original: str


class VideoResult(TypedDict):
    frames: list[VideoFrame]
    track_ids: list[int]
    tracker: str


def probe_video(source: Path) -> dict:
    try:
        response = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=codec_name,width,height:format=duration,format_name", "-of", "json", str(source),
        ], capture_output=True, text=True, check=True, timeout=15)
        metadata = json.loads(response.stdout)
        stream = metadata["streams"][0]
        duration = float(metadata["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        if ("mp4" not in metadata["format"]["format_name"] or stream["codec_name"] != "h264"
                or not math.isfinite(duration) or not 0 < duration <= 600
                or min(width, height) <= 0 or width * height > 3840 * 2160):
            raise ValueError("Unsupported video")
    except (subprocess.SubprocessError, KeyError, IndexError, ValueError) as error:
        raise ValueError("Upload an H.264 MP4, up to 10 minutes and 3840 × 2160 pixels") from error
    return {"duration": duration, "width": width, "height": height}


def process_video(source: Path, output: Path, *, start: float, end: float, max_frames: int = 60,
                  model_path: Path = Path("models/yolov8n-seg.pt"), tracker: str = "botsort",
                  model_factory=Segmenter) -> VideoResult:
    metadata = probe_video(source)
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= metadata["duration"]
            and 1 <= max_frames <= 60 and tracker in {"botsort", "bytetrack"}):
        raise ValueError("Choose a valid video range, frame limit, and tracker")
    # A new model owns fresh tracker state; retries and other videos cannot inherit identities.
    model = model_factory(model_path).model
    capture = cv2.VideoCapture(str(source))
    frames, track_ids = [], set()
    interval = (end - start) / max_frames
    next_timestamp = start
    output.mkdir(parents=True, exist_ok=False)
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        while len(frames) < max_frames:
            success, pixels = capture.read()
            if not success:
                break
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if timestamp >= end:
                break
            if timestamp + 1e-6 < next_timestamp:
                continue
            image = Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)).convert("RGBA")
            prediction = model.track(image.convert("RGB"), persist=True, tracker=f"{tracker}.yaml",
                                     classes=[0], conf=0.1, retina_masks=True, device="cpu", verbose=False)[0]
            people, identities = [], []
            if prediction.boxes.id is not None:
                if prediction.masks is None:
                    raise RuntimeError("Tracked detections have no segmentation masks")
                for box, confidence, mask, identity in zip(
                    prediction.boxes.xyxy.cpu().numpy(), prediction.boxes.conf.cpu().numpy(),
                    prediction.masks.data.cpu().numpy(), prediction.boxes.id.cpu().numpy(), strict=True,
                ):
                    if mask.shape != (image.height, image.width):
                        raise RuntimeError("Video mask does not match source dimensions")
                    x1, y1, x2, y2 = map(float, box)
                    people.append(Person(float(confidence), (x1, y1, x2-x1, y2-y1), mask > 0.5))
                    identities.append(int(identity))
            prefix = f"frame-{len(frames):04d}"
            frame_output = output / prefix
            result = export_segmentation(image, people, frame_output)
            image.save(frame_output / "original.png")
            for person, identity in zip(result["people"], identities, strict=True):
                person["track_id"] = identity
                track_ids.add(identity)
                for key in ("mask", "isolated"):
                    person[key] = f"{prefix}/{person[key]}"
            for key in ("preview", "mask", "isolated"):
                result[key] = f"{prefix}/{result[key]}"
            frames.append(dict(result, frame_index=int(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1,
                               timestamp=timestamp, original=f"{prefix}/original.png"))
            next_timestamp = start + len(frames) * interval
    finally:
        capture.release()
    if not frames:
        raise ValueError("No decodable frames in the selected range")
    return {"frames": frames, "track_ids": sorted(track_ids), "tracker": tracker}
