"""Bounded background audit jobs for the interactive product API."""
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from analytics_support import AnalyticsError

AuditScope = tuple[str, str, str | None]


class AuditJobs:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audit")
        self._lock = Lock()
        self._jobs: dict[str, tuple[AuditScope, Future]] = {}

    def start(self, scope: AuditScope, calculate: Callable[[], dict]) -> str:
        with self._lock:
            for job_id, (key, job) in self._jobs.items():
                if key == scope and not job.done():
                    return job_id
            if sum(not job.done() for _, job in self._jobs.values()) >= 4:
                raise AnalyticsError("audit_busy", "Аудит занят. Попробуйте ещё раз.", 503)
            if len(self._jobs) >= 16:
                oldest = next(key for key, (_, job) in self._jobs.items() if job.done())
                del self._jobs[oldest]
            job_id = uuid4().hex
            self._jobs[job_id] = (scope, self._executor.submit(calculate))
            return job_id

    def result(self, scope: AuditScope, job_id: str) -> dict | None:
        with self._lock:
            entry = self._jobs.get(job_id)
        if entry is None or entry[0] != scope:
            raise AnalyticsError("audit_job_unavailable", "Расчёт аудита недоступен.", 404)
        job = entry[1]
        return job.result() if job.done() else None

    def close(self) -> None:
        self._executor.shutdown(cancel_futures=True)
