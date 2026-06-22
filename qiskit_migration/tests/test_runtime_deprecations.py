"""Unit tests for runtime deprecation capture (hermetic — faked sandbox, no Docker)."""

from __future__ import annotations

import json

from qiskit_migration.migration.models import SandboxReport
from qiskit_migration.migration.runtime_deprecations import (
    _parse_message,
    build_capture_harness,
    capture_runtime_deprecations,
    parse_warnings,
)

# Real-shape Qiskit deprecation messages.
_MSG_OPFLOW = (
    "The class ``qiskit.opflow.state_fns.circuit_state_fn.CircuitStateFn`` is deprecated as of "
    "qiskit-terra 0.24.0. It will be removed in the Qiskit 1.0 release. For code migration "
    "guidelines, visit https://qisk.it/opflow_migration."
)
_MSG_BIND = (
    "The method ``qiskit.circuit.quantumcircuit.QuantumCircuit.bind_parameters()`` is deprecated "
    "as of qiskit 0.45.0. It will be removed in the Qiskit 1.0.0 release. Use assign_parameters() "
    "instead"
)
_MSG_ALGO = (
    "``qiskit.utils.algorithm_globals`` is deprecated as of qiskit 0.45.0. It will be removed in "
    "Qiskit 1.0. Install qiskit_algorithms and use ``qiskit_algorithms.utils`` instead."
)


def _stdout(warnings: list[dict], started: bool = True) -> str:
    """Render the harness's incremental output: a start marker + one sentinel per warning."""
    lines = ["some user output"]
    if started:
        lines.append("__RTSTART__")
    for w in warnings:
        lines.append("__RTDEP1__" + json.dumps(w) + "__RTEND1__")
    return "\n".join(lines) + "\n"


class _FakeSandbox:
    backend = "docker"

    def __init__(self, stdout: str = "", ok: bool = True, error_type: str | None = None):
        self._stdout, self._ok, self._err = stdout, ok, error_type

    def run(self, code, *, warnings_as_errors=True, max_capture=None):
        return SandboxReport(
            backend=self.backend, ok=self._ok, stdout=self._stdout, error_type=self._err
        )


# --- harness / parse ----------------------------------------------------------


def test_build_harness_prepends_and_compiles():
    harnessed = build_capture_harness("x = 1\n")
    assert harnessed.rstrip().endswith("x = 1")
    assert "showwarning" in harnessed and "__RTSTART__" in harnessed
    compile(harnessed, "<harness>", "exec")  # the prepended harness must be valid Python


def test_parse_warnings_collects_all_records():
    out = _stdout(
        [
            {"category": "DeprecationWarning", "message": "x"},
            {"category": "DeprecationWarning", "message": "y"},
        ]
    )
    recs = parse_warnings(out)
    assert [r["message"] for r in recs] == ["x", "y"]


def test_parse_warnings_empty_when_absent():
    assert parse_warnings("no sentinel") == []


# --- message parsing ----------------------------------------------------------


def test_parse_message_with_use_instead():
    symbol, replacement, since, removed = _parse_message(_MSG_BIND)
    assert symbol.endswith("QuantumCircuit.bind_parameters")
    assert replacement == "assign_parameters"
    assert since == "0.45.0"
    assert removed == "1.0.0"


def test_parse_message_second_backtick_replacement():
    symbol, replacement, since, removed = _parse_message(_MSG_ALGO)
    assert symbol == "qiskit.utils.algorithm_globals"
    assert replacement == "qiskit_algorithms.utils"
    assert removed == "1.0"


def test_parse_message_no_replacement():
    symbol, replacement, _since, removed = _parse_message(_MSG_OPFLOW)
    assert symbol.endswith("CircuitStateFn")
    assert replacement is None
    assert removed == "1.0"


def test_parse_message_rejects_stopword_replacement():
    # "use the simulators ..." must not yield replacement="the".
    msg = "``qiskit.providers.basicaer.X`` is deprecated. Use the simulators from qiskit instead."
    _symbol, replacement, _since, _removed = _parse_message(msg)
    assert replacement != "the"


# --- end to end ---------------------------------------------------------------


def test_capture_dedups_and_filters_categories():
    warns = [
        {"category": "DeprecationWarning", "message": _MSG_BIND, "filename": "x", "lineno": 1},
        {
            "category": "DeprecationWarning",
            "message": _MSG_BIND,
            "filename": "x",
            "lineno": 9,
        },  # dup
        {"category": "UserWarning", "message": "not a deprecation", "filename": "x", "lineno": 2},
        {"category": "DeprecationWarning", "message": _MSG_ALGO, "filename": "x", "lineno": 3},
    ]
    report = capture_runtime_deprecations("code", _FakeSandbox(_stdout(warns)))
    assert report.ran is True
    syms = [d.symbol for d in report.deprecations]
    assert len(report.deprecations) == 2  # dup collapsed, UserWarning filtered
    assert any(s.endswith("bind_parameters") for s in syms)
    assert "qiskit.utils.algorithm_globals" in syms
    assert report.deprecations[0].message  # full message preserved


def test_capture_reports_not_ran_without_sentinel():
    report = capture_runtime_deprecations(
        "code", _FakeSandbox("boom", ok=False, error_type="ImportError")
    )
    assert report.ran is False
    assert report.deprecations == []
    assert "ImportError" in report.note
