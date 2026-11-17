from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
import torch

from backend.ml.video import probe_video, process_video


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x24:r=5:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def decoded_frames(path):
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        success, frame = capture.read()
        if not success:
            break
        frames.append(frame)
    capture.release()
    return frames


def test_full_video_masks_keep_ids_and_cover_absent_frames(clip, tmp_path):
    model = Mock()
    count = 0

    def track(image, **options):
        nonlocal count
        count += 1
        ids = {1: [17], 2: [8], 3: [8, 17], 4: [], 5: [17]}[count]
        masks = torch.zeros((len(ids), image.height, image.width))
        masks[:, :8, :8] = 1
        return [SimpleNamespace(boxes=SimpleNamespace(id=torch.tensor(ids) if ids else None),
                                masks=SimpleNamespace(data=masks))]

    model.track.side_effect = track
    factory = Mock(return_value=SimpleNamespace(model=model))
    progress = Mock()
    output = tmp_path / "run"
    result = process_video(clip, output, model_factory=factory, on_progress=progress)
    assert result["frame_count"] == model.track.call_count == 5
    assert result["fps"] == 5
    assert [track["track_id"] for track in result["tracks"]] == [8, 17]
    progress.assert_called_with(5, 5)
    factory.assert_called_once()
    assert len(decoded_frames(output / result["playback"])) == 5
    for identity, visibility in ((8, [False, True, True, False, False]), (17, [True, False, True, False, True])):
        frames = decoded_frames(output / f"person-{identity:04d}.mp4")
        assert len(frames) == 5
        assert [bool((frame > 128).any()) for frame in frames] == visibility
    assert {path.name for path in output.iterdir()} == {"playback.mp4", "person-0008.mp4", "person-0017.mp4"}


def test_negative_video_and_invalid_inputs(clip, tmp_path):
    factory = Mock(return_value=SimpleNamespace(model=Mock()))
    factory.return_value.model.track.return_value = [SimpleNamespace(boxes=SimpleNamespace(id=None))]
    result = process_video(clip, tmp_path / "negative", model_factory=factory)
    assert result["tracks"] == []
    assert result["frame_count"] == 5
    assert len(decoded_frames(tmp_path / "negative/playback.mp4")) == 5
    invalid = tmp_path / "invalid.mp4"
    invalid.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="H.264 MP4"):
        probe_video(invalid)
    with pytest.raises(ValueError, match="botsort"):
        process_video(clip, tmp_path / "invalid", tracker="unknown", model_factory=factory)
