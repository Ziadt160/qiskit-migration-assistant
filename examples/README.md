# Examples

Five real migration cases the assistant handles, each as a `legacy.py` (the old input) and a
`migrated.py` (verified Qiskit 2.x output). These are **focused, illustrative snippets** of the key
API changes — they highlight the imports and call-sites that move, not standalone end-to-end programs
(some reference undefined names like `circuit` / `X_train`). The migrated side uses real, current
Qiskit 2.x APIs.

| Case | Era | What changes |
|------|-----|--------------|
| [`01_opflow_vqe`](01_opflow_vqe) | 0.x → 2.x | `qiskit.opflow` + `Aer`/`execute` + `TwoLocal` → `SparsePauliOp` + primitives + `efficient_su2` |
| [`02_aqua_qsvm`](02_aqua_qsvm) | 0.x → 2.x | `qiskit.aqua` QSVM → `qiskit-machine-learning` `QSVC` + `FidelityQuantumKernel` |
| [`03_primitives_v1_to_v2`](03_primitives_v1_to_v2) | 1.x → 2.x | V1 `Sampler`/`Estimator` (removed in 2.0) → `StatevectorSampler`/`StatevectorEstimator` + PUB API |
| [`04_fake_provider_backendv2`](04_fake_provider_backendv2) | 1.x → 2.x | V1 fakes + `execute()` → `qiskit_ibm_runtime.fake_provider` + `backend.run()` |
| [`05_ansatz_classes_to_functions`](05_ansatz_classes_to_functions) | 1.x → 2.1 | `EfficientSU2`/`RealAmplitudes`/`QFT` classes → `efficient_su2`/`real_amplitudes`/`QFTGate` |

## Run the assistant on one

```bash
python -m qiskit_migration.migration.cli --code "$(cat examples/01_opflow_vqe/legacy.py)" --offline
# or the full pipeline (needs retrieval keys + Docker sandbox) — see docs/HANDOFF.md
```
