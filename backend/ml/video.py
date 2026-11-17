"""Full-clip person tracking and synchronized playback/mask videos."""

from collections.abc import Callable
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import cv2
import numpy as np
from PIL import Image

from backend.contracts import VideoResult
from backend.ml.artifacts import encode_video
from backend.ml.pipeline import Segmenter


def probe_video(source: Path) -> dict:
    try:
        response = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,nb_frames:format=duration,format_name", "-of", "json", str(source),
        ], capture_output=True, text=True, check=True, timeout=15)
        metadata = json.loads(response.stdout)
        stream = metadata["streams"][0]
        duration = float(metadata["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        frame_count = int(stream["nb_frames"])
        if ("mp4" not in metadata["format"]["format_name"] or stream["codec_name"] != "h264"
                or not math.isfinite(duration) or not 0 < duration <= 600
                or min(width, height) <= 0 or width * height > 3840 * 2160
                or width % 2 or height % 2 or not 0 < fps <= 120 or frame_count < 1):
            raise ValueError("Unsupported video")
    except (subprocess.SubprocessError, KeyError, IndexError, ValueError, ZeroDivisionError) as error:
        raise ValueError("Upload an H.264 MP4, up to 10 minutes and 3840 × 2160 pixels, with even dimensions") from error
    return {"duration": duration, "width": width, "height": height, "fps": fps, "frame_count": frame_count}


def process_video(source: Path, output: Path, *, model_path: Path = Path("models/yolov8n-seg.pt"),
                  tracker: str = "botsort", model_factory=Segmenter,
                  on_progress: Callable[[int, int], None] | None = None) -> VideoResult:
    metadata = probe_video(source)
    if tracker not in {"botsort", "bytetrack"}:
        raise ValueError("Choose botsort or bytetrack")
    # Each attempt has fresh tracker state; identities never leak between clips or retries.
    model = model_factory(model_path).model
    capture = cv2.VideoCapture(str(source))
    output.mkdir(parents=True, exist_ok=False)
    # shortcut: normalize to constant frame rate and omit audio; preserve source timing/audio for exports later.
    width, height, fps = metadata["width"], metadata["height"], metadata["fps"]
    tracks: dict[int, VideoTrack] = {}
    frame_count = 0
    try:
        with TemporaryDirectory(dir=output) as temporary:
            masks = Path(temporary)
            with encode_video(output / "playback.mp4", width, height, fps, "rgb24") as playback:
                while True:
                    success, pixels = capture.read()
                    if not success:
                        break
                    if pixels.shape[:2] != (height, width):
                        raise ValueError("Rotated video is not supported in this proof of concept")
                    rgb = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
                    prediction = model.track(Image.fromarray(rgb), persist=True, tracker=f"{tracker}.yaml",
                                             classes=[0], conf=0.1, retina_masks=True, device="cpu", verbose=False)[0]
                    playback.write(rgb.tobytes())
                    if prediction.boxes.id is not None:
                        if prediction.masks is None:
                            raise RuntimeError("Tracked detections have no segmentation masks")
                        for identity, mask in zip(prediction.boxes.id.cpu().numpy(),
                                                  prediction.masks.data.cpu().numpy(), strict=True):
                            if mask.shape != (height, width):
                                raise RuntimeError("Video mask does not match source dimensions")
                            identity = int(identity)
                            track = tracks.setdefault(identity, {
                                "track_id": identity, "mask_video": f"person-{identity:04d}.mp4",
                                "first_frame": frame_count, "last_frame": frame_count,
                            })
                            track["last_frame"] = frame_count
                            Image.fromarray((mask > 0.5).astype(np.uint8) * 255).save(
                                masks / f"{identity}-{frame_count}.png")
                    frame_count += 1
                    if on_progress and (frame_count == 1 or frame_count % 10 == 0):
                        on_progress(frame_count, metadata["frame_count"])
            if frame_count != metadata["frame_count"]:
                raise ValueError("Decoded frame count does not match the video; refusing incomplete output")
            if on_progress:
                on_progress(frame_count, frame_count)
            black = bytes(width * height)
            for identity, track in tracks.items():
                with encode_video(output / track["mask_video"], width, height, fps, "gray") as encoded:
                    for index in range(frame_count):
                        path = masks / f"{identity}-{index}.png"
                        if path.exists():
                            with Image.open(path) as image:
                                encoded.write(image.tobytes())
                        else:
                            encoded.write(black)
    finally:
        capture.release()
    return {"playback": "playback.mp4", "width": width, "height": height, "fps": fps,
            "frame_count": frame_count, "tracks": sorted(tracks.values(), key=lambda item: item["track_id"]),
            "tracker": tracker}
