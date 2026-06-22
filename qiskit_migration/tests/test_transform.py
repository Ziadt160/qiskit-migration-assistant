"""Unit tests for migration orchestration (offline + mocked live stages)."""

from __future__ import annotations

import pytest

from qiskit_migration.migration.deprecations import DeprecationStore, load_seed_records
from qiskit_migration.migration.models import LLMTransformOutput, SandboxReport
from qiskit_migration.migration.transform import MigrationTransformer, find_deprecations

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
        self.deps_seen: list[list] = []  # the deps list passed on each call

    def transform(self, code, deps, chunks, source_version=None, feedback=None):
        self.feedbacks.append(feedback)
        self.deps_seen.append(list(deps))
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


def test_no_deprecations_short_circuits_without_llm(store):
    # Clean, already-modern code: the table flags nothing, so the pipeline must NOT invoke
    # the LLM (which can "tidy" correct code into broken code) — it returns the input verbatim.
    gen = _FakeGenerator("THIS WOULD REPLACE THE CODE IF THE LLM RAN")
    retriever = _FakeRetriever()
    transformer = MigrationTransformer(store, retriever, gen, target_version="2.2")

    clean = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n"
    result = transformer.transform(clean)

    assert gen.seen is None  # LLM never called
    assert retriever.called is False  # retrieval skipped
    assert result.ported_code == clean  # input returned unchanged
    assert result.changes == []
    assert result.deprecations_found == []
    assert result.repair_attempts == 0
    assert result.validation is not None and result.validation.passed
    assert any("No deprecated APIs detected" in w for w in result.warnings)


def test_passthrough_runs_sandbox_when_configured(store):
    # When nothing is deprecated, the input is still executed (honest "does it run?" signal).
    sandbox = _FakeSandbox([SandboxReport(backend="fake", ok=True, returncode=0)])
    transformer = MigrationTransformer(
        store, _FakeRetriever(), _FakeGenerator("unused"), target_version="2.2", sandbox=sandbox
    )

    result = transformer.transform("import math\nx = math.pi\n")

    assert result.execution is not None and result.execution.ok
    assert sandbox.i == 1  # the (unchanged) input was run exactly once


def test_runtime_deprecation_grounds_repair(store):
    # Root-2 closed loop: the sandbox fails on a 2.1-era TwoLocal DeprecationWarning the static
    # table never harvested; the loop must parse it, add it to the next generation's deps as an
    # authoritative runtime record, and surface it in the result — version-current by construction.
    twolocal_stderr = (
        "Traceback (most recent call last):\n"
        '  File "/work/snippet.py", line 1, in <module>\n'
        "DeprecationWarning: The class ``qiskit.circuit.library.n_local.two_local.TwoLocal`` is "
        "deprecated as of Qiskit 2.1. It will be removed in Qiskit 3.0. Use the function "
        "qiskit.circuit.library.n_local instead."
    )
    gen = _SeqGenerator(
        [LLMTransformOutput(ported_code=_GOOD), LLMTransformOutput(ported_code=_GOOD)]
    )
    sandbox = _FakeSandbox(
        [
            SandboxReport(
                backend="fake", ok=False, error_type="DeprecationWarning", stderr=twolocal_stderr
            ),
            SandboxReport(backend="fake", ok=True, returncode=0),
        ]
    )
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", sandbox=sandbox, max_repairs=2
    )

    result = transformer.transform("from qiskit import execute\nexecute(qc, backend)\n")

    assert result.repair_attempts == 1
    # The repair generation received the runtime-learned record in its authoritative deps.
    repair_deps = gen.deps_seen[1]
    learned = [d for d in repair_deps if d.symbol.endswith("TwoLocal")]
    assert learned and learned[0].source == "runtime-sandbox"
    assert learned[0].replacement == "qiskit.circuit.library.n_local"
    # It also surfaces in the result and in the repair feedback.
    assert any(h.symbol.endswith("TwoLocal") for h in result.deprecations_found)
    assert "TwoLocal" in (gen.feedbacks[1] or "")


def test_self_repair_gives_up_after_max_repairs(store):
    gen = _SeqGenerator([LLMTransformOutput(ported_code=_BAD)])  # never fixes
    sandbox = _FakeSandbox([SandboxReport(backend="fake", ok=False, error_type="ImportError")])
    transformer = MigrationTransformer(
        store, _FakeRetriever(), gen, target_version="2.2", sandbox=sandbox, max_repairs=2
    )

    result = transformer.transform(_OLD)

    assert result.repair_attempts == 2
    assert not result.validation.passed
