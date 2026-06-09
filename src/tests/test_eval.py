"""The golden-set evaluation must pass its gate on the curated seed alone (offline)."""

from __future__ import annotations

import pytest

from src.eval.dataset.golden import load_golden
from src.eval.metrics import evaluate_detection, evaluate_reference_cleanliness
from src.migration.deprecations import DeprecationStore, load_seed_records


@pytest.fixture
def seed_store(tmp_path):
    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    store.upsert_many(load_seed_records())
    return store


def test_detection_recall_meets_gate(seed_store):
    result = evaluate_detection(load_golden(), seed_store)
    assert result.recall >= 0.9, result.per_case


def test_reference_code_is_clean(seed_store):
    result = evaluate_reference_cleanliness(load_golden(), seed_store, "2.2")
    assert result.pass_rate == 1.0, result.failures


def test_executable_correctness_all_pass():
    from src.eval.metrics import evaluate_executable_correctness
    from src.migration.models import SandboxReport

    cases = [{"id": "a", "reference_ported_code": "x"}, {"id": "b", "reference_ported_code": "y"}]

    class _OkSandbox:
        def run(self, code):
            return SandboxReport(backend="fake", ok=True)

    result = evaluate_executable_correctness(cases, _OkSandbox())
    assert result.pass_rate == 1.0
    assert not result.failures


def test_executable_correctness_reports_failures():
    from src.eval.metrics import evaluate_executable_correctness
    from src.migration.models import SandboxReport

    cases = [{"id": "a", "reference_ported_code": "x"}, {"id": "b", "reference_ported_code": "y"}]

    class _MixedSandbox:
        def __init__(self):
            self.n = 0

        def run(self, code):
            self.n += 1
            ok = self.n == 1
            return SandboxReport(backend="fake", ok=ok, error_type=None if ok else "ImportError")

    result = evaluate_executable_correctness(cases, _MixedSandbox())
    assert result.pass_rate == 0.5
    assert result.failures[0]["error_type"] == "ImportError"


# --- isolated retrieval eval ---

_RET_CASE = {
    "id": "execute",
    "old_code": "from qiskit import execute\nexecute(qc, b)",
    "expected_apis_changed": ["qiskit.execute"],
}


def test_evaluate_retrieval_hit(seed_store):
    from src.eval.metrics import evaluate_retrieval

    class _Retriever:
        def retrieve(self, symbols, deps):
            return [{"text": "The execute function was removed; use backend.run instead."}]

    result = evaluate_retrieval([_RET_CASE], _Retriever(), seed_store)
    assert result.recall == 1.0
    assert result.context_hit_rate == 1.0


def test_evaluate_retrieval_miss(seed_store):
    from src.eval.metrics import evaluate_retrieval

    class _Retriever:
        def retrieve(self, symbols, deps):
            return [{"text": "completely unrelated documentation about widgets"}]

    result = evaluate_retrieval([_RET_CASE], _Retriever(), seed_store)
    assert result.recall == 0.0
    assert result.context_hit_rate == 0.0


# --- end-to-end eval ---


def test_evaluate_end_to_end_success():
    from src.eval.metrics import evaluate_end_to_end
    from src.migration.models import MigrationResult, SandboxReport, ValidationReport

    case = {**_RET_CASE, "source_version": "0.46"}

    class _Transformer:
        def transform(self, code, source_version=None):
            return MigrationResult(
                target_version="2.2",
                ported_code="from qiskit import transpile\nbackend.run(transpile(qc, b))",
                validation=ValidationReport(syntax_ok=True),
                execution=SandboxReport(backend="fake", ok=True),
                repair_attempts=0,
            )

    result = evaluate_end_to_end([case], _Transformer())
    assert result.n == 1
    assert result.validation_pass_rate == 1.0
    assert result.change_applied_rate == 1.0  # 'execute' no longer present
    assert result.executable_pass_rate == 1.0


def test_evaluate_end_to_end_unapplied_change():
    from src.eval.metrics import evaluate_end_to_end
    from src.migration.models import MigrationResult, ValidationReport

    case = {**_RET_CASE, "source_version": "0.46"}

    class _Transformer:
        def transform(self, code, source_version=None):
            return MigrationResult(
                target_version="2.2",
                ported_code="from qiskit import execute\nexecute(qc, b)",  # still wrong
                validation=ValidationReport(syntax_ok=True, deprecated_symbols=["qiskit.execute"]),
                execution=None,
                repair_attempts=2,
            )

    result = evaluate_end_to_end([case], _Transformer())
    assert result.validation_pass_rate == 0.0
    assert result.change_applied_rate == 0.0  # execute still present
    assert result.executable_pass_rate is None  # no sandbox execution
