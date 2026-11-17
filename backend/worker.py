"""Run independent, restartable workers against the persistent image queue."""

import argparse
from collections.abc import Callable
from functools import partial
import logging
import os
from pathlib import Path
import shutil
from threading import Event, Thread

from backend.contracts import SegmentationResult, VideoClaim
from backend.store import Store


logger = logging.getLogger(__name__)
Processor = Callable[[Path, Path], SegmentationResult]


def run_once(store: Store, process: Processor, *, lease_seconds: float = 60, process_video=None) -> bool:
    if lease_seconds <= 0:
        raise ValueError("Lease duration must be positive")
    claim = store.claim(lease_seconds=lease_seconds)
    if claim is None:
        return False
    finished = Event()
    published = False

    def heartbeat() -> None:
        while not finished.wait(lease_seconds / 3):
            try:
                if not store.heartbeat(claim, lease_seconds=lease_seconds):
                    logger.warning("Lost lease for job %s", claim.job_id)
                    return
            except Exception:
                logger.exception("Could not renew lease for job %s", claim.job_id)

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if isinstance(claim, VideoClaim):
            if process_video is None:
                from backend.ml.video import process_video
            result = process_video(claim.source_path, claim.output_dir, start=claim.start_seconds,
                                   end=claim.end_seconds, max_frames=claim.max_frames, tracker=claim.tracker)
        else:
            result = process(claim.source_path, claim.output_dir)
        for path in claim.output_dir.rglob("*"):
            if not path.is_file():
                continue
            with path.open("rb") as artifact:
                os.fsync(artifact.fileno())
        nested = sorted((path for path in claim.output_dir.rglob("*") if path.is_dir()),
                        key=lambda path: len(path.parts), reverse=True)
        for directory in (*nested, claim.output_dir, claim.output_dir.parent, claim.output_dir.parent.parent, store.root):
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        published = store.succeed(claim, result)
        if published:
            logger.info("Completed job %s", claim.job_id)
        else:
            logger.warning("Discarded stale result for job %s", claim.job_id)
    except Exception as error:
        logger.exception("Processing failed for job %s", claim.job_id)
        try:
            store.fail(claim, str(error))
        except Exception:
            logger.exception("Could not record failure; lease recovery will retry job %s", claim.job_id)
    finally:
        finished.set()
        thread.join()
        if not published:
            shutil.rmtree(claim.output_dir, ignore_errors=True)
            try:
                claim.output_dir.parent.rmdir()
            except OSError:
                pass  # Another attempt may still be using this job directory.
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, default=Path(os.environ.get("CARTWHEEL_STORAGE", "storage")))
    parser.add_argument("--model", choices=["yolov8n-seg.pt", "yolov8s-seg.pt"], default="yolov8n-seg.pt")
    parser.add_argument("--once", action="store_true", help="Process at most one available job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = Store(args.storage)
    segmenter = None

    def process(source: Path, output: Path) -> SegmentationResult:
        nonlocal segmenter
        from backend.ml.artifacts import process_image
        from backend.ml.pipeline import Segmenter

        if segmenter is None:
            segmenter = Segmenter(Path("models") / args.model)
        return process_image(source, output, segmenter)

    try:
        while True:
            from backend.ml.video import process_video
            processed = run_once(store, process, process_video=partial(
                process_video, model_path=Path("models") / args.model))
            if args.once:
                break
            if not processed:
                Event().wait(1)
    except KeyboardInterrupt:
        logger.info("Worker stopped; unfinished claims will recover after their leases expire")


if __name__ == "__main__":
    main()
