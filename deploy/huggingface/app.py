"""Hosted demo (Hugging Face Spaces / Streamlit Community Cloud).

Paste legacy Qiskit (0.x / 1.x) code and see every deprecated/removed symbol the assistant
detects, with its verified Qiskit 2.x replacement. Runs the *offline detection* pipeline
in-process — zero API keys, zero cost, no Docker. Full LLM migration + Docker-sandbox
validation + behavioral-equivalence run locally (see the repo README / docs/HANDOFF.md).
"""

import streamlit as st

from src.migration.deprecations import (
    DeprecationStore,
    load_harvested_records,
    load_seed_records,
)
from src.migration.transform import find_deprecations
from src.migration.validate_input import InputValidationError

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

st.set_page_config(page_title="Qiskit Migration Assistant", page_icon="⚛️", layout="wide")


@st.cache_resource
def get_store() -> DeprecationStore:
    # Build from the curated seed + sandbox-verified harvested tier (no docs corpus needed).
    store = DeprecationStore("demo.db")
    store.create()
    store.upsert_many(load_seed_records())
    store.upsert_many(load_harvested_records())
    return store


store = get_store()

st.title("⚛️ Qiskit Migration Assistant")
st.caption(
    "Paste legacy Qiskit (0.x / 1.x) code — the assistant detects what's deprecated or removed "
    "and shows the verified Qiskit 2.x replacement, grounded in a sandbox-verified table."
)

left, right = st.columns(2)
with left:
    choice = st.selectbox("Load an example", ["(your own code)", *EXAMPLES])
    default = EXAMPLES.get(choice, next(iter(EXAMPLES.values())))
    code = st.text_area("Legacy Qiskit code", value=default, height=340)
    analyze = st.button("Analyze", type="primary")

with right:
    if analyze and code.strip():
        try:
            _symbols, deps = find_deprecations(code, store)
        except InputValidationError as exc:
            st.error(f"Not valid Python: {exc}")
        else:
            if not deps:
                st.success("No known deprecations found against the current knowledge base.")
            else:
                st.subheader(f"{len(deps)} deprecation(s) detected")
                for d in deps:
                    repl = d.replacement or "— removed; no drop-in replacement"
                    with st.container(border=True):
                        st.markdown(f"**`{d.symbol}`** · _{d.status}_")
                        st.markdown(f"→ **`{repl}`**")
                        st.caption(
                            f"deprecated {d.since_version or '?'}, removed {d.removed_in or '?'} "
                            f"· source: {d.source}"
                        )
                        if d.note:
                            st.caption(d.note)
    else:
        st.info("Load an example or paste code, then click **Analyze**.")

st.divider()
st.caption(
    "Hosted demo = offline detection only (zero keys, zero cost). Full LLM migration, "
    "Docker-sandbox execution, and behavioral-equivalence checks run locally — see the repo."
)
