from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.api import create_app
from backend.contracts import VideoClaim
from backend.ml.video import process_video
from backend.store import Store
from backend.worker import run_once


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x24:r=5:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def upload_video(client, clip):
    response = client.post("/api/videos", files={"file": (clip.name, clip.read_bytes(), "video/mp4")})
    assert response.status_code == 201
    return response.json()


def test_full_video_upload_queue_seekable_outputs_and_recovery(tmp_path, clip):
    app = create_app(tmp_path / "storage")
    client = TestClient(app)
    video = upload_video(client, clip)
    run = video["runs"][0]
    assert run["status"] == "queued"
    assert run["mode"] == "full"
    assert client.get(video["original_url"], headers={"Range": "bytes=0-255"}).status_code == 206
    url = f"/api/videos/{video['id']}/runs"
    assert client.post(url, json={"start_seconds": 0, "end_seconds": 1}).status_code == 422
    assert client.post(url, json={}).status_code == 400
    store = app.state.store
    claim = store.claim()
    assert isinstance(claim, VideoClaim)
    assert store.heartbeat(claim)
    assert store.update_video_progress(claim, 2, 5)
    with store._transaction() as db:
        db.execute("UPDATE video_jobs SET lease_expires_at=0 WHERE id=?", (claim.job_id,))
    reclaimed = store.claim()
    assert reclaimed.token != claim.token
    assert store.get_job(claim.job_id)["processed_frames"] == 0
    assert not store.update_video_progress(claim, 3, 5)
    assert not store.succeed(claim, {})
    assert store.fail(reclaimed, "retryable")
    with store._transaction() as db:
        db.execute("UPDATE video_jobs SET available_at=0 WHERE id=?", (claim.job_id,))

    def process(source, output, **options):
        import torch
        factory = Mock(return_value=SimpleNamespace(model=Mock()))
        factory.return_value.model.track.return_value = [SimpleNamespace(
            boxes=SimpleNamespace(id=torch.tensor([17])), masks=SimpleNamespace(data=torch.ones((1,24,32))))]
        return process_video(source, output, model_factory=factory, **options)

    assert run_once(store, lambda *_: pytest.fail("Video reached image processor"), process_video=process)
    finished = client.get(f"/api/video-runs/{run['id']}").json()
    assert finished["status"] == "succeeded"
    assert finished["processed_frames"] == finished["total_frames"] == 5
    assert finished["result"]["frame_count"] == 5
    playback = client.get(finished["result"]["playback_url"], headers={"Range": "bytes=0-255"})
    assert playback.status_code == 206
    assert playback.headers["content-type"] == "video/mp4"
    mask = client.get(finished["result"]["tracks"][0]["mask_video_url"], headers={"Range": "bytes=0-255"})
    assert mask.status_code == 206
    assert mask.headers["content-type"] == "video/mp4"
    assert client.get(f"/api/video-runs/{run['id']}/artifacts/unlisted.mp4").status_code == 404
    reopened = TestClient(create_app(tmp_path / "storage"))
    assert reopened.get(f"/api/videos/{video['id']}").json()["runs"][0]["status"] == "succeeded"


def test_invalid_video_is_not_persisted(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/videos", files={"file": ("bad.mp4", b"invalid")}).status_code == 400
    assert client.get("/api/videos").json()["items"] == []
    assert not list((tmp_path / "videos").glob("*/original.mp4"))


def test_video_failure_exhaustion_and_manual_retry(tmp_path, clip):
    app = create_app(tmp_path / "storage")
    client = TestClient(app)
    video = upload_video(client, clip)
    run = video["runs"][0]
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


def test_old_sampled_jobs_are_fenced_without_removing_video(tmp_path, clip):
    store = Store(tmp_path / "storage")
    client = TestClient(create_app(store.root))
    video = upload_video(client, clip)
    claim = store.claim()
    with store._transaction() as db:
        db.execute("ALTER TABLE video_jobs DROP COLUMN mode")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        reopened = list(pool.map(lambda _: Store(store.root), range(3)))[0]
    assert reopened.get_job(claim.job_id)["status"] == "failed"
    assert not reopened.succeed(claim, {})
    assert reopened.get_video(video["id"])["runs"] == []
    assert client.get(video["original_url"]).status_code == 200
    assert reopened.create_video_run(video["id"])["status"] == "queued"
