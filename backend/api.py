"""Image upload, gallery and job-status API. Workers run independently."""

import os
from io import BytesIO
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
from uuid import uuid4
from typing import Annotated, Literal

import numpy as np
from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from backend.ml.pipeline import isolate, load_image
from backend.store import Store


class PersonSelection(BaseModel):
    person_indices: list[Annotated[int, Field(strict=True, ge=1)]] = Field(max_length=300)


class UploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limits = {"/api/images": 26, "/api/videos": 101}
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in limits:
            return await self.app(scope, receive, send)
        limit = limits[scope["path"]]
        size, exceeded, responded = 0, False, False
        response = JSONResponse(status_code=413, content={
            "error": {"code": "request_failed", "message": f"Upload request exceeds the {limit} MiB limit"}})

        async def limited_receive():
            nonlocal size, exceeded
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit * 1024 * 1024:
                exceeded = True
                # The multipart parser closes its temporary files for this exception.
                raise MultiPartException("Upload request is too large")
            return message

        async def limited_send(message):
            nonlocal responded
            if not exceeded:
                await send(message)
            elif not responded:
                responded = True
                await response(scope, receive, send)

        return await self.app(scope, limited_receive, limited_send)


def create_app(root: Path | None = None) -> FastAPI:
    store = Store(root if root is not None else Path(os.environ.get("CARTWHEEL_STORAGE", "storage")))
    app = FastAPI(title="Cartwheel")
    app.add_middleware(UploadLimit)
    app.state.store = store

    @app.exception_handler(KeyError)
    async def missing_resource(request: Request, error: KeyError):
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": error.args[0]}})

    @app.exception_handler(ValueError)
    async def invalid_request(request: Request, error: ValueError):
        return JSONResponse(status_code=400, content={"error": {"code": "invalid_request", "message": str(error)}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": str(error)}})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse(status_code=error.status_code,
                            content={"error": {"code": "request_failed", "message": str(error.detail)}})

    def image_response(image: dict) -> dict:
        image["original_url"] = f"/api/images/{image['id']}/original"
        result = image["result"]
        if result:
            prefix = f"/api/images/{image['id']}/artifacts/"
            image["preview_url"] = prefix + result["preview"]
            image["mask_url"] = prefix + result["mask"]
            image["isolated_url"] = prefix + result["isolated"]
            image["people"] = [dict(person, mask_url=prefix + person["mask"],
                                     isolated_url=prefix + person["isolated"]) for person in result["people"]]
        else:
            image.update(preview_url=None, mask_url=None, isolated_url=None, people=[])
        return image

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/images", status_code=202)
    def upload_image(file: UploadFile = File(...)):
        image_id, size = str(uuid4()), 0
        destination = store.original_path(image_id)
        try:
            with NamedTemporaryFile(dir=store.root) as temporary:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:
                        raise HTTPException(413, "Image exceeds the 25 MiB limit")
                    temporary.write(chunk)
                temporary.flush()
                try:
                    image = load_image(Path(temporary.name))
                except (OSError, ValueError, Image.DecompressionBombError) as error:
                    message = "Upload a valid PNG or JPEG image." if isinstance(error, OSError) else str(error)
                    raise HTTPException(400, message) from error
                destination.parent.mkdir(parents=True)
                with destination.open("wb") as original:
                    image.save(original, format="PNG")
                    original.flush()
                    os.fsync(original.fileno())
                for directory in (destination.parent, destination.parent.parent, store.root):
                    descriptor = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                return image_response(store.create_image(
                    image_id, Path(file.filename or "image").name, image.width, image.height))
        except Exception:
            shutil.rmtree(destination.parent, ignore_errors=True)
            raise
        finally:
            file.file.close()

    @app.get("/api/images")
    def list_images(limit: int = Query(20, ge=1, le=100), cursor: str | None = None):
        page = store.list_images(limit, cursor)
        page["items"] = [image_response(image) for image in page["items"]]
        return page

    @app.get("/api/images/{image_id}")
    def get_image(image_id: str):
        return image_response(store.get_image(image_id))

    @app.delete("/api/images/{image_id}", status_code=204)
    def delete_image(image_id: str):
        store.delete_image(image_id)
        return Response(status_code=204)

    @app.get("/api/images/{image_id}/original")
    def get_original(image_id: str):
        store.get_image(image_id)
        return FileResponse(store.original_path(image_id), media_type="image/png")

    @app.get("/api/images/{image_id}/artifacts/{filename}")
    def get_artifact(image_id: str, filename: str):
        return FileResponse(store.artifact_path(image_id, filename), media_type="image/png")

    @app.post("/api/images/{image_id}/cutout")
    def create_cutout(image_id: str, selection: PersonSelection,
                      view: Literal["mask", "isolated"] = "isolated"):
        detail = store.get_image(image_id)
        if detail["status"] != "ready":
            raise HTTPException(409, "Wait for image processing to finish before selecting people")
        people = {person["index"]: person for person in detail["result"]["people"]}
        indices = selection.person_indices
        if len(indices) != len(set(indices)) or not set(indices) <= people.keys():
            raise HTTPException(400, "Select unique person indices detected in this image")
        with Image.open(store.original_path(image_id)) as source:
            union = np.zeros((source.height, source.width), dtype=bool)
            for index in indices:
                with Image.open(store.artifact_path(image_id, people[index]["mask"])) as mask:
                    union |= np.array(mask) > 0
            mask, isolated = isolate(source, [union])
        buffer = BytesIO()
        (mask if view == "mask" else isolated).save(buffer, format="PNG")
        return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return store.get_job(job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str):
        try:
            return store.retry(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    from backend.video_api import register_video_routes
    register_video_routes(app, store)
    return app
