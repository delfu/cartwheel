"""Video uploads, full-clip jobs, and seekable per-person mask streams."""

import os
from pathlib import Path
import shutil
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

from backend.ml.video import probe_video
from backend.store import Store


class VideoProcessing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tracker: Literal["botsort", "bytetrack"] = "botsort"


def register_video_routes(app: FastAPI, store: Store) -> None:
    def run_response(run):
        if "video_id" not in run or run["mode"] != "full":
            raise KeyError("Full-video run not found")
        result = run["result"]
        if result:
            prefix = f"/api/video-runs/{run['id']}/artifacts/"
            result["playback_url"] = prefix + result["playback"]
            for track in result["tracks"]:
                track["mask_video_url"] = prefix + track["mask_video"]
        return run

    def video_response(video):
        video["original_url"] = f"/api/videos/{video['id']}/original"
        if "runs" in video:
            video["runs"] = [run_response(run) for run in video["runs"]]
        return video

    @app.post("/api/videos", status_code=201)
    def upload_video(file: UploadFile = File(...)):
        video_id = str(uuid4())
        path = store.video_path(video_id)
        try:
            path.parent.mkdir(parents=True)
            size = 0
            with path.open("wb") as destination:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, "Video exceeds the 100 MiB limit")
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            metadata = probe_video(path)
            for directory in (path.parent, path.parent.parent, store.root):
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return video_response(store.create_video(video_id, Path(file.filename or "video.mp4").name, metadata))
        except Exception:
            shutil.rmtree(path.parent, ignore_errors=True)
            raise
        finally:
            file.file.close()

    @app.get("/api/videos")
    def list_videos():
        return {"items": [video_response(video) for video in store.list_videos()]}

    @app.get("/api/videos/{video_id}")
    def get_video(video_id: str):
        return video_response(store.get_video(video_id))

    @app.get("/api/videos/{video_id}/original")
    def get_original(video_id: str):
        store.get_video(video_id)
        return FileResponse(store.video_path(video_id), media_type="video/mp4")

    @app.post("/api/videos/{video_id}/runs", status_code=202)
    def process_full_video(video_id: str, selection: VideoProcessing):
        return run_response(store.create_video_run(video_id, selection.tracker))

    @app.get("/api/video-runs/{job_id}")
    def get_run(job_id: str):
        return run_response(store.get_job(job_id))

    @app.get("/api/video-runs/{job_id}/artifacts/{filename:path}")
    def get_artifact(job_id: str, filename: str):
        return FileResponse(store.video_artifact_path(job_id, filename), media_type="video/mp4")
