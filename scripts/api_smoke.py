"""Exercise real HTTP uploads and parallel ML workers; stop every child on exit."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from check_outputs import check


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def wait_for_api(client, process):
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError("API exited; inspect api.log")
        try:
            if client.get("/api/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("API did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="A new directory for results and logs")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ, CARTWHEEL_STORAGE=str(output / "storage"))
    processes = []
    with ExitStack() as stack:
        api_log = stack.enter_context((output / "api.log").open("w"))

        def start_api():
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "backend.api:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(port),
            ], cwd=project, env=environment, stdout=api_log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process

        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                api = start_api()
                wait_for_api(client, api)
                jobs = []
                for image in args.images:
                    with image.open("rb") as source:
                        response = client.post("/api/images", files={"file": (image.name, source)})
                    assert response.status_code == 202, response.text
                    detail = response.json()
                    assert detail["job"]["status"] == "queued"
                    jobs.append({"id": detail["job"]["id"], "image_id": detail["id"], "seen": ["queued"]})
                for index in range(2):
                    log = stack.enter_context((output / f"worker-{index + 1}.log").open("w"))
                    processes.append(subprocess.Popen([
                        sys.executable, "-m", "backend.worker", "--storage", str(output / "storage"),
                    ], cwd=project, stdout=log, stderr=subprocess.STDOUT))
                deadline = time.monotonic() + 90
                peak_processing = 0
                while True:
                    statuses = []
                    for job in jobs:
                        response = client.get(f"/api/jobs/{job['id']}")
                        assert response.status_code == 200
                        status = response.json()["status"]
                        if status not in job["seen"]:
                            job["seen"].append(status)
                        assert status != "failed", response.text
                        statuses.append(status)
                    peak_processing = max(peak_processing, statuses.count("processing"))
                    if all(status == "succeeded" for status in statuses):
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError("Jobs did not finish; inspect worker logs")
                    time.sleep(0.1)
                for job in jobs:
                    image = client.get(f"/api/images/{job['image_id']}").json()
                    result = image["result"]
                    destination = output / job["image_id"]
                    destination.mkdir()
                    original = client.get(image["original_url"])
                    assert original.status_code == 200
                    (destination / "original.png").write_bytes(original.content)
                    names = {result[key] for key in ("preview", "mask", "isolated")}
                    for person in result["people"]:
                        names.update((person["mask"], person["isolated"]))
                    for name in names:
                        response = client.get(f"/api/images/{job['image_id']}/artifacts/{name}")
                        assert response.status_code == 200
                        (destination / name).write_bytes(response.content)
                    (destination / "metadata.json").write_text(json.dumps(dict(result, original="original.png")))
                    job["people"] = check(destination)
                gallery_ids, cursor = set(), None
                while True:
                    page = client.get("/api/images", params={"cursor": cursor} if cursor else {}).json()
                    gallery_ids.update(image["id"] for image in page["items"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                assert gallery_ids == {job["image_id"] for job in jobs}
                stop(api)
                api = start_api()
                wait_for_api(client, api)
                for job in jobs:
                    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "succeeded"
                report = {"workers": 2, "peak_processing": peak_processing,
                          "api_restart": "passed", "output_checks": "passed", "jobs": jobs}
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report, indent=2))
        finally:
            for process in reversed(processes):
                stop(process)


if __name__ == "__main__":
    main()
