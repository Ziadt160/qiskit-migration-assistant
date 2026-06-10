"""The harvester must turn Griffe breakages into verified, promotable records.

Hermetic: no Griffe (Stage 1 mining is mocked at the breakage level) and no Docker (a fake
sandbox scripts which symbols survive on the target). We test the pure stages — candidate
generation and verify-then-promote — which is where the logic lives.
"""

from __future__ import annotations

import pytest

from src.migration.deprecations import DeprecationStore, _score
from src.migration.harvest import (
    HarvestReport,
    _candidates_from_breakages,
    _extract_replacement,
    _is_public,
    harvest_candidates,
)
from src.migration.models import SandboxReport


class FakeSandbox:
    """ok=True only for probes targeting a symbol in `present` (simulates the target)."""

    backend = "fake"

    def __init__(self, present: set[str]):
        self.present = present

    def run(self, code: str) -> SandboxReport:
        ok = any(repr(sym) in code for sym in self.present)
        return SandboxReport(backend=self.backend, ok=ok, error_type=None if ok else "ImportError")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Use `cx` instead.", "cx"),
        ("Instead, use QuantumCircuit.cx().", "QuantumCircuit.cx"),
        ("This was replaced by PauliList in 1.0.", "PauliList"),
        ("Deprecated with no guidance.", None),
        (None, None),
    ],
)
def test_extract_replacement(text, expected):
    assert _extract_replacement(text) == expected


def test_is_public():
    assert _is_public("qiskit.opflow")
    assert _is_public("qiskit.circuit.QuantumCircuit.cnot")
    assert not _is_public("qiskit._accelerate.foo")
    assert not _is_public("qiskit.circuit._utils.helper")


def test_candidates_from_breakages_filters_and_enriches():
    breakages = [
        {"kind": "OBJECT_REMOVED", "object_path": "qiskit.opflow"},
        {"kind": "OBJECT_REMOVED", "object_path": "qiskit.opflow"},  # dupe -> collapsed
        {"kind": "OBJECT_REMOVED", "object_path": "qiskit._internal.thing"},  # private -> dropped
        {"kind": "PARAMETER_REMOVED", "object_path": "qiskit.transpile"},  # not a removal, drop
        {"kind": "OBJECT_REMOVED", "object_path": "qiskit.circuit.QuantumCircuit.cnot"},
    ]
    docs = {"qiskit.circuit.QuantumCircuit.cnot": "Removed. Use `cx` instead."}
    cands = _candidates_from_breakages(breakages, "2.0.2", lambda p: docs.get(p))

    symbols = [c["symbol"] for c in cands]
    assert symbols == ["qiskit.opflow", "qiskit.circuit.QuantumCircuit.cnot"]
    assert all(c["status"] == "removed" and c["removed_in"] == "2.0.2" for c in cands)
    assert cands[0]["replacement"] is None  # no docstring hint
    assert cands[1]["replacement"] == "cx"  # extracted from docstring


def test_harvest_candidates_verifies_and_promotes(tmp_path):
    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    candidates = [
        # old gone + replacement imports clean -> promoted with replacement
        {"symbol": "qiskit.opflow", "replacement": "qiskit.quantum_info", "removed_in": "2.0.2"},
        # old still present on target -> rejected
        {"symbol": "qiskit.transpile", "replacement": None, "removed_in": "2.0.2"},
        # old gone, no replacement -> promoted as removed-only
        {"symbol": "qiskit.tools.job_monitor", "replacement": None, "removed_in": "2.0.2"},
        # old gone, but the replacement hypothesis is garbage -> still promoted, replacement dropped
        {"symbol": "qiskit.assemble", "replacement": "qiskit.bogus_xyz", "removed_in": "2.0.2"},
    ]
    sandbox = FakeSandbox(present={"qiskit.quantum_info", "qiskit.transpile"})

    report = harvest_candidates(candidates, sandbox, store=store)

    assert isinstance(report, HarvestReport)
    assert report.mined == 4
    assert report.verified == 3  # opflow + job_monitor + assemble; transpile rejected
    assert report.promoted == 3
    promoted = {r.symbol: r for r in report.records}
    assert set(promoted) == {"qiskit.opflow", "qiskit.tools.job_monitor", "qiskit.assemble"}
    assert all(r.source == "sandbox-verified" for r in report.records)
    assert promoted["qiskit.opflow"].replacement == "qiskit.quantum_info"
    assert promoted["qiskit.tools.job_monitor"].replacement is None
    assert promoted["qiskit.assemble"].replacement is None  # garbage replacement dropped
    assert store.count() == 3


def test_harvest_candidates_dry_run_without_store():
    candidates = [{"symbol": "qiskit.opflow", "replacement": None, "removed_in": "2.0.2"}]
    report = harvest_candidates(candidates, FakeSandbox(present=set()))
    assert report.verified == 1
    assert report.promoted == 0  # nothing written without a store


def test_sandbox_verified_outranks_parser_but_not_seed():
    from src.migration.deprecations import DeprecationRecord

    seed = DeprecationRecord("x", "removed", source="curated-seed")
    verified = DeprecationRecord("x", "removed", source="sandbox-verified")
    parsed = DeprecationRecord("x", "removed", source="release-note")
    assert _score(seed) > _score(verified) > _score(parsed)
