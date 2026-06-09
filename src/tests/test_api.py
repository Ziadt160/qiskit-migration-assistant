"""API tests using FastAPI's TestClient in eager mode with a fake transformer.

No Redis and no live services: the job runs inline and the transformer is patched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.db.db import JobStore
from src.migration.models import MigrationResult, ValidationReport


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = JobStore("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    store.create_all()

    fake_result = MigrationResult(
        target_version="2.2",
        ported_code="from qiskit import transpile\n",
        validation=ValidationReport(syntax_ok=True),
    )

    class _FakeTransformer:
        def transform(self, code, source_version=None):
            return fake_result

    monkeypatch.setattr("src.worker.tasks.build_transformer", lambda: _FakeTransformer())

    from src.api.main import create_app

    return TestClient(create_app(store=store, eager=True))


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_migrate_then_poll_completes(client):
    resp = client.post("/migrate", json={"code": "from qiskit import execute\nexecute(qc, b)"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    poll = client.get(f"/jobs/{job_id}")
    assert poll.status_code == 200
    body = poll.json()
    assert body["status"] == "completed"
    assert body["result"]["ported_code"].startswith("from qiskit import transpile")


def test_migrate_rejects_invalid_python(client):
    resp = client.post("/migrate", json={"code": "def f(:\n    pass"})
    assert resp.status_code == 400


def test_job_not_found(client):
    assert client.get("/jobs/nope").status_code == 404


def test_metrics_endpoint_exposed(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "migration_requests_total" in resp.text


def test_rate_limit_returns_429(tmp_path, monkeypatch):
    store = JobStore("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    store.create_all()

    class _FakeTransformer:
        def transform(self, code, source_version=None):
            return MigrationResult(
                target_version="2.2",
                ported_code="x",
                validation=ValidationReport(syntax_ok=True),
            )

    monkeypatch.setattr("src.worker.tasks.build_transformer", lambda: _FakeTransformer())

    from src.api.main import create_app

    client = TestClient(create_app(store=store, eager=True, rate_limit_per_min=1))
    payload = {"code": "from qiskit import execute\nexecute(qc, b)"}
    assert client.post("/migrate", json=payload).status_code == 202
    assert client.post("/migrate", json=payload).status_code == 429
