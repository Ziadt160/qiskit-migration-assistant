"""Unit tests for static validation of ported code (no network)."""

from __future__ import annotations

import pytest

from src.migration.deprecations import DeprecationStore, load_seed_records
from src.migration.validate_output import validate_output


@pytest.fixture
def store(tmp_path):
    s = DeprecationStore(str(tmp_path / "dep.db"))
    s.create()
    s.upsert_many(load_seed_records())
    return s


def test_clean_modern_code_passes(store):
    code = "from qiskit import transpile\nfrom qiskit_aer import AerSimulator\n"
    report = validate_output(code, store, "2.2")
    assert report.passed
    assert report.deprecated_symbols == []


def test_leaked_removed_symbol_is_flagged(store):
    code = "from qiskit import execute\nexecute(qc, backend)"
    report = validate_output(code, store, "2.2")
    assert not report.passed
    assert any("execute" in s for s in report.deprecated_symbols)


def test_syntax_error_is_flagged(store):
    report = validate_output("def f(:\n    pass", store, "2.2")
    assert report.syntax_ok is False
    assert not report.passed
