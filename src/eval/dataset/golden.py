"""Golden evaluation set for the migration assistant.

Each case pairs a real old-Qiskit snippet with the APIs that *should* be detected
as deprecated and a hand-verified modern reference. Used to gate CI on:
  * deprecation-detection recall (offline);
  * reference cleanliness — references must pass static validation (offline);
  * (live) retrieval recall and executable correctness.
"""

from __future__ import annotations

GOLDEN: list[dict] = [
    {
        "id": "execute-aer-basic",
        "source_version": "0.46",
        "old_code": (
            "from qiskit import QuantumCircuit, execute, Aer\n"
            "qc = QuantumCircuit(2, 2)\n"
            "qc.h(0)\n"
            "qc.cx(0, 1)\n"
            "qc.measure([0, 1], [0, 1])\n"
            "backend = Aer.get_backend('qasm_simulator')\n"
            "result = execute(qc, backend, shots=1024).result()\n"
            "print(result.get_counts())\n"
        ),
        "expected_apis_changed": ["qiskit.execute", "qiskit.Aer"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit, transpile\n"
            "from qiskit_aer import AerSimulator\n"
            "qc = QuantumCircuit(2, 2)\n"
            "qc.h(0)\n"
            "qc.cx(0, 1)\n"
            "qc.measure([0, 1], [0, 1])\n"
            "backend = AerSimulator()\n"
            "transpiled = transpile(qc, backend)\n"
            "result = backend.run(transpiled, shots=1024).result()\n"
            "print(result.get_counts())\n"
        ),
    },
    {
        "id": "bind-parameters",
        "source_version": "0.45",
        "old_code": (
            "from qiskit import QuantumCircuit\n"
            "from qiskit.circuit import Parameter\n"
            "theta = Parameter('t')\n"
            "qc = QuantumCircuit(1)\n"
            "qc.rx(theta, 0)\n"
            "bound = qc.bind_parameters({theta: 0.5})\n"
        ),
        "expected_apis_changed": ["QuantumCircuit.bind_parameters"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit\n"
            "from qiskit.circuit import Parameter\n"
            "theta = Parameter('t')\n"
            "qc = QuantumCircuit(1)\n"
            "qc.rx(theta, 0)\n"
            "bound = qc.assign_parameters({theta: 0.5})\n"
        ),
    },
    {
        "id": "opflow-operators",
        "source_version": "0.43",
        "old_code": ("from qiskit.opflow import X, Z\nop = (X ^ X) + (Z ^ Z)\n"),
        "expected_apis_changed": ["qiskit.opflow"],
        "reference_ported_code": (
            "from qiskit.quantum_info import SparsePauliOp\n"
            "op = SparsePauliOp(['XX', 'ZZ'], coeffs=[1.0, 1.0])\n"
        ),
    },
    {
        "id": "algorithms-moved",
        "source_version": "0.45",
        "old_code": (
            "from qiskit.algorithms.optimizers import COBYLA\noptimizer = COBYLA(maxiter=100)\n"
        ),
        "expected_apis_changed": ["qiskit.algorithms"],
        "reference_ported_code": (
            "from qiskit_algorithms.optimizers import COBYLA\noptimizer = COBYLA(maxiter=100)\n"
        ),
    },
    {
        "id": "basicaer-removed",
        "source_version": "0.46",
        "old_code": (
            "from qiskit import BasicAer\nbackend = BasicAer.get_backend('qasm_simulator')\n"
        ),
        "expected_apis_changed": ["qiskit.BasicAer"],
        "reference_ported_code": (
            "from qiskit.providers.basic_provider import BasicSimulator\n"
            "backend = BasicSimulator()\n"
        ),
    },
    {
        "id": "assemble-removed",
        "source_version": "0.46",
        "old_code": (
            "from qiskit import QuantumCircuit, transpile, assemble\n"
            "from qiskit_aer import AerSimulator\n"
            "qc = QuantumCircuit(1, 1)\n"
            "qc.h(0)\n"
            "qc.measure(0, 0)\n"
            "backend = AerSimulator()\n"
            "tqc = transpile(qc, backend)\n"
            "qobj = assemble(tqc, backend)\n"
            "result = backend.run(qobj).result()\n"
        ),
        "expected_apis_changed": ["qiskit.compiler.assemble"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit, transpile\n"
            "from qiskit_aer import AerSimulator\n"
            "qc = QuantumCircuit(1, 1)\n"
            "qc.h(0)\n"
            "qc.measure(0, 0)\n"
            "backend = AerSimulator()\n"
            "tqc = transpile(qc, backend)\n"
            "result = backend.run(tqc).result()\n"
        ),
    },
    {
        "id": "ibmq-removed",
        "source_version": "0.40",
        "old_code": (
            "from qiskit import IBMQ\n"
            "IBMQ.load_account()\n"
            "provider = IBMQ.get_provider(hub='ibm-q')\n"
        ),
        "expected_apis_changed": ["qiskit.IBMQ"],
        "reference_ported_code": (
            "from qiskit_ibm_runtime import QiskitRuntimeService\n"
            "service = QiskitRuntimeService()\n"
        ),
    },
    {
        "id": "qasm-method-removed",
        "source_version": "0.45",
        "old_code": (
            "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(0)\nprint(qc.qasm())\n"
        ),
        "expected_apis_changed": ["QuantumCircuit.qasm"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit\n"
            "from qiskit.qasm2 import dumps\n"
            "qc = QuantumCircuit(2)\n"
            "qc.h(0)\n"
            "print(dumps(qc))\n"
        ),
    },
    {
        "id": "extensions-unitarygate",
        "source_version": "0.45",
        "old_code": (
            "import numpy as np\n"
            "from qiskit import QuantumCircuit\n"
            "from qiskit.extensions import UnitaryGate\n"
            "qc = QuantumCircuit(1)\n"
            "qc.append(UnitaryGate(np.array([[0, 1], [1, 0]])), [0])\n"
        ),
        "expected_apis_changed": ["qiskit.extensions"],
        "reference_ported_code": (
            "import numpy as np\n"
            "from qiskit import QuantumCircuit\n"
            "from qiskit.circuit.library import UnitaryGate\n"
            "qc = QuantumCircuit(1)\n"
            "qc.append(UnitaryGate(np.array([[0, 1], [1, 0]])), [0])\n"
        ),
    },
    {
        "id": "tools-job-monitor",
        "source_version": "0.46",
        "old_code": (
            "from qiskit import QuantumCircuit, transpile\n"
            "from qiskit_aer import AerSimulator\n"
            "from qiskit.tools import job_monitor\n"
            "qc = QuantumCircuit(1, 1)\n"
            "qc.h(0)\n"
            "qc.measure(0, 0)\n"
            "backend = AerSimulator()\n"
            "job = backend.run(transpile(qc, backend))\n"
            "job_monitor(job)\n"
            "print(job.result().get_counts())\n"
        ),
        "expected_apis_changed": ["qiskit.tools.job_monitor"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit, transpile\n"
            "from qiskit_aer import AerSimulator\n"
            "qc = QuantumCircuit(1, 1)\n"
            "qc.h(0)\n"
            "qc.measure(0, 0)\n"
            "backend = AerSimulator()\n"
            "job = backend.run(transpile(qc, backend))\n"
            "print(job.result().get_counts())\n"
        ),
    },
    {
        "id": "fake-provider-removed",
        "source_version": "0.46",
        "old_code": (
            "from qiskit.providers.fake_provider import FakeProvider\n"
            "provider = FakeProvider()\n"
            "backend = provider.get_backend('fake_vigo')\n"
        ),
        "expected_apis_changed": ["qiskit.providers.fake_provider.FakeProvider"],
        "reference_ported_code": (
            "from qiskit.providers.fake_provider import GenericBackendV2\n"
            "backend = GenericBackendV2(num_qubits=5)\n"
        ),
    },
    {
        "id": "basicaer-module-removed",
        "source_version": "0.46",
        "old_code": (
            "from qiskit.providers.basicaer import QasmSimulatorPy\nbackend = QasmSimulatorPy()\n"
        ),
        "expected_apis_changed": ["qiskit.providers.basicaer"],
        "reference_ported_code": (
            "from qiskit.providers.basic_provider import BasicSimulator\n"
            "backend = BasicSimulator()\n"
        ),
    },
    {
        "id": "opflow-algorithms-vqe",
        "source_version": "0.43",
        "old_code": (
            "from qiskit.opflow import X, Z, I\n"
            "from qiskit.algorithms.optimizers import SPSA\n"
            "hamiltonian = (X ^ X) + (Z ^ Z) + (I ^ I)\n"
            "optimizer = SPSA(maxiter=50)\n"
        ),
        "expected_apis_changed": ["qiskit.opflow", "qiskit.algorithms"],
        "reference_ported_code": (
            "from qiskit.quantum_info import SparsePauliOp\n"
            "from qiskit_algorithms.optimizers import SPSA\n"
            "hamiltonian = SparsePauliOp(['XX', 'ZZ', 'II'], coeffs=[1.0, 1.0, 1.0])\n"
            "optimizer = SPSA(maxiter=50)\n"
        ),
    },
    {
        "id": "execute-sampler-primitive",
        "source_version": "0.45",
        "old_code": (
            "from qiskit import QuantumCircuit, Aer, execute\n"
            "qc = QuantumCircuit(2)\n"
            "qc.h(0)\n"
            "qc.cx(0, 1)\n"
            "qc.measure_all()\n"
            "backend = Aer.get_backend('qasm_simulator')\n"
            "counts = execute(qc, backend, shots=1000).result().get_counts()\n"
            "print(counts)\n"
        ),
        "expected_apis_changed": ["qiskit.execute", "qiskit.Aer"],
        "reference_ported_code": (
            "from qiskit import QuantumCircuit\n"
            "from qiskit.primitives import StatevectorSampler\n"
            "qc = QuantumCircuit(2)\n"
            "qc.h(0)\n"
            "qc.cx(0, 1)\n"
            "qc.measure_all()\n"
            "sampler = StatevectorSampler()\n"
            "result = sampler.run([qc], shots=1000).result()\n"
            "counts = result[0].data.meas.get_counts()\n"
            "print(counts)\n"
        ),
    },
]


def load_golden() -> list[dict]:
    return GOLDEN
