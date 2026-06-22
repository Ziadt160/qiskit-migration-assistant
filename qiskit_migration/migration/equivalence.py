"""Behavioral-equivalence check — proof a migration preserves *meaning*, not just imports.

The sandbox (``sandbox.py``) answers "does the ported code run on the target Qiskit?".
This module answers the harder, more valuable question: "does it still *do the same thing*?"

Strategy — **old-on-old vs new-on-new**:

  1. Run the ORIGINAL code on an OLD Qiskit (``Dockerfile.sandbox-legacy``, e.g. 0.46.3)
     and the MIGRATED code on the TARGET Qiskit (``Dockerfile.sandbox``, 2.x).
  2. In each run, a small harness (appended to the user code) inspects the module
     namespace for every :class:`~qiskit.QuantumCircuit` it built and emits a
     **statevector fingerprint** — the state the circuit prepares from |0…0⟩, with any
     final measurements stripped. The statevector is *physics*, so it is stable across
     Qiskit versions even when the circuit is *built* with different (renamed) APIs.
  3. On the host, match circuits by variable name and compare each pair by
     **fidelity** ``|⟨ψ_old|ψ_new⟩|``. Fidelity is invariant to global phase (a free
     parameter of any quantum state) and, unlike a hash, tolerant to the sub-ULP float
     noise two different library versions produce — so equivalent states score ≈ 1.0.

Honest scope (matches the project's verification-first ethos — we never *claim*
equivalence we can't measure):

  * Only circuits that reduce to a pure state from |0…0⟩ are compared. Circuits with
    unbound parameters, mid-circuit measurement/reset, or more than ``max_qubits``
    qubits are reported ``skipped:<reason>`` and excluded from the verdict.
  * If either side fails to run, or no circuit lines up by name, the verdict is
    ``None`` (undetermined) — never a false "equivalent".

The check is informational by default; it is the strongest *correctness* signal the
system can produce short of a full property-based test, and it reuses the existing
sandbox infrastructure wholesale.
"""

from __future__ import annotations

import json
import logging
import re

import numpy as np

from qiskit_migration.config import get_settings
from qiskit_migration.migration.models import CircuitComparison, EquivalenceReport
from qiskit_migration.migration.sandbox import Sandbox

logger = logging.getLogger(__name__)

# Generous stdout budget for the fingerprint JSON: a 12-qubit statevector is 4096 complex
# amplitudes (~150 KB serialized), well over the sandbox's default 4 KB capture cap.
_FP_CAPTURE = 4_000_000

_FP_BEGIN = "__EQUIV_FP__"
_FP_END = "__EQUIV_END__"
_FP_RE = re.compile(re.escape(_FP_BEGIN) + r"(.*?)" + re.escape(_FP_END), re.DOTALL)

# Appended to the user code. Runs in the user module's globals (so it sees their circuits),
# uses only APIs present in both 0.46 and 2.x (QuantumCircuit, Statevector.from_instruction,
# remove_final_measurements, num_parameters), and prints exactly one sentinel-wrapped JSON
# object — even on failure — so the host can always parse a result out of the run.
_HARNESS = """

def __equiv_emit__():
    import json as __json

    try:
        from qiskit import QuantumCircuit as __QC
        from qiskit.quantum_info import Statevector as __SV
    except Exception as __e:  # pragma: no cover - exercised only on a broken image
        print("__EQUIV_FP__" + __json.dumps({"__harness_error__": repr(__e)}) + "__EQUIV_END__")
        return

    __max_q = __MAX_QUBITS__
    __out = {}
    for __name, __val in list(globals().items()):
        if __name.startswith("__"):
            continue
        if not isinstance(__val, __QC):
            continue
        __entry = {"n_qubits": getattr(__val, "num_qubits", None)}
        try:
            if getattr(__val, "num_parameters", 0):
                __entry["status"] = "skipped:unbound-parameters"
            elif __val.num_qubits > __max_q:
                __entry["status"] = "skipped:too-large"
            else:
                __c = __val.copy()
                try:
                    __c.remove_final_measurements(inplace=True)
                except Exception:
                    pass
                __sv = __SV.from_instruction(__c)
                __entry["status"] = "ok"
                __entry["statevector"] = [[float(__z.real), float(__z.imag)] for __z in __sv.data]
        except Exception as __ex:
            __entry["status"] = "error:" + type(__ex).__name__
        __out[__name] = __entry
    print("__EQUIV_FP__" + __json.dumps(__out) + "__EQUIV_END__")


__equiv_emit__()
"""


def build_harness(user_code: str, max_qubits: int) -> str:
    """Return ``user_code`` with the fingerprint harness appended."""
    harness = _HARNESS.replace("__MAX_QUBITS__", str(int(max_qubits)))
    # Guard against the (rare) user file with no trailing newline running into the harness.
    return user_code.rstrip("\n") + "\n" + harness


def parse_fingerprints(stdout: str) -> dict[str, dict] | None:
    """Extract the harness's fingerprint object from sandbox stdout, or None if absent.

    Takes the last sentinel block, so ordinary user ``print`` output before it is ignored.
    """
    matches = _FP_RE.findall(stdout or "")
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fidelity(a: list[list[float]], b: list[list[float]]) -> float:
    """State fidelity ``|⟨a|b⟩|`` for two statevectors given as [real, imag] pairs.

    Both are renormalized defensively; returns 0.0 on a dimension mismatch (different
    qubit counts can't be the same state). Invariant to global phase by construction.
    """
    va = np.array([complex(re, im) for re, im in a], dtype=complex)
    vb = np.array([complex(re, im) for re, im in b], dtype=complex)
    if va.shape != vb.shape or va.size == 0:
        return 0.0
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    # Clamp the sub-ULP overshoot from normalization; fidelity is physically in [0, 1].
    return float(min(1.0, np.abs(np.vdot(va / na, vb / nb))))


def compare_fingerprints(
    old: dict[str, dict],
    new: dict[str, dict],
    *,
    fidelity_threshold: float,
) -> list[CircuitComparison]:
    """Match circuits by variable name and compare each pair by statevector fidelity."""
    comparisons: list[CircuitComparison] = []
    for name in sorted(set(old) | set(new)):
        o = old.get(name)
        n = new.get(name)
        if o is None:
            comparisons.append(CircuitComparison(name=name, status="missing-in-old"))
            continue
        if n is None:
            comparisons.append(CircuitComparison(name=name, status="missing-in-new"))
            continue
        n_qubits = o.get("n_qubits") if o.get("n_qubits") is not None else n.get("n_qubits")
        if o.get("status") != "ok" or n.get("status") != "ok":
            # Carry whichever side couldn't be fingerprinted so the reason is visible.
            reason = o.get("status") if o.get("status") != "ok" else n.get("status")
            comparisons.append(CircuitComparison(name=name, status=str(reason), n_qubits=n_qubits))
            continue
        fid = _fidelity(o["statevector"], n["statevector"])
        comparisons.append(
            CircuitComparison(
                name=name,
                status="equivalent" if fid >= fidelity_threshold else "not-equivalent",
                fidelity=fid,
                n_qubits=n_qubits,
            )
        )
    return comparisons


def check_equivalence(
    original_code: str,
    ported_code: str,
    old_sandbox: Sandbox,
    new_sandbox: Sandbox,
    *,
    fidelity_threshold: float | None = None,
    max_qubits: int | None = None,
) -> EquivalenceReport:
    """Run both versions, fingerprint their circuits, and compare behavior.

    ``old_sandbox`` must target an *old* Qiskit that can run ``original_code``;
    ``new_sandbox`` targets the migration target. Both are run with deprecation
    warnings demoted (old code legitimately warns) and an enlarged stdout capture.
    """
    settings = get_settings()
    threshold = (
        settings.equivalence_fidelity_threshold
        if fidelity_threshold is None
        else fidelity_threshold
    )
    max_q = settings.equivalence_max_qubits if max_qubits is None else max_qubits

    old_report = old_sandbox.run(
        build_harness(original_code, max_q), warnings_as_errors=False, max_capture=_FP_CAPTURE
    )
    new_report = new_sandbox.run(
        build_harness(ported_code, max_q), warnings_as_errors=False, max_capture=_FP_CAPTURE
    )

    old_fps = parse_fingerprints(old_report.stdout)
    new_fps = parse_fingerprints(new_report.stdout)
    old_ran = old_fps is not None and "__harness_error__" not in old_fps
    new_ran = new_fps is not None and "__harness_error__" not in new_fps

    backend = getattr(new_sandbox, "backend", "unknown")

    if not old_ran or not new_ran:
        which = "original code on the legacy image" if not old_ran else "migrated code on target"
        return EquivalenceReport(
            backend=backend,
            old_ran=old_ran,
            new_ran=new_ran,
            comparisons=[],
            equivalent=None,
            note=f"Could not fingerprint {which}; equivalence undetermined.",
        )

    comparisons = compare_fingerprints(old_fps, new_fps, fidelity_threshold=threshold)
    comparable = [c for c in comparisons if c.status in ("equivalent", "not-equivalent")]

    if not comparable:
        return EquivalenceReport(
            backend=backend,
            old_ran=True,
            new_ran=True,
            comparisons=comparisons,
            equivalent=None,
            note="No circuit prepared a comparable pure state on both sides "
            "(parametric, measured/reset, too large, or renamed) - equivalence undetermined.",
        )

    equivalent = all(c.status == "equivalent" for c in comparable)
    n_eq = sum(c.status == "equivalent" for c in comparable)
    note = f"{n_eq}/{len(comparable)} comparable circuit(s) behaviorally equivalent."
    return EquivalenceReport(
        backend=backend,
        old_ran=True,
        new_ran=True,
        comparisons=comparisons,
        equivalent=equivalent,
        note=note,
    )


def default_equivalence_sandboxes() -> tuple[Sandbox, Sandbox]:
    """Construct (old, new) Docker sandboxes for a real equivalence check.

    Old code runs on the pinned legacy image; new code on the target image. Equivalence
    is inherently two-environment, so both sides are Docker regardless of the default
    ``SANDBOX_BACKEND`` (a single installed Qiskit cannot run both old and new code).
    """
    from qiskit_migration.migration.sandbox import DockerSandbox

    settings = get_settings()
    old = DockerSandbox(image=settings.legacy_sandbox_image)
    new = DockerSandbox(image=settings.sandbox_image)
    return old, new
