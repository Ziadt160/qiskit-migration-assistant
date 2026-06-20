"""Unit tests for AST-based Qiskit symbol extraction (pure logic, no network).

Core invariant (identity vs. name): a usage that resolves to a real import is matched
by its *full identity* and never contributes a bare last-segment key; only usages we
can't statically resolve (a method on an untyped object) fall back to the last segment.
"""

from __future__ import annotations

from src.migration.symbols import extract_symbols


def test_from_import_call_is_resolved_to_qualified():
    syms = extract_symbols("from qiskit import execute\nexecute(qc, backend)")
    assert "qiskit.execute" in syms.qualified
    assert "qiskit.execute" in syms.resolved
    assert "qiskit.execute" in syms.lookup_keys
    # An imported, called name matches by full identity — NOT a bare last-segment key.
    assert "execute" not in syms.lookup_keys


def test_resolved_import_does_not_emit_bare_last_segment():
    # from qiskit_aer import Aer; Aer.get_backend() must NOT produce a bare "Aer" key, so
    # it can never collide with the removed `qiskit.Aer` (the root false-positive class).
    syms = extract_symbols("from qiskit_aer import Aer\nb = Aer.get_backend('aer_simulator')")
    assert "qiskit_aer.Aer" in syms.qualified
    assert "qiskit_aer.Aer" in syms.resolved
    assert "qiskit_aer.Aer" in syms.lookup_keys
    assert "Aer" not in syms.lookup_keys


def test_old_aer_import_still_matches_by_full_identity():
    # The removed import is still detected — by full symbol, not by last segment.
    syms = extract_symbols("from qiskit import Aer\nb = Aer.get_backend('aer_simulator')")
    assert "qiskit.Aer" in syms.lookup_keys
    assert "qiskit.Aer" in syms.resolved


def test_module_attribute_chain_resolves_to_full_identity():
    syms = extract_symbols("import qiskit\nqiskit.execute(qc, backend)")
    assert "qiskit.execute" in syms.qualified
    assert "qiskit.execute" in syms.resolved
    assert "execute" not in syms.lookup_keys  # resolved -> full identity only


def test_import_as_alias_is_resolved():
    syms = extract_symbols("import qiskit.circuit as qc_mod\nqc_mod.QuantumCircuit(2)")
    assert "qiskit.circuit.QuantumCircuit" in syms.qualified
    assert "qiskit.circuit.QuantumCircuit" in syms.resolved


def test_bare_method_call_kept_as_last_segment():
    # `qc` is a local object we can't statically type -> the last segment is the only signal.
    syms = extract_symbols("qc.bind_parameters({theta: 0.5})")
    assert "bind_parameters" in syms.calls
    assert "bind_parameters" in syms.lookup_keys
    assert "qc.bind_parameters" not in syms.resolved  # never resolved -> last-segment fallback


def test_module_prefix_keys_still_emitted():
    # Module-level deprecations (qiskit.opflow) must still match a member import.
    syms = extract_symbols("from qiskit.opflow import X\nop = X ^ X")
    assert "qiskit.opflow.X" in syms.lookup_keys
    assert "qiskit.opflow" in syms.lookup_keys  # prefix key preserved
    assert "X" not in syms.lookup_keys  # resolved -> no bare last segment


def test_imports_recorded():
    syms = extract_symbols("from qiskit.providers.fake_provider import FakeProvider")
    assert "qiskit.providers.fake_provider.FakeProvider" in syms.imports
