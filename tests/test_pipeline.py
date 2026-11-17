from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest
from PIL import Image

from backend.ml.pipeline import Segmenter, isolate, load_image


def test_isolate_unions_masks_and_preserves_source_alpha():
    image = Image.new("RGBA", (3, 2), (100, 150, 200, 128))
    first = np.array([[True, False, False], [False, False, False]])
    second = np.array([[True, True, False], [False, False, False]])
    mask, result = isolate(image, [first, second])
    assert mask.mode == "L"
    assert np.array(mask).tolist() == [[255, 255, 0], [0, 0, 0]]
    assert result.getpixel((1, 0)) == (100, 150, 200, 128)
    assert result.getpixel((2, 0)) == (0, 0, 0, 0)


def test_no_people_produces_empty_mask_and_transparent_image():
    mask, result = isolate(Image.new("RGB", (3, 2), "white"), [])
    assert mask.getbbox() is None
    assert result.getchannel("A").getbbox() is None


@pytest.mark.parametrize("mask", [np.ones((2, 2), dtype=bool), np.ones((2, 3), dtype=np.uint8)])
def test_rejects_invalid_masks(mask):
    with pytest.raises(ValueError, match="boolean arrays matching"):
        isolate(Image.new("RGB", (3, 2)), [mask])


@pytest.mark.parametrize("suffix", [".jpg", ".png"])
def test_loads_png_and_jpeg_at_original_size(tmp_path, suffix):
    path = tmp_path / f"image{suffix}"
    Image.new("RGB", (37, 19)).save(path)
    image = load_image(path)
    assert image.size == (37, 19)
    assert image.mode == "RGBA"


def test_applies_exif_orientation(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (7, 3)).save(path, exif=exif)
    assert load_image(path).size == (3, 7)


def test_rejects_unsupported_image_format(tmp_path):
    path = tmp_path / "image.gif"
    Image.new("RGB", (3, 2)).save(path)
    with pytest.raises(ValueError, match="PNG or JPEG"):
        load_image(path)


def test_rejects_corrupt_image(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    with pytest.raises(OSError):
        load_image(path)


def test_rejects_large_files_before_decoding(tmp_path):
    path = tmp_path / "large.jpg"
    with path.open("wb") as file:
        file.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="25 MiB"):
        load_image(path)


def tensor(values):
    return SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: np.array(values)))


def test_segment_requests_person_masks_in_source_coordinates():
    segmenter = object.__new__(Segmenter)
    boxes = MagicMock()
    boxes.__len__.return_value = 1
    boxes.xyxy = tensor([[1, 0, 3, 2]])
    boxes.conf = tensor([0.9])
    result = SimpleNamespace(boxes=boxes, masks=SimpleNamespace(data=tensor([[[0, 1, 1], [0, 1, 0]]])))
    segmenter.model = Mock()
    segmenter.model.predict.return_value = [result]
    people = segmenter.segment(Image.new("RGB", (3, 2)))
    assert len(people) == 1
    assert people[0].bbox == (1, 0, 2, 2)
    assert people[0].mask.dtype == bool
    assert people[0].mask.shape == (2, 3)
    assert segmenter.model.predict.call_args.kwargs["classes"] == [0]
    assert segmenter.model.predict.call_args.kwargs["retina_masks"] is True


def test_segment_handles_no_detections():
    segmenter = object.__new__(Segmenter)
    segmenter.model = Mock()
    segmenter.model.predict.return_value = [SimpleNamespace(boxes=[], masks=None)]
    assert segmenter.segment(Image.new("RGB", (3, 2))) == []


@pytest.mark.parametrize("masks", [None, SimpleNamespace(data=tensor([[[1]]]))])
def test_segment_rejects_missing_or_misaligned_masks(masks):
    segmenter = object.__new__(Segmenter)
    boxes = MagicMock()
    boxes.__len__.return_value = 1
    boxes.xyxy = tensor([[0, 0, 3, 2]])
    boxes.conf = tensor([0.9])
    segmenter.model = Mock()
    segmenter.model.predict.return_value = [SimpleNamespace(boxes=boxes, masks=masks)]
    with pytest.raises(RuntimeError):
        segmenter.segment(Image.new("RGB", (3, 2)))
