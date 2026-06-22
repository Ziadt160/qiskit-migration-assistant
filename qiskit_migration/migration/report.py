"""File discovery, unified diffs, and coverage reporting for batch migration.

Pure helpers (no network) so they're easy to unit-test and reuse from the CLI,
the API, and the UI.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from qiskit_migration.migration.deprecations import DeprecationRecord
from qiskit_migration.migration.models import CoverageSummary, ValidationReport
from qiskit_migration.migration.symbols import extract_symbols

_SKIP_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
}


def iter_python_files(path: str | Path, recursive: bool = True) -> list[Path]:
    """Return the .py files under `path` (a file or directory), skipping junk dirs."""
    p = Path(path)
    if p.is_file():
        return [p] if p.suffix == ".py" else []
    if not p.is_dir():
        return []
    pattern = "**/*.py" if recursive else "*.py"
    return sorted(
        f
        for f in p.glob(pattern)
        if f.is_file() and not any(part in _SKIP_DIRS for part in f.parts)
    )


def unified_diff(old: str, new: str, path: str = "snippet.py") -> str:
    """A git-style unified diff between `old` and `new` (empty string if identical)."""
    if old == new:
        return ""
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )


def compute_coverage(
    ported_code: str,
    deprecations: list[DeprecationRecord],
    validation: ValidationReport | None,
) -> CoverageSummary:
    """A detected deprecation is 'handled' if its symbol no longer appears in the output."""
    try:
        keys = extract_symbols(ported_code).lookup_keys
    except SyntaxError:
        keys = set()

    unresolved: list[str] = []
    for dep in deprecations:
        last = dep.symbol.rsplit(".", 1)[-1]
        if dep.symbol in keys or last in keys:
            unresolved.append(dep.symbol)

    total = len(deprecations)
    return CoverageSummary(
        handled=total - len(unresolved),
        total=total,
        unresolved=sorted(set(unresolved)),
        validation_passed=bool(validation and validation.passed),
    )
