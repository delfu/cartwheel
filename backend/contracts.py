"""Shared queue claim and published ML output contract."""

from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict


class BoundingBox(TypedDict):
    x: float
    y: float
    width: float
    height: float


class PersonResult(TypedDict):
    track_id: NotRequired[int]
    index: int
    confidence: float
    bbox: BoundingBox
    mask: str
    isolated: str


class SegmentationResult(TypedDict):
    width: int
    height: int
    preview: str
    mask: str
    isolated: str
    people: list[PersonResult]


@dataclass(frozen=True)
class Claim:
    job_id: str
    image_id: str
    token: str
    source_path: Path
    output_dir: Path


@dataclass(frozen=True)
class VideoClaim:
    job_id: str
    video_id: str
    token: str
    source_path: Path
    output_dir: Path
    start_seconds: float
    end_seconds: float
    max_frames: int
    tracker: str
