"""Hosted demo (Hugging Face Spaces — Gradio SDK).

Paste legacy Qiskit (0.x / 1.x) code and see every deprecated/removed symbol the assistant
detects, with its verified Qiskit 2.x replacement. Runs the *offline detection* pipeline
in-process — zero API keys, zero cost, no Docker. Full LLM migration + Docker-sandbox
validation + behavioral-equivalence run locally (see the repo README / docs/HANDOFF.md).
"""

import gradio as gr

from qiskit_migration.migration.deprecations import (
    DeprecationStore,
    load_harvested_records,
    load_seed_records,
)
from qiskit_migration.migration.transform import find_deprecations
from qiskit_migration.migration.validate_input import InputValidationError

EXAMPLES = {
    "opflow VQE (0.x → 2.x)": (
        "from qiskit.opflow import I, Z, PauliSumOp\n"
        "from qiskit import Aer, execute\n"
        "from qiskit.circuit.library import TwoLocal\n"
        "from qiskit.algorithms import VQE\n\n"
        'ansatz = TwoLocal(2, "ry", "cz", reps=3)\n'
        'vqe = VQE(ansatz, quantum_instance=Aer.get_backend("aer_simulator"))\n'
    ),
    "qiskit.aqua QSVM (0.x → 2.x)": (
        "from qiskit.aqua.algorithms import QSVM\n"
        "from qiskit.aqua.components.feature_maps import SecondOrderExpansion\n"
        "from qiskit.aqua import QuantumInstance\n\n"
        "qsvm = QSVM(feature_map, training_input, test_input)\n"
    ),
    "V1 → V2 primitives (1.x → 2.x)": (
        "from qiskit.primitives import Sampler, Estimator\n\n"
        "sampler = Sampler()\n"
        "quasi = sampler.run(circuit, parameter_values=params).result().quasi_dists[0]\n"
    ),
    "ansatz classes → functions (1.x → 2.1)": (
        "from qiskit.circuit.library import EfficientSU2, RealAmplitudes, QFT\n\n"
        "ansatz = EfficientSU2(4, reps=2)\n"
        "qft = QFT(4, do_swaps=True)\n"
    ),
}


def _build_store() -> DeprecationStore:
    # Curated seed + sandbox-verified harvested tier (no docs corpus / keys needed).
    store = DeprecationStore("demo.db")
    store.create()
    store.upsert_many(load_seed_records())
    store.upsert_many(load_harvested_records())
    return store


STORE = _build_store()


def analyze(code: str) -> str:
    if not code or not code.strip():
        return "Paste some code or pick an example, then click **Analyze**."
    try:
        _symbols, deps = find_deprecations(code, STORE)
    except InputValidationError as exc:
        return f"⚠️ Not valid Python: {exc}"
    if not deps:
        return "✅ No known deprecations found against the current knowledge base."
    out = [f"### {len(deps)} deprecation(s) detected"]
    for d in deps:
        repl = d.replacement or "— removed; no drop-in replacement"
        meta = (
            f"deprecated {d.since_version or '?'}, removed {d.removed_in or '?'} "
            f"· source: {d.source}"
        )
        block = f"**`{d.symbol}`** · _{d.status}_\n\n→ **`{repl}`**\n\n_{meta}_"
        if d.note:
            block += f"\n\n{d.note}"
        out.append(block)
    return "\n\n---\n\n".join(out)


with gr.Blocks(title="Qiskit Migration Assistant") as demo:
    gr.Markdown(
        "# ⚛️ Qiskit Migration Assistant\n"
        "Paste legacy Qiskit (0.x / 1.x) code — see what's deprecated or removed and the verified "
        "Qiskit 2.x replacement, grounded in a sandbox-verified table."
    )
    with gr.Row():
        with gr.Column():
            code = gr.Code(
                value=next(iter(EXAMPLES.values())),
                language="python",
                label="Legacy Qiskit code",
                lines=16,
            )
            gr.Examples(examples=[[v] for v in EXAMPLES.values()], inputs=code, label="Examples")
            btn = gr.Button("Analyze", variant="primary")
        with gr.Column():
            output = gr.Markdown()
    btn.click(analyze, inputs=code, outputs=output)
    gr.Markdown(
        "_Hosted demo = offline detection only (zero keys, zero cost). Full LLM migration, "
        "Docker-sandbox execution, and behavioral-equivalence checks run locally — see the repo._"
    )

if __name__ == "__main__":
    demo.launch()
