"""Queue abstraction: enqueue migration jobs onto Redis/RQ, or run them eagerly.

Eager mode (no Redis) runs the job inline using the *same* JobStore the caller
holds — handy for local dev and unit tests. Async mode hands the job to RQ; the
worker process constructs its own store from settings (shared DB).
"""

from __future__ import annotations

from qiskit_migration.config import get_settings
from qiskit_migration.db.db import JobStore

_QUEUE_NAME = "migrations"


def get_queue():
    import redis
    from rq import Queue

    connection = redis.from_url(get_settings().redis_url)
    return Queue(_QUEUE_NAME, connection=connection)


def enqueue_migration(job_id: str, eager: bool = False, store: JobStore | None = None):
    if eager:
        from qiskit_migration.worker.tasks import run_migration_job

        run_migration_job(job_id, store=store)
        return None
    return get_queue().enqueue(
        "qiskit_migration.worker.tasks.run_migration_job",
        job_id,
        job_timeout=get_settings().job_timeout_s,
    )
