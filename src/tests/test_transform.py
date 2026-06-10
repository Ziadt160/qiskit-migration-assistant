"""Unit tests for migration orchestration (offline + mocked live stages)."""

from __future__ import annotations

import pytest

from src.migration.deprecations import DeprecationStore, load_seed_records
from src.migration.models import LLMTransformOutput, SandboxReport
from src.migration.transform import MigrationTransformer, find_deprecations

_GOOD = (
    "from qiskit import transpile\n"
    "transpiled = transpile(qc, backend)\n"
    "result = backend.run(transpiled).result()\n"
)
_BAD = "from qiskit import execute\nexecute(qc, backend)"

_OLD = "from qiskit import execute\nexecute(qc, backend)"


@pytest.fixture
def store(tmp_path):
    s = DeprecationStore(str(tmp_path / "dep.db"))
    s.create()
    s.upsert_many(load_seed_records())
    return s


def test_find_deprecations_offline(store):
    symbols, deps = find_deprecations(_OLD, store)
    assert "qiskit.execute" in symbols.lookup_keys
    assert any(d.symbol in ("qiskit.execute", "execute") for d in deps)


class _FakeRetriever:
    def __init__(self):
        self.called = False

    def retrieve(self, symbols, deps):
        self.called = True
        return [{"source": "guides/upgrade.mdx", "text": "Use backend.run instead of execute."}]


class _FakeGenerator:
    def __init__(self, ported_code):
        self._code = ported_code
        self.seen = None

    def transform(self, code, deps, chunks, source_version=None, feedback=None):
        self.seen = {"code": code, "deps": deps, "chunks": chunks, "src": source_version}
        return LLMTransformOutput(
            ported_code=self._code, changes=[], warnings=["could not verify X"]
        )


class _SeqGenerator:
    """Returns a scripted sequence of outputs and records the feedback it receives."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.feedbacks = []

    def transform(self, code, deps, chunks, source_version=None, feedback=None):
        self.feedbacks.append(feedback)
        out = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        return out


class _FakeSandbox:
    def __init__(self, results):
        self._results = list(results)
        self.i = 0

    def run(self, code):
        r = self._results[min(self.i, len(self._results) - 1)]
        self.i += 1
        return r


def test_full_pipeline_passes_validation(store):
    gen = _FakeGenerator(_GOOD)
    retriever = _FakeRetriever()
    transformer = MigrationTransformer(store, retriever, gen, target_version="2.2", max_repairs=0)

    result = transformer.transform(_OLD, source_version="0.46")

    assert retriever.called
    assert result.validation is not None and result.validation.passed
    assert result.ported_code.startswith("from qiskit import transpile")
    assert any(h.symbol in ("qiskit.execute", "execute") for h in result.deprecations_found)
    assert "could not verify X" in result.warnings
    assert gen.seen is not None and gen.seen["deps"]


def test_full_pipeline_detects_leaked_symbol(store):
    gen = _FakeGenerator(_BAD)  # still wrong
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", max_repairs=0
    )

    result = transformer.transform(_OLD)

    assert result.validation is not None and not result.validation.passed
    assert result.validation.deprecated_symbols


def test_self_repair_recovers_after_sandbox_failure(store):
    gen = _SeqGenerator(
        [
            LLMTransformOutput(ported_code=_BAD),  # attempt 1: fails
            LLMTransformOutput(ported_code=_GOOD),  # attempt 2: fixed
        ]
    )
    sandbox = _FakeSandbox(
        [
            SandboxReport(
                backend="fake", ok=False, error_type="ImportError", stderr="cannot import execute"
            ),
            SandboxReport(backend="fake", ok=True, returncode=0),
        ]
    )
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", sandbox=sandbox, max_repairs=2
    )

    result = transformer.transform(_OLD)

    assert result.repair_attempts == 1
    assert result.validation.passed
    assert result.execution is not None and result.execution.ok
    # The repair prompt carried the sandbox error back to the generator.
    assert gen.feedbacks[0] is None
    assert "ImportError" in (gen.feedbacks[1] or "")


def test_self_repair_gives_up_after_max_repairs(store):
    gen = _SeqGenerator([LLMTransformOutput(ported_code=_BAD)])  # never fixes
    sandbox = _FakeSandbox([SandboxReport(backend="fake", ok=False, error_type="ImportError")])
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", sandbox=sandbox, max_repairs=2
    )

    result = transformer.transform(_OLD)

    assert result.repair_attempts == 2
    assert not result.validation.passed


def test_safety_net_applies_known_replacement(store):
    # The model never fixes the leaked import; the deterministic safety net does, and the
    # sandbox confirms the patched code runs -> it gets adopted.
    bad = "from qiskit.algorithms.optimizers import COBYLA\noptimizer = COBYLA()\n"
    gen = _FakeGenerator(bad)
    sandbox = _FakeSandbox(
        [
            SandboxReport(backend="fake", ok=False, error_type="ModuleNotFoundError"),  # bad fails
            SandboxReport(backend="fake", ok=True, returncode=0),  # patched runs clean
        ]
    )
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", sandbox=sandbox, max_repairs=0
    )

    result = transformer.transform(bad)

    assert "qiskit_algorithms.optimizers" in result.ported_code
    assert "qiskit.algorithms" not in result.ported_code
    assert result.execution is not None and result.execution.ok
    assert result.repair_attempts == 0  # the LLM never fixed it; the safety net did


def test_safety_net_rejects_patch_that_does_not_run(store):
    # A non-drop-in replacement (opflow -> quantum_info is not a literal rename) must NOT be
    # adopted: the patched code still fails the sandbox, so the original output is kept.
    bad = "from qiskit.opflow import X, Z\nop = (X ^ X) + (Z ^ Z)\n"
    gen = _FakeGenerator(bad)
    sandbox = _FakeSandbox([SandboxReport(backend="fake", ok=False, error_type="ImportError")])
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", sandbox=sandbox, max_repairs=0
    )

    result = transformer.transform(bad)

    assert "qiskit.opflow" in result.ported_code  # patch rejected; original kept
    assert result.execution is not None and not result.execution.ok
