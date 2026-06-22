"""Background task: run a migration job and persist its result.

`build_transformer()` is a seam so tests can substitute a fake without touching
live services. Results are cached by code+target so identical resubmissions are
served instantly. The RQ worker imports `run_migration_job` by dotted path.
"""

from __future__ import annotations

import logging

from qiskit_migration.cache import ResultCache, get_result_cache
from qiskit_migration.config import get_settings
from qiskit_migration.db.db import JobStatus, JobStore
from qiskit_migration.observability import JOB_DURATION, JOB_OUTCOMES

logger = logging.getLogger(__name__)


_TRANSFORMER = None


def build_transformer():
    """Construct (and cache) the live transformer. Patched in tests.

    Cached at module level so a long-lived worker loads the embedding model once
    rather than per job.
    """
    global _TRANSFORMER
    if _TRANSFORMER is None:
        from qiskit_migration.migration.transform import MigrationTransformer

        _TRANSFORMER = MigrationTransformer.from_settings(get_settings().deprecations_db_path)
    return _TRANSFORMER


def run_migration_job(
    job_id: str, store: JobStore | None = None, cache: ResultCache | None = None
) -> None:
    store = store or JobStore()
    cache = cache if cache is not None else get_result_cache()
    target = get_settings().qiskit_target_version

    job = store.get(job_id)
    if job is None:
        logger.error("run_migration_job: job %s not found", job_id)
        return

    cached = cache.get(job.query, target)
    if cached is not None:
        store.set_status(job_id, JobStatus.COMPLETED.value, result_code=cached)
        JOB_OUTCOMES.labels(status="completed_cached").inc()
        logger.info("job %s served from cache", job_id)
        return

    store.set_status(job_id, JobStatus.PROCESSING.value)
    try:
        with JOB_DURATION.time():
            transformer = build_transformer()
            result = transformer.transform(job.query, source_version=job.source_version)
        result_code = result.model_dump_json()
        cache.set(job.query, target, result_code)
        store.set_status(job_id, JobStatus.COMPLETED.value, result_code=result_code)
        JOB_OUTCOMES.labels(status="completed").inc()
        logger.info("job %s completed", job_id)
    except Exception as e:  # noqa: BLE001 - record failure on the job, don't crash the worker
        logger.exception("job %s failed", job_id)
        store.set_status(job_id, JobStatus.FAILED.value, error=f"{type(e).__name__}: {e}")
        JOB_OUTCOMES.labels(status="failed").inc()
