import pytest

from backend.ml.artifacts import encode_video


def test_encoder_reports_failure_instead_of_publishing_invalid_output(tmp_path):
    with pytest.raises(RuntimeError, match="Video encoding failed"):
        with encode_video(tmp_path / "invalid.mp4", 0, 0, 25, "gray"):
            pass
