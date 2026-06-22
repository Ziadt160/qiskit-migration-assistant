"""Legacy Qiskit (1.x): a V1 fake backend + execute().

The legacy fake backends and top-level execute() are removed in 2.x. `circuit` is defined elsewhere.
"""

from qiskit import execute, transpile
from qiskit.providers.fake_provider import FakeManila

backend = FakeManila()
tqc = transpile(circuit, backend, optimization_level=3)
counts = execute(tqc, backend, shots=2048).result().get_counts()
