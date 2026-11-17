"""Manual video processing and frame artifact endpoints."""

from io import BytesIO
import os
from pathlib import Path
import shutil
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, Response
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from backend.ml.pipeline import isolate
from backend.ml.video import probe_video
from backend.store import Store


class VideoRange(BaseModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_frames: int = Field(default=60, ge=1, le=60, strict=True)
    tracker: Literal["botsort", "bytetrack"] = "botsort"


class TrackSelection(BaseModel):
    track_ids: list[Annotated[int, Field(strict=True, ge=1)]] = Field(max_length=300)


def register_video_routes(app: FastAPI, store: Store) -> None:
    def run_response(run):
        if "video_id" not in run:
            raise KeyError("Video run not found")
        result = run["result"]
        if result:
            prefix = f"/api/video-runs/{run['id']}/artifacts/"
            for frame in result["frames"]:
                for key in ("original", "preview", "mask", "isolated"):
                    frame[f"{key}_url"] = prefix + frame[key]
                for person in frame["people"]:
                    person["mask_url"] = prefix + person["mask"]
                    person["isolated_url"] = prefix + person["isolated"]
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
    def process_range(video_id: str, selection: VideoRange):
        return run_response(store.create_video_run(video_id, selection.start_seconds, selection.end_seconds,
                                                    selection.max_frames, selection.tracker))

    @app.get("/api/video-runs/{job_id}")
    def get_run(job_id: str):
        return run_response(store.get_job(job_id))

    @app.get("/api/video-runs/{job_id}/artifacts/{filename:path}")
    def get_artifact(job_id: str, filename: str):
        return FileResponse(store.video_artifact_path(job_id, filename), media_type="image/png")

    @app.post("/api/video-runs/{job_id}/frames/{frame_number}/cutout")
    def cutout(job_id: str, frame_number: int, selection: TrackSelection,
               view: Literal["mask", "isolated"] = "isolated"):
        run = store.get_job(job_id)
        if "video_id" not in run:
            raise KeyError("Video run not found")
        if run["status"] != "succeeded":
            raise HTTPException(409, "Wait for video processing to finish")
        result = run["result"]
        if not 0 <= frame_number < len(result["frames"]):
            raise KeyError("Processed frame not found")
        indices = selection.track_ids
        if len(indices) != len(set(indices)) or not set(indices) <= set(result["track_ids"]):
            raise ValueError("Select unique track IDs from this processing run")
        frame = result["frames"][frame_number]
        with Image.open(store.video_artifact_path(job_id, frame["original"])) as original:
            union = np.zeros((original.height, original.width), dtype=bool)
            for person in frame["people"]:
                if person["track_id"] in indices:
                    with Image.open(store.video_artifact_path(job_id, person["mask"])) as mask:
                        union |= np.array(mask) > 0
            mask, isolated = isolate(original, [union])
        buffer = BytesIO()
        (mask if view == "mask" else isolated).save(buffer, format="PNG")
        return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})
