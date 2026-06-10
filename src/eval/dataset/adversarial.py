"""Held-out *adversarial* evaluation set — the honest coverage-gap probe.

Why this exists (see HANDOFF §12.1): the golden set is built from the same
curated seed records it tests, so its 1.00 detection recall is **circular** — it
proves "lookup works on APIs the seed already knows," not real-world coverage.

This set is the opposite by construction: every case uses a genuine Qiskit
0.x→2.x breaking change that is **deliberately absent from the curated seed**
(`data/known_deprecations.json`) — neither as a full symbol nor as a matching
last segment. Running detection over it therefore measures the *gap* between what
we curated and the real migration surface. It is a **diagnostic, not a CI gate**:
misses are the expected, useful signal, and the missed symbols are the worklist
for growing the seed.

**The loop works (and has cycled once):** the original 13 held-out cases
(QuantumInstance, aqua, ignis, providers.aer, test.mock, tools.visualization /
parallel_map, cnot/toffoli/mct/fredkin/snapshot, PauliTable) were measured at
0/13, curated into the seed, and **graduated into the golden set** — see
`golden.py`. The cases below are the *next* frontier: real breaking changes the
now-expanded seed still does not know. As each gets curated, graduate it too (the
held-out invariant test will flag any case the seed has caught up to).

Every API below was verified against the local Qiskit documentation checkout
(`documentation/docs/...`); the `source` field records the citing doc. References
are modern, statically-clean equivalents.

Schema matches `golden.py` (so `evaluate_detection` / `evaluate_reference_cleanliness`
work unchanged) plus a `category` tag used for the per-bucket coverage report.
"""

from __future__ import annotations

ADVERSARIAL: list[dict] = [
    # --- removed QuantumCircuit gate methods (append the gate instead) ------ #
    {
        "id": "gate-diagonal",
        "category": "removed-gate-method",
        "source_version": "0.45",
        "old_code": (
            "from qiskit import QuantumCircuit\n"
            "qc = QuantumCircuit(2)\n"
            "qc.diagonal([1, -1, -1, 1], [0, 1])\n"
        ),
        "expected_apis_changed": ["QuantumCircuit.diagonal"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit\n"
            "from qiskit.circuit.library import DiagonalGate\n"
            "qc = QuantumCircuit(2)\n"
            "qc.append(DiagonalGate([1, -1, -1, 1]), [0, 1])\n"
        ),
        "source": "documentation/docs/guides/qiskit-1.0-features.mdx (QuantumCircuit gates)",
    },
    {
        "id": "gate-squ",
        "category": "removed-gate-method",
        "source_version": "0.45",
        "old_code": (
            "from qiskit import QuantumCircuit\n"
            "qc = QuantumCircuit(1)\n"
            "qc.squ([[0, 1], [1, 0]], 0)\n"
        ),
        "expected_apis_changed": ["QuantumCircuit.squ"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit\n"
            "qc = QuantumCircuit(1)\n"
            "qc.unitary([[0, 1], [1, 0]], [0])\n"
        ),
        "source": "documentation/docs/guides/qiskit-1.0-features.mdx (QuantumCircuit gates)",
    },
    # --- removed / relocated functions ------------------------------------- #
    {
        "id": "converters-ast-to-dag",
        "category": "removed-function",
        "source_version": "0.45",
        "old_code": ("from qiskit.converters import ast_to_dag\ndag = ast_to_dag(ast)\n"),
        "expected_apis_changed": ["qiskit.converters.ast_to_dag"],
        "reference_ported_code": (
            "from qiskit.converters import circuit_to_dag\ndag = circuit_to_dag(circuit)\n"
        ),
        "source": "documentation/docs/guides/qiskit-1.0-features.mdx (qiskit.converters)",
    },
    {
        "id": "transpiler-synthesis-graysynth",
        "category": "moved-import",
        "source_version": "0.45",
        "old_code": (
            "from qiskit.transpiler.synthesis import graysynth\n"
            "circuit = graysynth(cnots, angles)\n"
        ),
        "expected_apis_changed": ["qiskit.transpiler.synthesis.graysynth"],
        "reference_ported_code": (
            "from qiskit.synthesis import synth_cnot_phase_aam\n"
            "circuit = synth_cnot_phase_aam(cnots, angles)\n"
        ),
        "source": "documentation/docs/guides/qiskit-1.0-features.mdx (qiskit.transpiler synthesis)",
    },
    {
        "id": "transpiler-synthesis-cnot-synth",
        "category": "moved-import",
        "source_version": "0.45",
        "old_code": (
            "from qiskit.transpiler.synthesis import cnot_synth\ncircuit = cnot_synth(state)\n"
        ),
        "expected_apis_changed": ["qiskit.transpiler.synthesis.cnot_synth"],
        "reference_ported_code": (
            "from qiskit.synthesis import synth_cnot_count_full_pmh\n"
            "circuit = synth_cnot_count_full_pmh(state)\n"
        ),
        "source": "documentation/docs/guides/qiskit-1.0-features.mdx (qiskit.transpiler synthesis)",
    },
]


def load_adversarial() -> list[dict]:
    return ADVERSARIAL
