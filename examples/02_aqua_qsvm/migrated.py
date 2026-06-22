"""Migrated to Qiskit 2.x.

Cross-package move: qiskit.aqua QSVM -> qiskit-machine-learning's QSVC, driven by a
FidelityQuantumKernel; the aqua SecondOrderExpansion feature map -> qiskit.circuit.library's
ZZFeatureMap. Data is now plain X/y arrays.
"""

from qiskit.circuit.library import ZZFeatureMap
from qiskit_machine_learning.algorithms import QSVC
from qiskit_machine_learning.kernels import FidelityQuantumKernel

feature_map = ZZFeatureMap(feature_dimension=2, reps=2)
kernel = FidelityQuantumKernel(feature_map=feature_map)
qsvc = QSVC(quantum_kernel=kernel)
qsvc.fit(X_train, y_train)
print(qsvc.score(X_test, y_test))
