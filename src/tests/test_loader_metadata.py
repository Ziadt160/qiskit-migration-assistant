"""Unit tests for version-aware metadata extraction (pure logic, no network)."""

from __future__ import annotations

import pytest

from src.ingestion.loader import QiskitMarkdownLoader


@pytest.fixture
def loader(tmp_path):
    return QiskitMarkdownLoader(str(tmp_path), current_version="2.2")


def _meta(loader: QiskitMarkdownLoader, rel: str) -> dict:
    return loader._extract_metadata(loader.base_dir / rel)


def test_release_note_tagged_with_version(loader):
    m = _meta(loader, "api/qiskit/release-notes/0.45.mdx")
    assert m["doc_type"] == "release_note"
    assert m["version"] == "0.45"


def test_current_api_matches_target_version(loader):
    m = _meta(loader, "api/qiskit/2.2/circuit.mdx")
    assert m["doc_type"] == "current_api"
    assert m["version"] == "2.2"


def test_historical_api_is_versioned_not_current(loader):
    m = _meta(loader, "api/qiskit/0.46/circuit.mdx")
    assert m["doc_type"] == "api_versioned"
    assert m["version"] == "0.46"


def test_migration_guide_detected(loader):
    m = _meta(loader, "guides/pulse-migration.mdx")
    assert m["doc_type"] == "migration_guide"
    assert "version" not in m


def test_upgrade_from_open_is_migration_guide(loader):
    m = _meta(loader, "guides/upgrade-from-open.mdx")
    assert m["doc_type"] == "migration_guide"


def test_plain_guide(loader):
    m = _meta(loader, "guides/circuit-construction.mdx")
    assert m["doc_type"] == "guide"


def test_tutorial(loader):
    m = _meta(loader, "tutorials/grover.mdx")
    assert m["doc_type"] == "tutorial"


def test_general_fallback(loader):
    m = _meta(loader, "accessibility.mdx")
    assert m["doc_type"] == "general"
    assert "version" not in m


def test_source_is_relative_posix(loader):
    m = _meta(loader, "api/qiskit/2.2/circuit.mdx")
    assert m["source"] == "api/qiskit/2.2/circuit.mdx"
    assert m["file_extension"] == ".mdx"
