"""Migrated to Qiskit 2.x: V2 reference primitives + the PUB result API.

Runs take a list of PUBs (primitive unified blocs); results are indexed per-PUB and read from
.data (counts for the sampler, .evs for the estimator).
"""

from qiskit.primitives import StatevectorEstimator, StatevectorSampler

sampler = StatevectorSampler()
result = sampler.run([(circuit, params)]).result()
counts = result[0].data.meas.get_counts()

estimator = StatevectorEstimator()
value = estimator.run([(circuit, observable)]).result()[0].data.evs
