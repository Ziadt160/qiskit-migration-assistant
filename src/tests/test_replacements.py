"""Replacement-attachment from the flake8 map must be precise and verified.

Hermetic: a fixed fake mapping (no flake8 import) and a fake sandbox (no Docker). We test the
member-wise rename construction and the verify-before-attach gate.
"""

from __future__ import annotations

from src.migration.models import SandboxReport
from src.migration.replacements import (
    enrich_records,
    load_guide_replacements,
    propose_from_guide,
    propose_replacement,
)


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


def test_load_guide_replacements_parses_table(tmp_path):
    md = tmp_path / "guide.mdx"
    md.write_text(
        "Some prose.\n\n"
        "| Removed | Alternative |\n"
        "|---|---|\n"
        "| `QuantumCircuit.cnot` | [`QuantumCircuit.cx`](qiskit.circuit.QuantumCircuit#cx) |\n"
        "| `QuantumCircuit.diagonal` | [`DiagonalGate`](qiskit.circuit.library.DiagonalGate) |\n"
        "| not-a-symbol | skip |\n",
        encoding="utf-8",
    )
    guide = load_guide_replacements([md])
    # the link URL gives the full importable path (anchor -> trailing segment)
    assert guide["QuantumCircuit.cnot"] == "qiskit.circuit.QuantumCircuit.cx"
    assert guide["QuantumCircuit.diagonal"] == "qiskit.circuit.library.DiagonalGate"
    assert "not-a-symbol" not in guide


def test_propose_from_guide_suffix_match():
    guide = {"QuantumCircuit.cnot": "qiskit.circuit.QuantumCircuit.cx"}
    # harvested symbols carry the full internal path; match the table key by suffix
    assert (
        propose_from_guide("qiskit.circuit.quantumcircuit.QuantumCircuit.cnot", guide)
        == "qiskit.circuit.QuantumCircuit.cx"
    )
    assert propose_from_guide("qiskit.circuit.QuantumCircuit.cx", guide) is None


def test_enrich_falls_back_to_guide_map():
    guide = {"QuantumCircuit.cnot": "qiskit.circuit.QuantumCircuit.cx"}
    records = [{"symbol": "qiskit.circuit.quantumcircuit.QuantumCircuit.cnot", "replacement": None}]
    sandbox = FakeSandbox(importable={"qiskit.circuit.QuantumCircuit.cx"})
    stats = enrich_records(records, sandbox, {}, guide_map=guide)  # empty flake8 map
    assert stats == {"proposed": 1, "verified": 1}
    assert records[0]["replacement"] == "qiskit.circuit.QuantumCircuit.cx"


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
