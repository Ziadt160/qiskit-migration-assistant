"""Migrated to Qiskit 2.x: BackendV2 fakes from qiskit-ibm-runtime + backend.run().

The V2 fake backends live in qiskit_ibm_runtime.fake_provider; execute() is replaced by
running the transpiled circuit on the backend directly.
"""

from qiskit import transpile
from qiskit_ibm_runtime.fake_provider import FakeManilaV2

backend = FakeManilaV2()
tqc = transpile(circuit, backend, optimization_level=3)
counts = backend.run(tqc, shots=2048).result().get_counts()
