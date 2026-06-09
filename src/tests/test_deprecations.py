"""Unit tests for the deprecation parser and SQLite store (no network)."""

from __future__ import annotations

from src.migration.deprecations import (
    DeprecationStore,
    load_seed_records,
    parse_release_note,
)

_REMOVAL_NOTE = """
#### Removal Notes

*   The method `QuantumCircuit.bind_parameters` has been removed, following its
    deprecation in Qiskit 0.45. You can use `QuantumCircuit.assign_parameters` as a
    drop-in replacement with all its defaults.
"""

_FEATURE_NOTE = """
#### New Features

*   Added a shiny new `qiskit.cool.Thing` that does nothing relevant here.
"""


def test_parse_removed_record_with_replacement_and_versions():
    recs = parse_release_note(_REMOVAL_NOTE, "1.0", "release-notes/1.0.mdx")
    match = [r for r in recs if r.symbol == "QuantumCircuit.bind_parameters"]
    assert match, "expected the removed symbol to be parsed"
    rec = match[0]
    assert rec.status == "removed"
    assert rec.replacement == "QuantumCircuit.assign_parameters"
    assert rec.since_version == "0.45"
    assert rec.removed_in == "1.0"


def test_parser_ignores_non_relevant_sections():
    assert parse_release_note(_FEATURE_NOTE, "1.0", "x") == []


def test_seed_loads():
    seed = load_seed_records()
    symbols = {r.symbol for r in seed}
    assert "qiskit.execute" in symbols
    assert "QuantumCircuit.bind_parameters" in symbols


def test_store_lookup_finds_execute(tmp_path):
    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    store.upsert_many(load_seed_records())

    results = store.lookup({"execute", "qiskit.execute"})
    assert results
    assert any(r.replacement == "backend.run" for r in results)


def test_store_lookup_by_last_segment(tmp_path):
    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    store.upsert_many(load_seed_records())

    results = store.lookup({"bind_parameters"})
    assert any("assign_parameters" in (r.replacement or "") for r in results)


def test_current_apis_never_flagged_as_deprecated(tmp_path):
    """A parsed false-positive on a current API (e.g. transpile) must be suppressed."""
    from src.migration.deprecations import DeprecationRecord

    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    store.upsert_many(
        [
            DeprecationRecord(
                "qiskit.compiler.transpile", "removed", "1.0", "2.0", None, "n", "parsed"
            )
        ]
    )
    results = store.lookup({"transpile", "qiskit.transpile", "qiskit.compiler.transpile"})
    assert results == []  # transpile is allowlisted as current


def test_store_prefers_removed_seed_over_parsed_deprecated(tmp_path):
    from src.migration.deprecations import DeprecationRecord

    store = DeprecationStore(str(tmp_path / "dep.db"))
    store.create()
    store.upsert_many(
        [
            DeprecationRecord("qiskit.execute", "deprecated", "0.40", None, None, "n", "parsed"),
            *load_seed_records(),
        ]
    )
    results = store.lookup({"qiskit.execute"})
    top = results[0]
    assert top.symbol == "qiskit.execute"
    assert top.status == "removed"  # seed/removed beats parsed/deprecated
    assert top.source == "curated-seed"
