"""Migrated to Qiskit 2.1+: ansatz builder FUNCTIONS + QFTGate.

The classes become lowercase functions returning a QuantumCircuit; QFT becomes the QFTGate
instruction (synthesis happens at transpile time).
"""

from qiskit.circuit.library import QFTGate, efficient_su2, real_amplitudes

ansatz = efficient_su2(4, entanglement="linear", reps=2)
amps = real_amplitudes(4, reps=3)
qft = QFTGate(4)
