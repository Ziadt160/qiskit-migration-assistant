"""The execution-verification gate must turn sandbox runs into the right verdict.

Hermetic: no Docker. A fake sandbox decides a probe "passes" iff the symbol it targets is
in a configured `present` set — letting us script removed/surviving symbols and assert the
gate never auto-verifies anything the target doesn't actually support.
"""

from __future__ import annotations

import ast

import pytest

from qiskit_migration.migration.models import SandboxReport
from qiskit_migration.migration.verify_record import (
    make_probe,
    verify_candidate,
    verify_candidates,
)


class FakeSandbox:
    """Returns ok=True only when the probed symbol is in `present` (simulates the target)."""

    backend = "fake"

    def __init__(self, present: set[str]):
        self.present = present
        self.calls: list[str] = []

    def run(self, code: str) -> SandboxReport:
        self.calls.append(code)
        ok = any(repr(sym) in code for sym in self.present)
        return SandboxReport(
            backend=self.backend,
            ok=ok,
            returncode=0 if ok else 1,
            error_type=None if ok else "ImportError",
        )


@pytest.mark.parametrize(
    "symbol",
    [
        "qiskit.execute",  # top-level function
        "QuantumCircuit.cnot",  # bare Class.method
        "qiskit.opflow",  # module
        "qiskit.utils.QuantumInstance",  # module.Class
        "qiskit_aer",  # single-segment module
    ],
)
def test_make_probe_is_valid_python_and_embeds_symbol(symbol):
    code = make_probe(symbol)
    ast.parse(code)  # raises if not valid Python
    assert repr(symbol) in code


def test_make_probe_rejects_non_identifier():
    with pytest.raises(ValueError):
        make_probe("qiskit_aer save instructions")


def test_verified_when_old_gone_and_replacement_imports():
    # Old symbol absent on target; replacement present -> verified.
    sandbox = FakeSandbox(present={"QuantumCircuit.cx"})
    v = verify_candidate("QuantumCircuit.cnot", "QuantumCircuit.cx", sandbox)
    assert v.verified is True
    assert v.old_present is False
    assert v.replacement_ok is True
    assert len(sandbox.calls) == 2  # probed both old and new


def test_rejected_when_old_symbol_still_present():
    # The candidate claims a removal, but the symbol still imports clean -> reject.
    sandbox = FakeSandbox(present={"qiskit.execute", "QuantumCircuit.cx"})
    v = verify_candidate("qiskit.execute", "QuantumCircuit.cx", sandbox)
    assert v.verified is False
    assert v.old_present is True
    assert "still imports" in v.reason


def test_rejected_when_replacement_broken():
    # Old gone (good) but the proposed replacement does not import -> reject.
    sandbox = FakeSandbox(present=set())
    v = verify_candidate("qiskit.opflow", "qiskit.quantum_info.NotAThing", sandbox)
    assert v.verified is False
    assert v.old_present is False
    assert v.replacement_ok is False
    assert "replacement does not import" in v.reason


def test_verified_with_no_replacement():
    # Removed-with-no-replacement (e.g. job_monitor): old-gone alone suffices.
    sandbox = FakeSandbox(present=set())
    v = verify_candidate("qiskit.tools.job_monitor", None, sandbox)
    assert v.verified is True
    assert v.replacement_ok is None
    assert len(sandbox.calls) == 1  # no replacement probe


def test_unverifiable_replacement_routed_to_review():
    # A conceptual replacement that isn't a directly importable symbol must not auto-verify.
    sandbox = FakeSandbox(present=set())
    v = verify_candidate("qiskit.execute", "backend.run instead", sandbox)
    assert v.verified is False
    assert v.replacement_ok is None  # never probed
    assert "needs human review" in v.reason
    assert len(sandbox.calls) == 1  # only the old probe ran


def test_inconclusive_old_probe_is_not_verified():
    # Old probe fails with no recognizable absence error (sandbox/infra failure) -> never
    # treated as "removed", so an outage can't poison the table.
    class InfraFailSandbox:
        backend = "fake"

        def run(self, code: str) -> SandboxReport:
            return SandboxReport(backend="fake", ok=False, error_type=None)

    v = verify_candidate("qiskit.opflow", None, InfraFailSandbox())
    assert v.verified is False
    assert v.old_absent is False
    assert "inconclusive" in v.reason


def test_verify_candidates_batch():
    sandbox = FakeSandbox(present={"QuantumCircuit.cx"})
    candidates = [
        {"symbol": "QuantumCircuit.cnot", "replacement": "QuantumCircuit.cx"},  # verified
        {"symbol": "QuantumCircuit.cx", "replacement": "QuantumCircuit.cx"},  # old present, reject
        {"symbol": "qiskit.tools.job_monitor"},  # no replacement, old gone -> ok
    ]
    verdicts = verify_candidates(candidates, sandbox)
    assert [v.verified for v in verdicts] == [True, False, True]
