"""Migrated to Qiskit 2.x.

opflow operators -> qiskit.quantum_info.SparsePauliOp; the Aer backend -> a V2 primitive
(StatevectorEstimator); TwoLocal -> the efficient_su2 function; qiskit.algorithms -> qiskit_algorithms.
"""

from qiskit.circuit.library import efficient_su2
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp
from qiskit_algorithms import VQE
from qiskit_algorithms.optimizers import SPSA

H = SparsePauliOp(["ZZ", "IZ", "ZI"], [1.0, 0.5, 0.3])
ansatz = efficient_su2(2, reps=3)
vqe = VQE(StatevectorEstimator(), ansatz, SPSA())
result = vqe.compute_minimum_eigenvalue(operator=H)
print(result.eigenvalue)
