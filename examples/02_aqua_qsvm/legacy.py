"""Legacy Qiskit (0.x): a QSVM classifier from qiskit.aqua.

qiskit.aqua was dissolved into the standalone ecosystem packages. QSVM moved to
qiskit-machine-learning (as the kernel-based QSVC). `training_input` / `test_input` are the
aqua-style {label: array} dicts.
"""

from qiskit import Aer
from qiskit.aqua import QuantumInstance
from qiskit.aqua.algorithms import QSVM
from qiskit.aqua.components.feature_maps import SecondOrderExpansion

feature_map = SecondOrderExpansion(feature_dimension=2, depth=2)
qsvm = QSVM(feature_map, training_input, test_input)
result = qsvm.run(QuantumInstance(Aer.get_backend("qasm_simulator"), shots=1024))
print(result["testing_accuracy"])
