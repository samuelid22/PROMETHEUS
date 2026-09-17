from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Job:
    id: str
    video_name: str
    state: str = "processing"
    stage: str = "Queued"
    error: str | None = None
    run_dir: Path | None = None
    tier: str = "advanced"
    source_file: str = "source.mp4"


class JobManager:
    def __init__(self, max_workers: int = 1):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(
        self, video_name: str, tier: str = "advanced", source_file: str = "source.mp4"
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            video_name=video_name,
            tier=tier,
            source_file=source_file,
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def submit(self, job_id: str, work: Callable[[], None]) -> None:
        def _run() -> None:
            try:
                work()
            except Exception as exc:
                self.update(job_id, state="error", error=str(exc) or exc.__class__.__name__)

        self._executor.submit(_run)

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for name, value in fields.items():
                setattr(job, name, value)

    def public_info(self, job: Job) -> dict:
        return {
            "id": job.id,
            "video_name": job.video_name,
            "state": job.state,
            "stage": job.stage,
            "error": job.error,
            "tier": job.tier,
        }
