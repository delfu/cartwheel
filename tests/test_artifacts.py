from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

from backend.ml.artifacts import process_image
from backend.ml.pipeline import Person


def test_exports_person_masks_and_preserves_source_pixels(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (5, 3), (100, 150, 200, 128)).save(source)
    first = np.zeros((3, 5), dtype=bool)
    first[0, 0] = True
    second = np.zeros((3, 5), dtype=bool)
    second[1, 2] = True
    segmenter = Mock()
    segmenter.segment.return_value = [Person(0.9, (0, 0, 1, 1), first), Person(0.8, (2, 1, 1, 1), second)]
    output = tmp_path / "output"

    result = process_image(source, output, segmenter)

    assert (result["width"], result["height"]) == (5, 3)
    assert [person["index"] for person in result["people"]] == [1, 2]
    assert result["people"][1]["bbox"] == {"x": 2, "y": 1, "width": 1, "height": 1}
    assert result["people"][0]["confidence"] == 0.9
    segmenter.segment.assert_called_once()
    assert segmenter.segment.call_args.args[0].size == (5, 3)
    for name in (result["preview"], result["mask"], result["isolated"]):
        with Image.open(output / name) as image:
            assert image.size == (5, 3)
    for person, expected in zip(result["people"], (first, second), strict=True):
        with Image.open(output / person["mask"]) as image:
            assert image.mode == "L"
            assert np.array_equal(np.array(image), expected.astype(np.uint8) * 255)
        with Image.open(output / person["isolated"]) as image:
            pixels = np.array(image)
            assert image.mode == "RGBA"
            assert np.all(pixels[expected] == (100, 150, 200, 128))
            assert np.all(pixels[~expected] == 0)
    with Image.open(output / result["mask"]) as image:
        assert np.array_equal(np.array(image), (first | second).astype(np.uint8) * 255)
    assert not (output / "original.png").exists()


def test_no_people_exports_transparent_result_without_individual_masks(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (9, 7), "white").save(source)
    segmenter = Mock()
    segmenter.segment.return_value = []
    output = tmp_path / "output"

    result = process_image(source, output, segmenter)

    assert result["people"] == []
    assert {path.name for path in output.iterdir()} == {"preview.png", "mask.png", "isolated.png"}
    with Image.open(output / result["mask"]) as image:
        assert image.getbbox() is None
    with Image.open(output / result["isolated"]) as image:
        assert image.getchannel("A").getbbox() is None


def test_processing_failure_does_not_create_output(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (5, 3)).save(source)
    segmenter = Mock()
    segmenter.segment.side_effect = RuntimeError("inference failed")
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="inference failed"):
        process_image(source, output, segmenter)
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (5, 3)).save(source)
    segmenter = Mock()
    segmenter.segment.return_value = []
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "preview.png"
    sentinel.write_bytes(b"existing output")
    with pytest.raises(FileExistsError):
        process_image(source, output, segmenter)
    assert sentinel.read_bytes() == b"existing output"
