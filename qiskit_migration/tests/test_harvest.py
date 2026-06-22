"""The harvester must turn Griffe breakages into verified, promotable records.

Hermetic: no Griffe (Stage 1 mining is mocked at the breakage level) and no Docker (a fake
sandbox scripts which symbols survive on the target). We test the pure stages — candidate
generation and verify-then-promote — which is where the logic lives.
"""

from __future__ import annotations

import pytest

from qiskit_migration.migration.deprecations import DeprecationStore, _score
from qiskit_migration.migration.harvest import (
    HarvestReport,
    _candidates_from_breakages,
    _extract_replacement,
    _is_public,
    cross_package_candidates,
    harvest_candidates,
)
from qiskit_migration.migration.models import SandboxReport


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
        ("Instead, use the new API.", None),  # stopword "the" skipped, not proposed
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
    cands = _candidates_from_breakages(
        breakages, "2.0.2", lambda p: docs.get(p), dedupe_by_segment=False
    )

    by_symbol = {c["symbol"]: c for c in cands}
    assert set(by_symbol) == {"qiskit.opflow", "qiskit.circuit.QuantumCircuit.cnot"}
    assert all(c["status"] == "removed" and c["removed_in"] == "2.0.2" for c in cands)
    assert by_symbol["qiskit.opflow"]["replacement"] is None  # no docstring hint
    assert by_symbol["qiskit.circuit.QuantumCircuit.cnot"]["replacement"] == "cx"


def test_candidates_dedupe_by_segment_keeps_shortest():
    # An inherited method removed from many classes collapses to the most-canonical (shortest)
    # path; a uniquely-named removal is kept. Detection matches on last segment, so no signal lost.
    breakages = [
        {
            "kind": "OBJECT_REMOVED",
            "object_path": "qiskit.circuit.library.n_local.TwoLocal.diagonal",
        },
        {
            "kind": "OBJECT_REMOVED",
            "object_path": "qiskit.circuit.quantumcircuit.QuantumCircuit.diagonal",
        },
        {"kind": "OBJECT_REMOVED", "object_path": "qiskit.transpiler.synthesis.graysynth"},
    ]
    cands = _candidates_from_breakages(breakages, "2.0.2")  # dedupe on by default
    assert sorted(c["symbol"] for c in cands) == [
        "qiskit.circuit.quantumcircuit.QuantumCircuit.diagonal",
        "qiskit.transpiler.synthesis.graysynth",
    ]


def test_cross_package_candidates_name_matches_to_ecosystem():
    # A moved-same-name symbol becomes a candidate pointing at its ecosystem home; a symbol
    # with no ecosystem match and a skipped utility/base name are both dropped.
    old_symbols = {
        "VQE": "qiskit.aqua.algorithms.VQE",
        "COBYLA": "qiskit.aqua.components.optimizers.COBYLA",
        "AquaError": "qiskit.aqua.aqua_error.AquaError",  # utility -> skipped
        "SomeAquaOnlyThing": "qiskit.aqua.legacy.SomeAquaOnlyThing",  # no eco match -> dropped
    }
    eco_index = {
        "VQE": "qiskit_algorithms.VQE",
        "COBYLA": "qiskit_algorithms.optimizers.COBYLA",
        "AquaError": "qiskit_algorithms.AquaError",  # exists but the source name is skipped
    }
    cands = cross_package_candidates(
        old_symbols, eco_index, skip_names={"AquaError"}, removed_in="1.0"
    )
    by_symbol = {c["symbol"]: c for c in cands}
    assert set(by_symbol) == {
        "qiskit.aqua.algorithms.VQE",
        "qiskit.aqua.components.optimizers.COBYLA",
    }
    vqe = by_symbol["qiskit.aqua.algorithms.VQE"]
    assert vqe["replacement"] == "qiskit_algorithms.VQE"
    assert vqe["status"] == "moved" and vqe["removed_in"] == "1.0"


def test_cross_package_candidates_feed_verify_and_promote(tmp_path):
    # End-to-end on the pure stages: a name-matched candidate whose old path is absent on the
    # target and whose ecosystem replacement imports gets promoted as a 'moved' record; a
    # coincidental name collision whose old path still resolves on the target is rejected.
    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    old_symbols = {
        "VQE": "qiskit.aqua.algorithms.VQE",
        "transpile": "qiskit.aqua.transpile",  # collides with a live target symbol -> rejected
    }
    eco_index = {"VQE": "qiskit_algorithms.VQE", "transpile": "qiskit.transpile"}
    candidates = cross_package_candidates(old_symbols, eco_index)
    # FakeSandbox: replacement imports clean; the aqua paths are absent EXCEPT the colliding
    # 'qiskit.aqua.transpile' is (wrongly) reported present to simulate a non-removal.
    sandbox = FakeSandbox(present={"qiskit_algorithms.VQE", "qiskit.aqua.transpile"})

    report = harvest_candidates(candidates, sandbox, store=store, method="cross-package name-match")

    assert {r.symbol for r in report.records} == {"qiskit.aqua.algorithms.VQE"}
    rec = report.records[0]
    assert rec.status == "moved"
    assert rec.replacement == "qiskit_algorithms.VQE"
    assert "cross-package name-match" in rec.note and "moved" in rec.note


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
    from qiskit_migration.migration.deprecations import DeprecationRecord

    seed = DeprecationRecord("x", "removed", source="curated-seed")
    verified = DeprecationRecord("x", "removed", source="sandbox-verified")
    parsed = DeprecationRecord("x", "removed", source="release-note")
    assert _score(seed) > _score(verified) > _score(parsed)


def test_sandbox_verified_matches_full_symbol_only(tmp_path):
    # Auto-harvested records must NOT match by last segment (their names aren't vetted), so a
    # removed `qiskit.pulse.cx` can't false-flag the live `QuantumCircuit.cx`. Curated records
    # keep last-segment matching.
    from qiskit_migration.migration.deprecations import DeprecationRecord, DeprecationStore

    store = DeprecationStore(str(tmp_path / "d.db"))
    store.create()
    store.upsert_many(
        [
            DeprecationRecord("qiskit.pulse.cx", "removed", source="sandbox-verified"),
            DeprecationRecord("QuantumCircuit.cnot", "removed", source="curated-seed"),
        ]
    )
    assert {r.symbol for r in store.lookup({"qiskit.pulse.cx"})} == {"qiskit.pulse.cx"}
    assert store.lookup({"cx"}) == []  # bare last-segment must not hit the auto record
    assert {r.symbol for r in store.lookup({"cnot"})} == {"QuantumCircuit.cnot"}  # seed still does
