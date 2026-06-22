"""Static validation of the LLM's ported code.

Cheap, offline checks that catch the most important failure modes before (and
independently of) the heavier sandbox execution stage (M5):
  * the ported code must parse;
  * it must not still reference APIs that are removed/moved as of the target
    version — i.e. the migration must not "leak" a deprecated symbol.
"""

from __future__ import annotations

import ast

from qiskit_migration.migration.deprecations import DeprecationStore
from qiskit_migration.migration.models import ValidationReport
from qiskit_migration.migration.symbols import extract_symbols


def _version_tuple(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _invalid_as_of_target(removed_in: str | None, target_version: str) -> bool:
    """True if a symbol removed in `removed_in` is invalid on `target_version`."""
    removed = _version_tuple(removed_in)
    target = _version_tuple(target_version)
    if removed is None:  # unknown removal version -> treat as invalid (conservative)
        return True
    if target is None:
        return True
    return removed <= target


def validate_output(code: str, store: DeprecationStore, target_version: str) -> ValidationReport:
    errors: list[str] = []

    try:
        ast.parse(code)
        syntax_ok = True
    except SyntaxError as e:
        return ValidationReport(syntax_ok=False, errors=[f"Ported code has a syntax error: {e}"])

    leaked: set[str] = set()
    symbols = extract_symbols(code)
    for rec in store.lookup(symbols.lookup_keys):
        if rec.status in ("removed", "moved") and _invalid_as_of_target(
            rec.removed_in, target_version
        ):
            leaked.add(rec.symbol)

    return ValidationReport(syntax_ok=syntax_ok, deprecated_symbols=sorted(leaked), errors=errors)
