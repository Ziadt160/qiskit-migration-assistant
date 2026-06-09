"""Unit tests for the result cache and its wiring into the worker (no network)."""

from __future__ import annotations

import src.worker.tasks as tasks
from src.cache import InMemoryCache, NoOpCache, cache_key
from src.db.db import JobStore
from src.migration.models import MigrationResult, ValidationReport


def test_cache_key_is_deterministic_and_scoped():
    assert cache_key("a", "2.2") == cache_key("a", "2.2")
    assert cache_key("a", "2.2") != cache_key("a", "2.1")
    assert cache_key("a", "2.2") != cache_key("b", "2.2")


def test_inmemory_cache_roundtrip():
    cache = InMemoryCache()
    assert cache.get("code", "2.2") is None
    cache.set("code", "2.2", "value")
    assert cache.get("code", "2.2") == "value"


def test_noop_cache_never_stores():
    cache = NoOpCache()
    cache.set("code", "2.2", "value")
    assert cache.get("code", "2.2") is None


def test_worker_serves_identical_resubmission_from_cache(tmp_path, monkeypatch):
    store = JobStore("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    store.create_all()

    calls = {"n": 0}

    class _CountingTransformer:
        def transform(self, code, source_version=None):
            calls["n"] += 1
            return MigrationResult(
                target_version="2.2",
                ported_code="ok",
                validation=ValidationReport(syntax_ok=True),
            )

    monkeypatch.setattr(tasks, "build_transformer", lambda: _CountingTransformer())
    cache = InMemoryCache()
    code = "from qiskit import execute"

    first = store.create_job(code)
    tasks.run_migration_job(first, store=store, cache=cache)
    second = store.create_job(code)
    tasks.run_migration_job(second, store=store, cache=cache)

    assert calls["n"] == 1  # second submission served from cache
    assert store.get(second).status == "completed"
    assert store.get(second).result_code is not None
