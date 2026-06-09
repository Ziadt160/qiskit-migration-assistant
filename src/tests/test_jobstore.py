"""Unit tests for the SQLAlchemy-backed jobs store (SQLite, no network)."""

from __future__ import annotations

import pytest

from src.db.db import JobStatus, JobStore


@pytest.fixture
def store(tmp_path):
    s = JobStore("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    s.create_all()
    return s


def test_create_get_update_roundtrip(store):
    job_id = store.create_job("from qiskit import execute", source_version="0.46")
    job = store.get(job_id)
    assert job is not None
    assert job.status == JobStatus.PENDING.value  # lowercase enum value, no CHECK mismatch
    assert job.query.startswith("from qiskit")
    assert job.source_version == "0.46"

    store.set_status(job_id, JobStatus.COMPLETED.value, result_code='{"ported_code": "x"}')
    updated = store.get(job_id)
    assert updated.status == JobStatus.COMPLETED.value
    assert updated.result_code == '{"ported_code": "x"}'


def test_failed_status_records_error(store):
    job_id = store.create_job("code")
    store.set_status(job_id, JobStatus.FAILED.value, error="boom")
    assert store.get(job_id).error == "boom"


def test_get_missing_returns_none(store):
    assert store.get("does-not-exist") is None


def test_recent_lists_jobs(store):
    a = store.create_job("a")
    b = store.create_job("b")
    ids = {r.id for r in store.recent()}
    assert {a, b} <= ids
