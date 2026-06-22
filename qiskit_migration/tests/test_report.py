"""Unit tests for file discovery, diffs, and coverage (pure logic, no network)."""

from __future__ import annotations

from qiskit_migration.migration.deprecations import DeprecationRecord
from qiskit_migration.migration.models import ValidationReport
from qiskit_migration.migration.report import compute_coverage, iter_python_files, unified_diff

_DEP = DeprecationRecord("qiskit.execute", "removed", "0.46", "1.0", "backend.run", "n", "seed")


def test_iter_python_files_recursive_skips_junk(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "c.py").write_text("z = 3")
    (tmp_path / "notes.txt").write_text("hi")

    names = {f.name for f in iter_python_files(tmp_path, recursive=True)}
    assert names == {"a.py", "b.py"}  # junk dir + non-.py excluded


def test_iter_python_files_non_recursive(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2")
    assert {f.name for f in iter_python_files(tmp_path, recursive=False)} == {"a.py"}


def test_iter_python_files_single_file(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    assert iter_python_files(f) == [f]
    assert iter_python_files(tmp_path / "missing.txt") == []


def test_unified_diff():
    diff = unified_diff("a\nb", "a\nc", "x.py")
    assert "-b" in diff and "+c" in diff
    assert unified_diff("same", "same") == ""


def test_compute_coverage_handled():
    cov = compute_coverage(
        "from qiskit import transpile\ntranspile(qc, b)", [_DEP], ValidationReport(syntax_ok=True)
    )
    assert cov.handled == 1
    assert cov.total == 1
    assert cov.unresolved == []
    assert cov.validation_passed


def test_compute_coverage_unresolved():
    cov = compute_coverage(
        "from qiskit import execute\nexecute(qc, b)",
        [_DEP],
        ValidationReport(syntax_ok=True, deprecated_symbols=["qiskit.execute"]),
    )
    assert cov.handled == 0
    assert cov.unresolved == ["qiskit.execute"]
    assert not cov.validation_passed
