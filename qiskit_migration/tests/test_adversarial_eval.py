"""Integrity tests for the held-out adversarial eval set.

The adversarial set's value depends entirely on it being *held out*: every case
must exercise an API the curated seed does NOT know, otherwise it would be just as
circular as the golden set it is meant to counterbalance (HANDOFF §12.1). These
tests pin that invariant — including a deliberate tripwire that fails the moment
the seed grows to cover one of these APIs, signalling that the case should
graduate into the golden set.
"""

from __future__ import annotations

import ast

import pytest

from qiskit_migration.eval.dataset.adversarial import load_adversarial
from qiskit_migration.eval.metrics import evaluate_detection, evaluate_reference_cleanliness
from qiskit_migration.migration.deprecations import DeprecationStore, load_seed_records

_REQUIRED_KEYS = {
    "id",
    "category",
    "source_version",
    "old_code",
    "expected_apis_changed",
    "reference_ported_code",
}


@pytest.fixture
def seed_store(tmp_path):
    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    store.upsert_many(load_seed_records())
    return store


def test_adversarial_cases_are_well_formed():
    cases = load_adversarial()
    assert cases, "adversarial set is empty"

    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"

    for case in cases:
        missing = _REQUIRED_KEYS - case.keys()
        assert not missing, f"{case.get('id')}: missing keys {missing}"
        assert case["expected_apis_changed"], f"{case['id']}: no expected APIs"
        # Both snippets must be valid Python so detection/cleanliness can run.
        ast.parse(case["old_code"])
        ast.parse(case["reference_ported_code"])


def test_adversarial_set_is_held_out_from_seed(seed_store):
    """The honest-coverage tripwire: with the curated seed alone, NONE of these
    APIs should be detected. A non-zero count means the seed now covers a case —
    move it into the golden set and shrink the documented coverage gap.
    """
    result = evaluate_detection(load_adversarial(), seed_store)
    detected = [c for c in result.per_case if c["found"]]
    assert result.total_found == 0, (
        f"seed now detects {len(detected)} held-out case(s): "
        f"{[c['id'] for c in detected]} — graduate them into golden.py"
    )


def test_expected_apis_do_not_overlap_seed(seed_store):
    """Structural backstop, independent of the matching logic: no expected API may
    equal a seed symbol or a seed last-segment."""
    seed = load_seed_records()
    seed_symbols = {r.symbol for r in seed}
    seed_segments = {r.last_segment for r in seed}
    for case in load_adversarial():
        for api in case["expected_apis_changed"]:
            last = api.rsplit(".", 1)[-1]
            assert api not in seed_symbols, f"{case['id']}: {api} is already a seed symbol"
            assert last not in seed_segments, f"{case['id']}: segment {last} collides with seed"


def test_adversarial_references_are_clean(seed_store):
    """References are modern equivalents and must pass static validation, so the
    set stays usable for cleanliness/executable runs once the seed grows."""
    result = evaluate_reference_cleanliness(load_adversarial(), seed_store, "2.2")
    assert result.pass_rate == 1.0, result.failures
