"""Unit tests for AST-based Qiskit symbol extraction (pure logic, no network)."""

from __future__ import annotations

from src.migration.symbols import extract_symbols


def test_from_import_call_is_resolved_to_qualified():
    syms = extract_symbols("from qiskit import execute\nexecute(qc, backend)")
    assert "qiskit.execute" in syms.qualified
    assert "execute" in syms.calls
    assert "execute" in syms.lookup_keys
    assert "qiskit.execute" in syms.lookup_keys


def test_from_import_name_reference_resolved():
    syms = extract_symbols("from qiskit import Aer\nb = Aer.get_backend('aer_simulator')")
    assert "qiskit.Aer" in syms.qualified
    assert "qiskit.Aer.get_backend" in syms.attributes
    assert "Aer" in syms.lookup_keys


def test_module_attribute_chain():
    syms = extract_symbols("import qiskit\nqiskit.execute(qc, backend)")
    assert "qiskit.execute" in syms.qualified
    assert "execute" in syms.calls


def test_import_as_alias_is_resolved():
    syms = extract_symbols("import qiskit.circuit as qc_mod\nqc_mod.QuantumCircuit(2)")
    assert "qiskit.circuit.QuantumCircuit" in syms.qualified


def test_bare_method_call_kept_as_last_segment():
    syms = extract_symbols("qc.bind_parameters({theta: 0.5})")
    assert "bind_parameters" in syms.calls
    assert "bind_parameters" in syms.lookup_keys


def test_imports_recorded():
    syms = extract_symbols("from qiskit.providers.fake_provider import FakeProvider")
    assert "qiskit.providers.fake_provider.FakeProvider" in syms.imports
