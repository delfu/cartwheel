from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.api import create_app
from backend.contracts import VideoClaim
from backend.ml.video import process_video
from backend.worker import run_once


CLIP = Path(__file__).parents[1] / "data/man-and-child-25fps.mp4"


def upload_video(client):
    return client.post("/api/videos", files={"file": (CLIP.name, CLIP.read_bytes(), "video/mp4")})


def test_video_upload_playback_and_explicit_processing(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    uploaded = upload_video(client)
    assert uploaded.status_code == 201
    video = uploaded.json()
    assert video["runs"] == []
    assert app.state.store.claim() is None
    assert client.get("/api/videos").json()["items"][0]["id"] == video["id"]
    playback = client.get(video["original_url"], headers={"Range": "bytes=0-255"})
    assert playback.status_code == 206
    assert len(playback.content) == 256
    url = f"/api/videos/{video['id']}/runs"
    assert client.post(url, json={"start_seconds": 2, "end_seconds": 1}).status_code == 400
    assert client.post(url, json={"start_seconds": 0, "end_seconds": 1000}).status_code == 400
    assert client.post(url, json={"start_seconds": 0, "end_seconds": 1, "max_frames": 61}).status_code == 422
    run = client.post(url, json={"start_seconds": 0, "end_seconds": 0.2, "max_frames": 2}).json()
    assert run["status"] == "queued"
    assert client.post(url, json={"start_seconds": 0, "end_seconds": 1}).status_code == 400
    store = app.state.store
    claim = store.claim()
    assert isinstance(claim, VideoClaim)
    assert claim.start_seconds == 0
    assert store.heartbeat(claim)
    with store._transaction() as db:
        db.execute("UPDATE video_jobs SET lease_expires_at=0 WHERE id=?", (claim.job_id,))
    reclaimed = store.claim()
    assert reclaimed.token != claim.token
    assert not store.succeed(claim, {"frames": [], "track_ids": [], "tracker": "botsort"})
    assert store.fail(reclaimed, "retryable")
    with store._transaction() as db:
        db.execute("UPDATE video_jobs SET available_at=0 WHERE id=?", (claim.job_id,))

    def process(source, output, **options):
        from types import SimpleNamespace
        from unittest.mock import Mock
        factory = Mock(return_value=SimpleNamespace(model=Mock()))
        factory.return_value.model.track.return_value = [SimpleNamespace(boxes=SimpleNamespace(id=None))]
        return process_video(source, output, model_factory=factory, **options)

    assert run_once(store, lambda *_: pytest.fail("Video reached image processor"), process_video=process)
    finished = client.get(f"/api/video-runs/{run['id']}").json()
    assert finished["status"] == "succeeded"
    assert len(finished["result"]["frames"]) == 2
    frame = finished["result"]["frames"][0]
    assert client.get(frame["original_url"]).status_code == 200
    assert client.get(f"/api/video-runs/{run['id']}/artifacts/unlisted.png").status_code == 404
    response = client.post(f"/api/video-runs/{run['id']}/frames/0/cutout", json={"track_ids": []})
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert image.getchannel("A").getbbox() is None
    assert client.post(f"/api/video-runs/{run['id']}/frames/0/cutout", json={"track_ids": [999]}).status_code == 400
    reopened = TestClient(create_app(tmp_path))
    assert reopened.get(f"/api/videos/{video['id']}").json()["runs"][0]["status"] == "succeeded"


def test_invalid_video_is_not_persisted(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/videos", files={"file": ("bad.mp4", b"invalid")}).status_code == 400
    assert client.get("/api/videos").json()["items"] == []
    assert not list((tmp_path / "videos").glob("*/original.mp4"))


def test_track_selection_does_not_transfer_when_person_is_absent(tmp_path):
    import numpy as np
    from backend.ml.artifacts import export_segmentation
    from backend.ml.pipeline import Person

    app = create_app(tmp_path)
    client = TestClient(app)
    video = upload_video(client).json()
    client.post(f"/api/videos/{video['id']}/runs", json={"start_seconds": 0, "end_seconds": 1})
    store = app.state.store
    claim = store.claim()
    claim.output_dir.mkdir(parents=True)
    frames = []
    for number, track_id in enumerate((17, 8)):
        original = Image.new("RGBA", (8, 6), "red")
        mask = np.ones((6, 8), dtype=bool)
        prefix = f"frame-{number:04d}"
        folder = claim.output_dir / prefix
        frame = export_segmentation(original, [Person(0.9, (0, 0, 8, 6), mask)], folder)
        original.save(folder / "original.png")
        frame.update(original="original.png", timestamp=number * 0.5, frame_index=number)
        for key in ("original", "preview", "mask", "isolated"):
            frame[key] = f"{prefix}/{frame[key]}"
        for person in frame["people"]:
            person.update(track_id=track_id, mask=f"{prefix}/{person['mask']}",
                          isolated=f"{prefix}/{person['isolated']}")
        frames.append(frame)
    assert store.succeed(claim, {"frames": frames, "track_ids": [8, 17], "tracker": "botsort"})
    for frame_number, should_be_visible in ((0, True), (1, False)):
        response = client.post(f"/api/video-runs/{claim.job_id}/frames/{frame_number}/cutout",
                               json={"track_ids": [17]})
        assert response.status_code == 200
        with Image.open(BytesIO(response.content)) as image:
            assert bool(image.getchannel("A").getbbox()) is should_be_visible
    assert client.post(f"/api/jobs/{claim.job_id}/retry").status_code == 409


def test_video_failure_exhaustion_and_manual_retry(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    video = upload_video(client).json()
    run = client.post(f"/api/videos/{video['id']}/runs", json={"start_seconds": 0, "end_seconds": 1}).json()
    store = app.state.store
    for _ in range(3):
        claim = store.claim()
        assert store.fail(claim, "test failure")
        with store._transaction() as db:
            db.execute("UPDATE video_jobs SET available_at=0 WHERE id=?", (claim.job_id,))
    assert store.get_job(run["id"])["status"] == "failed"
    assert store.claim() is None
    retry = client.post(f"/api/jobs/{run['id']}/retry")
    assert retry.status_code == 202
    assert retry.json()["id"] != run["id"]
    assert len(store.get_video(video["id"])["runs"]) == 2
