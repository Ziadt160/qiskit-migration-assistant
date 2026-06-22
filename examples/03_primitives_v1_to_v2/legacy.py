"""Legacy Qiskit (1.x): V1 primitives (Sampler / Estimator), removed in 2.0.

The V1 .run(circuit, parameter_values=...) signature and the .quasi_dists / .values result
fields are gone. `circuit`, `params`, and `observable` are defined elsewhere.
"""

from qiskit.primitives import Estimator, Sampler

sampler = Sampler()
job = sampler.run(circuit, parameter_values=params)
quasi = job.result().quasi_dists[0]

estimator = Estimator()
value = estimator.run(circuit, observable).result().values[0]
