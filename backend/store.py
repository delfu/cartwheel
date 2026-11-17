"""Persistent image metadata and a leased SQLite queue for local workers."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from backend.contracts import Claim, SegmentationResult, VideoClaim


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "cartwheel.sqlite3"
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL,
                    created_at REAL NOT NULL, latest_job_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(id),
                    status TEXT NOT NULL CHECK(status IN ('queued','processing','succeeded','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                    error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    started_at REAL, finished_at REAL, available_at REAL NOT NULL,
                    lease_expires_at REAL, token TEXT, output_dir TEXT, result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, width INTEGER NOT NULL,
                    height INTEGER NOT NULL, duration REAL NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_jobs (
                    id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
                    start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
                    max_frames INTEGER NOT NULL, tracker TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','processing','succeeded','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                    error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    started_at REAL, finished_at REAL, available_at REAL NOT NULL,
                    lease_expires_at REAL, token TEXT, output_dir TEXT, result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS video_queue ON video_jobs(status,available_at,created_at);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS images_gallery ON images(created_at DESC, id DESC);
            """)

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def original_path(self, image_id: str) -> Path:
        return self.root / "images" / image_id / "original.png"

    def create_image(self, image_id: str, filename: str, width: int, height: int) -> dict:
        job_id = str(uuid4())
        with self._transaction() as db:
            now = time.time()
            db.execute("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?)",
                       (image_id, filename, width, height, now, job_id))
            self._insert_job(db, job_id, image_id, now)
        return self.get_image(image_id)

    @staticmethod
    def _insert_job(db, job_id, image_id, now):
        db.execute("""INSERT INTO jobs(id,image_id,status,created_at,updated_at,available_at)
                      VALUES (?,?,'queued',?,?,?)""", (job_id, image_id, now, now, now))

    @staticmethod
    def _job(row) -> dict:
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json")) if row["result_json"] else None
        for key in ("token", "output_dir"):
            result.pop(key)
        for key in ("created_at", "updated_at", "started_at", "finished_at", "available_at", "lease_expires_at"):
            value = result[key]
            result[key] = datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None
        return result

    @staticmethod
    def _image(row, job) -> dict:
        result = dict(row)
        result.pop("latest_job_id")
        result["created_at"] = datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat()
        result["status"] = "ready" if job["status"] == "succeeded" else job["status"]
        result["job"] = job
        result["result"] = job["result"]
        return result

    def get_job(self, job_id: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                row = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Job not found")
        return self._job(row)

    def get_image(self, image_id: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
            if row is None:
                raise KeyError("Image not found")
            job = db.execute("SELECT * FROM jobs WHERE id=?", (row["latest_job_id"],)).fetchone()
        return self._image(row, self._job(job))

    def list_images(self, limit: int = 20, cursor: str | None = None) -> dict:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            total = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            params, condition = [], ""
            if cursor:
                previous = db.execute("SELECT created_at FROM images WHERE id=?", (cursor,)).fetchone()
                if previous is None:
                    raise ValueError("Unknown gallery cursor")
                condition = "WHERE created_at < ? OR (created_at = ? AND id < ?)"
                params = [previous["created_at"], previous["created_at"], cursor]
            rows = db.execute(f"SELECT * FROM images {condition} ORDER BY created_at DESC,id DESC LIMIT ?",
                              (*params, limit + 1)).fetchall()
            page = rows[:limit]
            if not page:
                return {"items": [], "next_cursor": None, "total": total}
            placeholders = ",".join("?" for _ in page)
            jobs = {row["id"]: self._job(row) for row in db.execute(
                f"SELECT * FROM jobs WHERE id IN ({placeholders})", [row["latest_job_id"] for row in page])}
        return {"items": [self._image(row, jobs[row["latest_job_id"]]) for row in page],
                "next_cursor": page[-1]["id"] if len(rows) > limit else None, "total": total}

    def delete_image(self, image_id: str) -> None:
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone() is None:
                raise KeyError("Image not found")
            job_ids = [row["id"] for row in db.execute(
                "SELECT id FROM jobs WHERE image_id=?", (image_id,))]
            db.execute("DELETE FROM jobs WHERE image_id=?", (image_id,))
            db.execute("DELETE FROM images WHERE id=?", (image_id,))
        # Removing job rows fences running workers before any files disappear.
        for directory in [self.original_path(image_id).parent,
                          *(self.root / "jobs" / job_id for job_id in job_ids)]:
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                pass

    def claim(self, lease_seconds: float = 60) -> Claim | VideoClaim | None:
        row, token = self._claim_row("jobs", lease_seconds)
        if row is not None:
            return Claim(row["id"], row["image_id"], token, self.original_path(row["image_id"]),
                         self.root / "jobs" / row["id"] / token)
        row, token = self._claim_row("video_jobs", lease_seconds)
        if row is None:
            return None
        return VideoClaim(row["id"], row["video_id"], token, self.video_path(row["video_id"]),
                          self.root / "jobs" / row["id"] / token, row["start_seconds"],
                          row["end_seconds"], row["max_frames"], row["tracker"])

    def _claim_row(self, table: str, lease_seconds: float):
        if lease_seconds <= 0:
            raise ValueError("Lease duration must be positive")
        token = str(uuid4())
        with self._transaction() as db:
            now = time.time()
            db.execute(f"""UPDATE {table} SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
                error='Worker lease expired', updated_at=?, available_at=?, token=NULL, lease_expires_at=NULL,
                finished_at=CASE WHEN attempts>=max_attempts THEN ? ELSE NULL END
                WHERE status='processing' AND lease_expires_at<=?""", (now, now, now, now))
            row = db.execute(f"""SELECT * FROM {table} WHERE status='queued' AND available_at<=?
                                ORDER BY created_at,id LIMIT 1""", (now,)).fetchone()
            if row is not None:
                db.execute(f"""UPDATE {table} SET status='processing',attempts=attempts+1,token=?,
                    updated_at=?,started_at=COALESCE(started_at,?),lease_expires_at=? WHERE id=?""",
                           (token, now, now, now + lease_seconds, row["id"]))
        return row, token

    @staticmethod
    def _queue(claim: Claim | VideoClaim) -> str:
        return "video_jobs" if isinstance(claim, VideoClaim) else "jobs"

    def heartbeat(self, claim: Claim | VideoClaim, lease_seconds: float = 60) -> bool:
        if lease_seconds <= 0:
            raise ValueError("Lease duration must be positive")
        with self._transaction() as db:
            now = time.time()
            return db.execute(f"""UPDATE {self._queue(claim)} SET updated_at=?,lease_expires_at=? WHERE id=? AND token=?
                AND status='processing' AND lease_expires_at>?""",
                (now, now + lease_seconds, claim.job_id, claim.token, now)).rowcount == 1

    def succeed(self, claim: Claim | VideoClaim, result: SegmentationResult) -> bool:
        with self._transaction() as db:
            now = time.time()
            return db.execute(f"""UPDATE {self._queue(claim)} SET status='succeeded',result_json=?,output_dir=?,updated_at=?,
                finished_at=?,token=NULL,lease_expires_at=NULL,error=NULL WHERE id=? AND token=?
                AND status='processing' AND lease_expires_at>?""",
                (json.dumps(result), str(claim.output_dir.relative_to(self.root)), now, now,
                 claim.job_id, claim.token, now)).rowcount == 1

    def fail(self, claim: Claim | VideoClaim, error: str) -> bool:
        with self._transaction() as db:
            now = time.time()
            row = db.execute(f"""SELECT attempts,max_attempts FROM {self._queue(claim)} WHERE id=? AND token=?
                AND status='processing' AND lease_expires_at>?""", (claim.job_id, claim.token, now)).fetchone()
            if row is None:
                return False
            final = row["attempts"] >= row["max_attempts"]
            db.execute(f"""UPDATE {self._queue(claim)} SET status=?,error=?,updated_at=?,available_at=?,finished_at=?,
                token=NULL,lease_expires_at=NULL WHERE id=?""",
                ("failed" if final else "queued", error[:2000], now,
                 now + min(60, 2 ** (row["attempts"] - 1)), now if final else None, claim.job_id))
        return True

    def retry(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if "video_id" in job:
            if job["status"] != "failed":
                raise ValueError("Only failed video processing can be retried")
            return self.create_video_run(job["video_id"], job["start_seconds"], job["end_seconds"],
                                         job["max_frames"], job["tracker"])
        new_id = str(uuid4())
        with self._transaction() as db:
            now = time.time()
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Job not found")
            latest = db.execute("""SELECT j.status FROM images i JOIN jobs j ON j.id=i.latest_job_id
                                   WHERE i.id=?""", (row["image_id"],)).fetchone()
            if row["status"] != "failed" or latest["status"] in {"queued", "processing"}:
                raise ValueError("Only a failed job without active processing can be retried")
            self._insert_job(db, new_id, row["image_id"], now)
            db.execute("UPDATE images SET latest_job_id=? WHERE id=?", (new_id, row["image_id"]))
        return self.get_job(new_id)

    def artifact_path(self, image_id: str, filename: str) -> Path:
        with closing(self._connect()) as db:
            row = db.execute("""SELECT j.* FROM images i JOIN jobs j ON j.id=i.latest_job_id
                                WHERE i.id=? AND j.status='succeeded'""", (image_id,)).fetchone()
        if row is None:
            raise KeyError("Completed image result not found")
        result = json.loads(row["result_json"])
        allowed = {result["preview"], result["mask"], result["isolated"]}
        for person in result["people"]:
            allowed.update((person["mask"], person["isolated"]))
        if Path(filename).name != filename or filename not in allowed:
            raise KeyError("Artifact not found")
        path = self.root / row["output_dir"] / filename
        if not path.is_file():
            raise KeyError("Artifact not found")
        return path

    def video_path(self, video_id: str) -> Path:
        return self.root / "videos" / video_id / "original.mp4"

    def create_video(self, video_id: str, filename: str, metadata: dict) -> dict:
        with self._transaction() as db:
            db.execute("INSERT INTO videos VALUES (?,?,?,?,?,?)", (video_id, filename,
                       metadata["width"], metadata["height"], metadata["duration"], time.time()))
        return self.get_video(video_id)

    def get_video(self, video_id: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
            if row is None:
                raise KeyError("Video not found")
            runs = db.execute("SELECT * FROM video_jobs WHERE video_id=? ORDER BY created_at DESC,id DESC",
                              (video_id,)).fetchall()
        return dict(row, runs=[self._job(run) for run in runs])

    def list_videos(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM videos ORDER BY created_at DESC,id DESC").fetchall()
        return [dict(row) for row in rows]

    def create_video_run(self, video_id: str, start: float, end: float, max_frames: int, tracker: str) -> dict:
        job_id = str(uuid4())
        with self._transaction() as db:
            video = db.execute("SELECT duration FROM videos WHERE id=?", (video_id,)).fetchone()
            if video is None:
                raise KeyError("Video not found")
            if not (0 <= start < end <= video["duration"] and 1 <= max_frames <= 60
                    and tracker in {"botsort", "bytetrack"}):
                raise ValueError("Choose a valid video range, frame limit, and tracker")
            if db.execute("SELECT 1 FROM video_jobs WHERE video_id=? AND status IN ('queued','processing')",
                          (video_id,)).fetchone():
                raise ValueError("This video already has processing queued or running")
            now = time.time()
            db.execute("""INSERT INTO video_jobs(id,video_id,start_seconds,end_seconds,max_frames,tracker,
                status,created_at,updated_at,available_at) VALUES (?,?,?,?,?,?,'queued',?,?,?)""",
                (job_id, video_id, start, end, max_frames, tracker, now, now, now))
        return self.get_job(job_id)

    def video_artifact_path(self, job_id: str, filename: str) -> Path:
        with closing(self._connect()) as db:
            row = db.execute("SELECT output_dir,result_json FROM video_jobs WHERE id=? AND status='succeeded'",
                             (job_id,)).fetchone()
        if row is None:
            raise KeyError("Completed video run not found")
        allowed = set()
        for frame in json.loads(row["result_json"])["frames"]:
            allowed.update(frame[key] for key in ("original", "preview", "mask", "isolated"))
            for person in frame["people"]:
                allowed.update((person["mask"], person["isolated"]))
        if filename not in allowed:
            raise KeyError("Video artifact not found")
        path = self.root / row["output_dir"] / filename
        if not path.is_file():
            raise KeyError("Video artifact not found")
        return path
