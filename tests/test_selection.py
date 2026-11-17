from io import BytesIO

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from backend.api import create_app
from backend.ml.artifacts import export_segmentation
from backend.ml.pipeline import Person


@pytest.fixture
def ready_image(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    source = Image.new("RGBA", (4, 3), (100, 150, 200, 128))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    image_id = client.post("/api/images", files={"file": ("people.png", buffer.getvalue())}).json()["id"]
    first = np.zeros((3, 4), dtype=bool)
    first[0, 0] = True
    second = np.zeros((3, 4), dtype=bool)
    second[1, 2] = True
    claim = app.state.store.claim()
    result = export_segmentation(source, [Person(0.9, (0, 0, 1, 1), first),
                                          Person(0.8, (2, 1, 1, 1), second)], claim.output_dir)
    assert app.state.store.succeed(claim, result)
    return client, image_id


@pytest.mark.parametrize("indices", [[], [1], [2], [1, 2]])
def test_selection_exports_only_chosen_people_at_original_resolution(ready_image, indices):
    client, image_id = ready_image
    for view in ("mask", "isolated"):
        response = client.post(f"/api/images/{image_id}/cutout?view={view}", json={"person_indices": indices})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (4, 3)
            if view == "mask":
                assert image.mode == "L"
                assert image.getpixel((0, 0)) == (255 if 1 in indices else 0)
                assert image.getpixel((2, 1)) == (255 if 2 in indices else 0)
            else:
                assert image.mode == "RGBA"
                assert image.getpixel((0, 0)) == ((100, 150, 200, 128) if 1 in indices else (0, 0, 0, 0))
                assert image.getpixel((2, 1)) == ((100, 150, 200, 128) if 2 in indices else (0, 0, 0, 0))
                assert image.getpixel((3, 2)) == (0, 0, 0, 0)


@pytest.mark.parametrize("indices,status", [([99], 400), ([1, 1], 400), ([True], 422), ([1.5], 422)])
def test_selection_rejects_unknown_duplicate_or_invalid_indices(ready_image, indices, status):
    client, image_id = ready_image
    response = client.post(f"/api/images/{image_id}/cutout", json={"person_indices": indices})
    assert response.status_code == status


def test_selection_requires_completed_processing(tmp_path):
    client = TestClient(create_app(tmp_path))
    buffer = BytesIO()
    Image.new("RGB", (4, 3)).save(buffer, format="PNG")
    image_id = client.post("/api/images", files={"file": ("queued.png", buffer.getvalue())}).json()["id"]
    assert client.post(f"/api/images/{image_id}/cutout", json={"person_indices": []}).status_code == 409
