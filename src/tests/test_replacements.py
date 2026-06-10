"""Replacement-attachment from the flake8 map must be precise and verified.

Hermetic: a fixed fake mapping (no flake8 import) and a fake sandbox (no Docker). We test the
member-wise rename construction and the verify-before-attach gate.
"""

from __future__ import annotations

from src.migration.models import SandboxReport
from src.migration.replacements import enrich_records, propose_replacement


class FakeSandbox:
    backend = "fake"

    def __init__(self, importable: set[str]):
        self.importable = importable

    def run(self, code: str) -> SandboxReport:
        ok = any(repr(sym) in code for sym in self.importable)
        return SandboxReport(backend="fake", ok=ok, error_type=None if ok else "ImportError")


def test_propose_rename_applied_member_wise():
    m = {"qiskit.algorithms": "has moved; replace `qiskit.algorithms` with `qiskit_algorithms`"}
    assert propose_replacement("qiskit.algorithms", m) == "qiskit_algorithms"
    assert propose_replacement("qiskit.algorithms.VQE", m) == "qiskit_algorithms.VQE"


def test_propose_direct_only_for_exact_match():
    m = {"qiskit.qasm": "{} has been removed; use qiskit.qasm2 instead"}
    assert propose_replacement("qiskit.qasm", m) == "qiskit.qasm2"
    # a member of the key is a prefix match but the direct replacement describes the module,
    # so it must NOT be applied to the member.
    assert propose_replacement("qiskit.qasm.Foo", m) is None


def test_propose_moved_to_and_no_match():
    m = {"qiskit.x.Y": "{} has moved to `qiskit.synthesis.Y`"}
    assert propose_replacement("qiskit.x.Y", m) == "qiskit.synthesis.Y"
    assert propose_replacement("qiskit.unrelated", m) is None
    assert propose_replacement("qiskit.anything", {}) is None


def test_enrich_attaches_only_verified_replacements():
    mapping = {"qiskit.opflow": "has moved; replace `qiskit.opflow` with `qiskit.quantum_info`"}
    records = [
        {"symbol": "qiskit.opflow.X", "replacement": None},  # -> quantum_info.X, imports -> attach
        {"symbol": "qiskit.opflow.Y", "replacement": None},  # -> quantum_info.Y, missing -> skip
        {"symbol": "qiskit.elsewhere", "replacement": None},  # no proposal
        {"symbol": "qiskit.opflow.Z", "replacement": "kept"},  # already has one -> untouched
    ]
    sandbox = FakeSandbox(importable={"qiskit.quantum_info.X"})

    stats = enrich_records(records, sandbox, mapping)

    assert stats == {"proposed": 2, "verified": 1}  # X and Y proposed; only X imports
    assert records[0]["replacement"] == "qiskit.quantum_info.X"
    assert records[1]["replacement"] is None  # proposed but did not import -> not attached
    assert records[2]["replacement"] is None  # no proposal
    assert records[3]["replacement"] == "kept"  # pre-existing left alone
