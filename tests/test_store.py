from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import pytest

from backend.store import Store


def enqueue(store):
    image_id = str(uuid4())
    source = store.original_path(image_id)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    return store.create_image(image_id, "photo.jpg", 30, 20)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("backend.store.time.time", lambda: now[0])
    return now


def test_upload_and_job_survive_reopening(tmp_path):
    store = Store(tmp_path)
    image = enqueue(store)
    reopened = Store(tmp_path)
    assert reopened.get_image(image["id"])["job"]["id"] == image["job"]["id"]
    assert reopened.claim().image_id == image["id"]


def test_only_one_worker_claims_a_job(tmp_path):
    stores = [Store(tmp_path), Store(tmp_path)]
    enqueue(stores[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda store: store.claim(), stores))
    assert sum(claim is not None for claim in claims) == 1
    assert not next(claim for claim in claims if claim).output_dir.exists()


def test_expired_worker_cannot_heartbeat_finish_or_fail(tmp_path, clock):
    store = Store(tmp_path)
    image = enqueue(store)
    original = store.claim(lease_seconds=10)
    clock[0] += 11
    replacement = store.claim(lease_seconds=10)
    assert replacement.job_id == original.job_id
    assert replacement.token != original.token
    assert replacement.output_dir != original.output_dir
    assert not store.heartbeat(original)
    assert not store.succeed(original, {})
    assert not store.fail(original, "stale")
    assert store.get_job(image["job"]["id"])["attempts"] == 2
    assert store.heartbeat(replacement)


def test_failures_back_off_and_stop_then_allow_manual_retry(tmp_path, clock):
    store = Store(tmp_path)
    image = enqueue(store)
    for attempt in range(1, 4):
        claim = store.claim()
        assert claim is not None
        assert store.fail(claim, "model unavailable")
        job = store.get_job(claim.job_id)
        assert job["attempts"] == attempt
        assert job["status"] == ("failed" if attempt == 3 else "queued")
        assert store.claim() is None
        clock[0] += 2 ** (attempt - 1)
    retried = store.retry(image["job"]["id"])
    assert retried["id"] != image["job"]["id"]
    assert retried["attempts"] == 0
    assert store.get_job(image["job"]["id"])["status"] == "failed"
    assert store.get_image(image["id"])["job"]["id"] == retried["id"]
    with pytest.raises(ValueError, match="active processing"):
        store.retry(image["job"]["id"])


def test_repeated_worker_crashes_exhaust_attempts(tmp_path, clock):
    store = Store(tmp_path)
    image = enqueue(store)
    for _ in range(3):
        assert store.claim(lease_seconds=1) is not None
        clock[0] += 2
    assert store.claim() is None
    assert store.get_job(image["job"]["id"])["status"] == "failed"


def test_gallery_pagination_has_no_duplicates_for_equal_timestamps(tmp_path, clock):
    store = Store(tmp_path)
    ids = {enqueue(store)["id"] for _ in range(3)}
    first = store.list_images(limit=2)
    second = store.list_images(limit=2, cursor=first["next_cursor"])
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2
    assert second["next_cursor"] is None
    assert {image["id"] for image in first["items"] + second["items"]} == ids


def test_empty_gallery_includes_total(tmp_path):
    assert Store(tmp_path).list_images() == {"items": [], "next_cursor": None, "total": 0}


def test_only_successfully_published_artifacts_are_readable(tmp_path):
    store = Store(tmp_path)
    image = enqueue(store)
    claim = store.claim()
    claim.output_dir.mkdir(parents=True)
    (claim.output_dir / "mask.png").write_bytes(b"mask")
    (claim.output_dir / "secret.png").write_bytes(b"secret")
    with pytest.raises(KeyError):
        store.artifact_path(image["id"], "mask.png")
    manifest = {"width": 30, "height": 20, "preview": "preview.png", "mask": "mask.png",
                "isolated": "isolated.png", "people": []}
    assert store.succeed(claim, manifest)
    assert Store(tmp_path).artifact_path(image["id"], "mask.png").read_bytes() == b"mask"
    assert store.get_image(image["id"])["status"] == "ready"
    with pytest.raises(KeyError):
        store.artifact_path(image["id"], "secret.png")
    with pytest.raises(KeyError):
        store.artifact_path(image["id"], "../mask.png")


def test_worker_cannot_publish_if_lease_expires_while_waiting_for_database_lock(tmp_path):
    store = Store(tmp_path)
    enqueue(store)
    claim = store.claim(lease_seconds=0.15)
    with sqlite3.connect(store.database, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(store.succeed, claim, {})
            time.sleep(0.2)
            blocker.commit()
            assert result.result() is False


def test_completed_results_survive_moving_the_storage_directory(tmp_path):
    original_root = tmp_path / "original"
    store = Store(original_root)
    image = enqueue(store)
    claim = store.claim()
    claim.output_dir.mkdir(parents=True)
    (claim.output_dir / "mask.png").write_bytes(b"mask")
    assert store.succeed(claim, {
        "width": 30, "height": 20, "preview": "preview.png", "mask": "mask.png",
        "isolated": "isolated.png", "people": [],
    })
    relocated_root = tmp_path / "relocated"
    original_root.rename(relocated_root)
    reopened = Store(relocated_root)
    assert reopened.original_path(image["id"]).read_bytes() == b"source"
    assert reopened.artifact_path(image["id"], "mask.png").read_bytes() == b"mask"
