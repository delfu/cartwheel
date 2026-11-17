from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

from PIL import Image

from backend.ml.artifacts import export_segmentation
from backend.store import Store
from backend.worker import run_once


def enqueue(store):
    image_id = str(uuid4())
    source = store.original_path(image_id)
    source.parent.mkdir(parents=True)
    Image.new("RGB", (8, 6), "white").save(source)
    return store.create_image(image_id, "image.png", 8, 6)["job"]["id"]


def process(source, output):
    with Image.open(source) as image:
        return export_segmentation(image, [], output)


def test_worker_publishes_complete_result_and_survives_store_restart(tmp_path):
    store = Store(tmp_path)
    job_id = enqueue(store)
    assert run_once(store, process)
    job = Store(tmp_path).get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["result"]["people"] == []
    assert store.artifact_path(job["image_id"], "isolated.png").is_file()
    assert not run_once(store, process)


def test_worker_retries_failure_and_removes_partial_artifacts(tmp_path):
    store = Store(tmp_path)
    job_id = enqueue(store)
    outputs = []

    def broken(source, output):
        outputs.append(output)
        output.mkdir(parents=True)
        (output / "preview.png").write_bytes(b"partial")
        raise RuntimeError("temporary inference failure")

    assert run_once(store, broken)
    job = store.get_job(job_id)
    assert job["status"] == "queued"
    assert job["attempts"] == 1
    assert "temporary inference failure" in job["error"]
    assert not outputs[0].exists()


def test_heartbeat_keeps_long_processing_claim_alive(tmp_path):
    store = Store(tmp_path)
    job_id = enqueue(store)

    def slow(source, output):
        time.sleep(0.3)
        assert Store(tmp_path).claim() is None
        return process(source, output)

    assert run_once(store, slow, lease_seconds=0.15)
    assert store.get_job(job_id)["status"] == "succeeded"


def test_worker_discards_output_after_another_worker_reclaims_job(tmp_path):
    store = Store(tmp_path)
    job_id = enqueue(store)
    outputs = []
    reclaimed = []

    def stale(source, output):
        outputs.append(output)
        result = process(source, output)
        with sqlite3.connect(store.database) as database:
            database.execute("UPDATE jobs SET lease_expires_at=0 WHERE id=?", (job_id,))
        reclaimed.append(Store(tmp_path).claim())
        return result

    assert run_once(store, stale)
    assert store.get_job(job_id)["status"] == "processing"
    assert reclaimed[0].output_dir != outputs[0]
    assert not outputs[0].exists()


def test_job_recovers_after_worker_process_dies(tmp_path):
    store = Store(tmp_path)
    job_id = enqueue(store)
    code = """
import os, sys
from pathlib import Path
from backend.store import Store
from backend.worker import run_once
run_once(Store(Path(sys.argv[1])), lambda source, output: os._exit(42), lease_seconds=0.15)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=Path(__file__).parents[1])
    assert result.returncode == 42
    assert store.get_job(job_id)["status"] == "processing"
    time.sleep(0.2)
    assert run_once(Store(tmp_path), process)
    job = store.get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["attempts"] == 2


def test_deleted_image_cannot_be_republished_by_running_worker(tmp_path):
    store = Store(tmp_path)
    job_id = enqueue(store)
    image_id = store.get_job(job_id)["image_id"]

    def deleted_during_inference(source, output):
        with Image.open(source) as original:
            original.load()
            store.delete_image(image_id)
            # Inference can finish and create files after the API has deleted them.
            return export_segmentation(original, [], output)

    assert run_once(store, deleted_during_inference)
    assert store.list_images()["total"] == 0
    assert store.claim() is None
    assert not (tmp_path / "jobs" / job_id).exists()
    assert not store.original_path(image_id).exists()
