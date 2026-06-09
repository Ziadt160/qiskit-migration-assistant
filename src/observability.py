"""Prometheus metrics for the API and worker.

Exposed at GET /metrics on the API. Note: in async (RQ) mode the worker is a
separate process, so its job-outcome counters live in that process's registry —
scrape it separately or run Prometheus multiprocess mode. In eager mode (and for
request-level counters) everything is in the API process.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

MIGRATE_REQUESTS = Counter("migration_requests_total", "Migrate requests received", ["result"])
JOB_OUTCOMES = Counter("migration_job_outcomes_total", "Migration job outcomes", ["status"])
JOB_DURATION = Histogram("migration_job_duration_seconds", "Wall-clock duration of a migration job")


def metrics_payload() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
