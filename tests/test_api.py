from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from backend.api import create_app


def upload(client, size=(30, 20)):
    buffer = BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return client.post("/api/images", files={"file": ("photo.png", buffer.getvalue(), "image/png")})


def test_upload_gallery_original_and_durable_job_status(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = upload(client)
    assert response.status_code == 202
    image = response.json()
    assert image["status"] == "queued"
    assert image["job"]["status"] == "queued"
    assert image["people"] == []
    assert image["mask_url"] is None
    assert client.get(image["original_url"]).status_code == 200
    with Image.open(BytesIO(client.get(image["original_url"]).content)) as original:
        assert original.size == (30, 20)
    reopened = TestClient(create_app(tmp_path))
    assert reopened.get(f"/api/jobs/{image['job']['id']}").json()["attempts"] == 0
    assert reopened.get("/api/images").json()["items"][0]["id"] == image["id"]
    assert reopened.get(f"/api/images/{image['id']}").json()["filename"] == "photo.png"


def test_rejects_invalid_image_and_request(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/images", files={"file": ("image.png", b"garbage", "image/png")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "request_failed"
    assert response.json()["error"]["message"] == "Upload a valid PNG or JPEG image."
    assert client.get("/api/images").json()["items"] == []
    assert client.get("/api/images?limit=0").status_code == 422
    assert client.get("/api/images?cursor=unknown").status_code == 400
    assert client.get("/api/jobs/unknown").status_code == 404


def test_rejects_oversized_upload(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/images", files={"file": ("large.png", b"0" * (25 * 1024 * 1024 + 1))})
    assert response.status_code == 413
    assert client.get("/api/images").json()["items"] == []


def test_limits_chunked_request_before_multipart_spooling_can_grow_unbounded(tmp_path):
    client = TestClient(create_app(tmp_path))
    chunks = iter([
        b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.png"\r\n\r\n',
        *[b"0" * (1024 * 1024)] * 27,
        b"\r\n--boundary--\r\n",
    ])
    response = client.post("/api/images", content=chunks,
                           headers={"Content-Type": "multipart/form-data; boundary=boundary"})
    assert response.status_code == 413
    assert "26 MiB" in response.json()["error"]["message"]
    assert client.get("/api/images").json()["items"] == []


def test_unfinished_artifacts_hidden_and_completed_artifacts_exposed(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    image = upload(client).json()
    url = f"/api/images/{image['id']}/artifacts/mask.png"
    assert client.get(url).status_code == 404
    claim = app.state.store.claim()
    claim.output_dir.mkdir(parents=True)
    Image.new("L", (30, 20)).save(claim.output_dir / "mask.png")
    assert client.get(url).status_code == 404
    app.state.store.succeed(claim, {"width": 30, "height": 20, "preview": "preview.png",
                                  "mask": "mask.png", "isolated": "isolated.png", "people": []})
    result = client.get(f"/api/images/{image['id']}").json()
    assert result["status"] == "ready"
    assert result["mask_url"] == url
    assert client.get(url).status_code == 200
    assert client.get(f"/api/images/{image['id']}/artifacts/unlisted.png").status_code == 404


def test_retry_rejects_non_failed_jobs(tmp_path):
    client = TestClient(create_app(tmp_path))
    image = upload(client).json()
    response = client.post(f"/api/jobs/{image['job']['id']}/retry")
    assert response.status_code == 409


def test_delete_removes_image_jobs_and_files_without_affecting_other_images(tmp_path):
    from backend.ml.artifacts import export_segmentation
    from backend.worker import run_once

    app = create_app(tmp_path)
    client = TestClient(app)
    image = upload(client).json()
    other = upload(client).json()
    store = app.state.store
    # Include an older failed attempt and a successful retry in the deletion.
    claim = store.claim()
    claim.output_dir.mkdir(parents=True)
    (claim.output_dir / "partial.png").write_bytes(b"partial")
    with store._transaction() as db:
        db.execute("UPDATE jobs SET max_attempts=1 WHERE id=?", (claim.job_id,))
    assert store.fail(claim, "failed attempt")
    retried = store.retry(claim.job_id)

    def process(source, output):
        with Image.open(source) as original:
            return export_segmentation(original, [], output)

    while run_once(store, process):
        pass
    mask_url = client.get(f"/api/images/{image['id']}").json()["mask_url"]
    assert client.get(mask_url).status_code == 200
    response = client.delete(f"/api/images/{image['id']}")
    assert response.status_code == 204
    assert response.content == b""
    for url in (f"/api/images/{image['id']}", image["original_url"], mask_url,
                f"/api/jobs/{claim.job_id}", f"/api/jobs/{retried['id']}"):
        assert client.get(url).status_code == 404
    assert client.post(f"/api/jobs/{claim.job_id}/retry").status_code == 404
    assert not store.original_path(image["id"]).parent.exists()
    assert not (tmp_path / "jobs" / claim.job_id).exists()
    assert not (tmp_path / "jobs" / retried["id"]).exists()
    gallery = client.get("/api/images").json()
    assert gallery["total"] == 1
    assert gallery["items"][0]["id"] == other["id"]
    assert client.get(other["original_url"]).status_code == 200
    assert client.delete(f"/api/images/{image['id']}").status_code == 404


def test_delete_queued_image_prevents_processing(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    image = upload(client).json()
    assert client.delete(f"/api/images/{image['id']}").status_code == 204
    assert app.state.store.claim() is None
    assert client.get("/api/images").json() == {"items": [], "next_cursor": None, "total": 0}
