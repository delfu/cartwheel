from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image
import pytest
import torch

from backend.ml.video import probe_video, process_video


CLIP = Path(__file__).parents[1] / "data/man-and-child-25fps.mp4"


def test_video_range_keeps_track_ids_when_detection_order_changes(tmp_path):
    model = Mock()
    calls = []

    def track(image, **options):
        calls.append(options)
        ids = [17, 8] if len(calls) == 1 else ([8] if len(calls) == 2 else [8, 17])
        masks = torch.zeros((len(ids), image.height, image.width))
        masks[:, :5, :5] = 1
        boxes = SimpleNamespace(id=torch.tensor(ids), xyxy=torch.tensor([[0, 0, 5, 5]] * len(ids)),
                                conf=torch.tensor([0.9] * len(ids)))
        return [SimpleNamespace(boxes=boxes, masks=SimpleNamespace(data=masks))]

    model.track.side_effect = track
    factory = Mock(return_value=SimpleNamespace(model=model))
    result = process_video(CLIP, tmp_path / "run", start=1, end=1.6, max_frames=3, model_factory=factory)
    frames = result["frames"]
    assert len(frames) == 3
    assert result["track_ids"] == [8, 17]
    assert [[p["track_id"] for p in frame["people"]] for frame in frames] == [[17, 8], [8], [8, 17]]
    assert all(1 <= frame["timestamp"] < 1.6 for frame in frames)
    assert frames[0]["timestamp"] < frames[1]["timestamp"] < frames[2]["timestamp"]
    assert all(call["persist"] and call["tracker"] == "botsort.yaml" for call in calls)
    factory.assert_called_once()
    with Image.open(tmp_path / "run" / frames[1]["people"][0]["mask"]) as mask:
        assert mask.size == (1920, 1080)


def test_video_negative_frames_and_invalid_ranges(tmp_path):
    factory = Mock(return_value=SimpleNamespace(model=Mock()))
    factory.return_value.model.track.return_value = [SimpleNamespace(boxes=SimpleNamespace(id=None))]
    result = process_video(CLIP, tmp_path / "negative", start=0, end=0.2, max_frames=2, model_factory=factory)
    assert result["track_ids"] == []
    with Image.open(tmp_path / "negative" / result["frames"][0]["isolated"]) as image:
        assert image.getchannel("A").getbbox() is None
    with pytest.raises(ValueError, match="valid video range"):
        process_video(CLIP, tmp_path / "invalid", start=2, end=1, model_factory=factory)
    invalid = tmp_path / "invalid.mp4"
    invalid.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="H.264 MP4"):
        probe_video(invalid)
