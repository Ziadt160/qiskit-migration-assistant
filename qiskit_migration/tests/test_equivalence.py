"""Unit tests for the behavioral-equivalence check (hermetic — no Docker/Qiskit).

The harness output is faked, so these exercise the comparison logic and the
``check_equivalence`` control flow without running a real sandbox. A separate test
compiles the appended harness so a syntax error in the template can't slip through.
"""

from __future__ import annotations

import json
import math

from qiskit_migration.migration.equivalence import (
    _fidelity,
    build_harness,
    check_equivalence,
    compare_fingerprints,
    parse_fingerprints,
)
from qiskit_migration.migration.models import SandboxReport

_INV_SQRT2 = 1 / math.sqrt(2)

# A Bell state (|00> + |11>)/sqrt(2) as [real, imag] amplitude pairs.
_BELL = [[_INV_SQRT2, 0.0], [0.0, 0.0], [0.0, 0.0], [_INV_SQRT2, 0.0]]
# The same state with a global phase of i applied — must still score fidelity 1.
_BELL_PHASED = [[0.0, _INV_SQRT2], [0.0, 0.0], [0.0, 0.0], [0.0, _INV_SQRT2]]
# |00> — orthogonal-ish to Bell; overlap 1/sqrt(2) < threshold.
_ZERO = [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]


def _fp_stdout(circuits: dict) -> str:
    """Wrap a fingerprint dict the way the in-sandbox harness prints it."""
    return "ordinary user print\n__EQUIV_FP__" + json.dumps(circuits) + "__EQUIV_END__\n"


class _FakeSandbox:
    """A Sandbox returning canned stdout, ignoring the code it's handed."""

    backend = "docker"

    def __init__(self, stdout: str = "", ok: bool = True):
        self._stdout = stdout
        self._ok = ok

    def run(self, code, *, warnings_as_errors=True, max_capture=None):
        return SandboxReport(backend=self.backend, ok=self._ok, stdout=self._stdout)


# --- harness / parsing ---------------------------------------------------------


def test_build_harness_is_valid_python_and_substitutes_cap():
    harnessed = build_harness("x = 1\n", max_qubits=9)
    assert "x = 1" in harnessed
    assert "__MAX_QUBITS__" not in harnessed  # placeholder replaced
    assert "9" in harnessed
    compile(harnessed, "<harness>", "exec")  # would raise on a template syntax error


def test_parse_fingerprints_takes_last_block_and_ignores_noise():
    stdout = "noise\n" + _fp_stdout({"qc": {"status": "ok", "statevector": _BELL, "n_qubits": 2}})
    fps = parse_fingerprints(stdout)
    assert fps is not None and fps["qc"]["status"] == "ok"


def test_parse_fingerprints_returns_none_when_absent():
    assert parse_fingerprints("no sentinel here") is None


# --- fidelity ------------------------------------------------------------------


def test_fidelity_identical_is_one():
    assert _fidelity(_BELL, _BELL) == 1.0


def test_fidelity_invariant_to_global_phase():
    assert _fidelity(_BELL, _BELL_PHASED) > 0.999999


def test_fidelity_distinct_states_below_threshold():
    assert _fidelity(_BELL, _ZERO) < 0.999


def test_fidelity_dimension_mismatch_is_zero():
    assert _fidelity(_BELL, _ZERO[:2]) == 0.0


# --- comparison ----------------------------------------------------------------


def _ok(sv):
    return {"status": "ok", "statevector": sv, "n_qubits": 2}


def test_compare_equivalent_and_phase_equivalent():
    comps = compare_fingerprints(
        {"qc": _ok(_BELL)}, {"qc": _ok(_BELL_PHASED)}, fidelity_threshold=0.999
    )
    assert len(comps) == 1
    assert comps[0].status == "equivalent"
    assert comps[0].fidelity is not None and comps[0].fidelity > 0.999


def test_compare_not_equivalent():
    comps = compare_fingerprints({"qc": _ok(_BELL)}, {"qc": _ok(_ZERO)}, fidelity_threshold=0.999)
    assert comps[0].status == "not-equivalent"


def test_compare_missing_each_side():
    comps = compare_fingerprints({"a": _ok(_BELL)}, {"b": _ok(_BELL)}, fidelity_threshold=0.999)
    by_name = {c.name: c.status for c in comps}
    assert by_name == {"a": "missing-in-new", "b": "missing-in-old"}


def test_compare_carries_skip_reason():
    comps = compare_fingerprints(
        {"qc": {"status": "skipped:unbound-parameters", "n_qubits": 1}},
        {"qc": _ok(_ZERO)},
        fidelity_threshold=0.999,
    )
    assert comps[0].status == "skipped:unbound-parameters"


# --- end-to-end control flow ---------------------------------------------------


def test_check_equivalence_equivalent():
    old = _FakeSandbox(_fp_stdout({"qc": _ok(_BELL)}))
    new = _FakeSandbox(_fp_stdout({"qc": _ok(_BELL_PHASED)}))
    report = check_equivalence("old", "new", old, new)
    assert report.old_ran and report.new_ran
    assert report.equivalent is True
    assert "1/1" in report.note


def test_check_equivalence_divergent():
    old = _FakeSandbox(_fp_stdout({"qc": _ok(_BELL)}))
    new = _FakeSandbox(_fp_stdout({"qc": _ok(_ZERO)}))
    report = check_equivalence("old", "new", old, new)
    assert report.equivalent is False


def test_check_equivalence_undetermined_when_old_did_not_run():
    old = _FakeSandbox("crashed with no fingerprint", ok=False)
    new = _FakeSandbox(_fp_stdout({"qc": _ok(_BELL)}))
    report = check_equivalence("old", "new", old, new)
    assert report.old_ran is False
    assert report.equivalent is None


def test_check_equivalence_undetermined_when_nothing_comparable():
    skipped = {"qc": {"status": "skipped:too-large", "n_qubits": 20}}
    old = _FakeSandbox(_fp_stdout(skipped))
    new = _FakeSandbox(_fp_stdout(skipped))
    report = check_equivalence("old", "new", old, new)
    assert report.old_ran and report.new_ran
    assert report.equivalent is None
    assert "comparable" in report.note
