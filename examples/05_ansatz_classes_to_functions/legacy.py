"""Legacy Qiskit (1.x): circuit-library ansatz CLASSES, deprecated in 2.1.

EfficientSU2 / RealAmplitudes / TwoLocal / QFT classes are replaced by builder functions and
QFTGate in 2.1+.
"""

from qiskit.circuit.library import QFT, EfficientSU2, RealAmplitudes

ansatz = EfficientSU2(4, entanglement="linear", reps=2)
amps = RealAmplitudes(4, reps=3)
qft = QFT(4, do_swaps=True)
