"""Background worker: N threads that claim queued jobs from the database and run them.
Runs inside the API process by default (WORKER_ENABLED=true) or standalone: `python -m app.worker`."""
import logging
import threading
import time

from sqlalchemy.exc import OperationalError

from ..config import get_settings
from ..db import SessionLocal
from . import jobs, runner

log = logging.getLogger("dreamcast.worker")


class WorkerPool:
    def __init__(self):
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        s = get_settings()
        try:
            with SessionLocal() as db:
                n = jobs.recover_interrupted(db)
        except OperationalError as e:
            raise RuntimeError("The database has not been initialised. Run: cd backend && .venv/bin/alembic upgrade head") from e
        if n:
            log.warning("Recovered %s interrupted job(s)", n)
        for i in range(max(1, s.worker_concurrency)):
            t = threading.Thread(target=self._loop, name=f"dreamcast-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Started %s worker thread(s)", len(self._threads))

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def _loop(self) -> None:
        poll = get_settings().worker_poll_seconds
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    job = jobs.claim_next(db)
                if job:
                    runner.run_job(job.id)
                    continue
            except Exception:  # noqa: BLE001
                log.exception("Worker loop error")
            self._stop.wait(poll)


def run_forever() -> None:
    from ..providers import register_default_providers
    register_default_providers()
    pool = WorkerPool()
    pool.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pool.stop()
