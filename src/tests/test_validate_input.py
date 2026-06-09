"""Unit tests for input guardrails (pure logic, no network)."""

from __future__ import annotations

import pytest

from src.migration.validate_input import InputValidationError, validate_input


def test_valid_code_returns_no_warnings():
    assert validate_input("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)") == []


def test_empty_raises():
    with pytest.raises(InputValidationError):
        validate_input("   \n  ")


def test_invalid_python_raises():
    with pytest.raises(InputValidationError):
        validate_input("def f(:\n    pass")


def test_too_long_raises():
    with pytest.raises(InputValidationError):
        validate_input("x = 1\n" * 1000, max_chars=100)


def test_secret_is_warned_not_raised():
    warnings = validate_input("api_key = 'abcdef1234567890ABCDEF'\nprint(api_key)")
    assert warnings and "secret" in warnings[0].lower()
