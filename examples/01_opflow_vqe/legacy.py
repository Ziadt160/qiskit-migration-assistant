"""Legacy Qiskit (0.x): a VQE built on qiskit.opflow + Aer + TwoLocal.

opflow, qiskit.algorithms, top-level Aer/execute, and the TwoLocal class are all gone in 2.x.
Feed this file to the migration assistant to port it.
"""

from qiskit import Aer
from qiskit.algorithms import VQE
from qiskit.algorithms.optimizers import SPSA
from qiskit.circuit.library import TwoLocal
from qiskit.opflow import I, Z

H = (Z ^ Z) + 0.5 * (I ^ Z) + 0.3 * (Z ^ I)
ansatz = TwoLocal(2, "ry", "cz", reps=3)
vqe = VQE(ansatz, optimizer=SPSA(), quantum_instance=Aer.get_backend("aer_simulator"))
result = vqe.compute_minimum_eigenvalue(operator=H)
print(result.eigenvalue)
