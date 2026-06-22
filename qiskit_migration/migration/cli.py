"""Command-line entry point for the Qiskit migration assistant.

Examples (run from the repo root):

    # Build the deprecation knowledge base from the docs corpus (offline):
    python -m qiskit_migration.migration.cli --build-store

    # Offline: just report what's deprecated in a snippet (no network):
    python -m qiskit_migration.migration.cli --offline --file old_code.py

    # Full migration of one snippet (needs PINECONE + the configured LLM):
    python -m qiskit_migration.migration.cli --file old_code.py

    # Migrate a file or a whole directory, showing a diff per changed file:
    python -m qiskit_migration.migration.cli --path ./my_project --recursive
    python -m qiskit_migration.migration.cli --path ./my_project --recursive --apply  # write
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from qiskit_migration.config import Settings, get_settings
from qiskit_migration.migration.deprecations import DeprecationStore, build_deprecation_store
from qiskit_migration.migration.report import iter_python_files, unified_diff
from qiskit_migration.migration.transform import find_deprecations
from qiskit_migration.migration.validate_input import InputValidationError

_DEFAULT_DB = "app.db"
_DEFAULT_DOCS = "documentation/docs"


def _read_code(args: argparse.Namespace) -> str:
    if args.code:
        return args.code
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    return sys.stdin.read()


def _store_count(store: DeprecationStore) -> int:
    try:
        return store.count()
    except sqlite3.OperationalError:
        return 0


def _ensure_store(db_path: str, docs_dir: str) -> DeprecationStore:
    store = DeprecationStore(db_path)
    if _store_count(store) == 0:
        if not Path(docs_dir).is_dir():
            sys.exit(
                f"Deprecation store is empty and docs dir '{docs_dir}' not found. "
                f"Run with --build-store --docs-dir <path>."
            )
        print(f"Building deprecation store from {docs_dir} ...", file=sys.stderr)
        build_deprecation_store(docs_dir, db_path)
    return store


def _missing_keys(settings: Settings) -> list[str]:
    """Keys required for a live migration, given the configured LLM provider."""
    missing = []
    if not settings.pinecone_api_key:
        missing.append("PINECONE_API_KEY")
    if settings.llm_provider == "gemini" and not settings.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if settings.llm_provider in ("openai", "openai_compatible") and not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    return missing


def _run_offline(code: str, store: DeprecationStore) -> int:
    symbols, deps = find_deprecations(code, store)
    print(f"Qiskit symbols detected: {', '.join(sorted(symbols.lookup_keys)) or '(none)'}\n")
    if not deps:
        print("No known deprecations found for this code against the current knowledge base.")
        return 0
    print(f"{len(deps)} deprecation(s) found:\n" + "-" * 70)
    for d in deps:
        print(f"  {d.symbol}  [{d.status}]")
        print(f"      -> replacement: {d.replacement or '(none / removed feature)'}")
        print(f"      deprecated {d.since_version}, removed {d.removed_in}  (source: {d.source})")
        if d.note:
            print(f"      note: {d.note[:160]}")
    return 0


def _run_runtime_deps(code: str) -> int:
    """Run the code on the legacy Qiskit image and report the deprecations it actually triggers."""
    from qiskit_migration.migration.runtime_deprecations import (
        capture_runtime_deprecations,
        legacy_sandbox,
    )

    report = capture_runtime_deprecations(code, legacy_sandbox())
    if not report.ran:
        print(f"Could not capture runtime deprecations. {report.note}")
        return 2
    print(f"{report.note}\n" + "-" * 70)
    for d in report.deprecations:
        print(f"  {d.symbol or '(unparsed)'}  [{d.category}, removed {d.removed_in or '?'}]")
        print(f"      -> {d.replacement or '(no inline hint; full message below)'}")
        if not d.replacement:
            print(f"         {d.message[:160]}")
    return 0


def _print_coverage(prefix: str, result) -> None:
    cov = result.coverage
    if not cov:
        return
    status = "PASS" if cov.validation_passed else "FAIL"
    extra = f"; unresolved: {', '.join(cov.unresolved)}" if cov.unresolved else ""
    print(
        f"{prefix}coverage {cov.handled}/{cov.total} deprecated APIs handled, "
        f"validation {status}{extra}"
    )


def _run_full(code: str, db_path: str, source_version: str | None, as_json: bool) -> int:
    from qiskit_migration.migration.transform import MigrationTransformer

    transformer = MigrationTransformer.from_settings(db_path)
    result = transformer.transform(code, source_version=source_version)

    if as_json:
        print(result.model_dump_json(indent=2))
        return 0

    print("=" * 70 + "\nPORTED CODE\n" + "=" * 70)
    print(result.ported_code)
    if result.changes:
        print("\n" + "=" * 70 + "\nCHANGES\n" + "=" * 70)
        for ch in result.changes:
            print(f"  - {ch.old} -> {ch.new}\n      {ch.reason} (cite: {ch.citation})")
    if result.warnings:
        print("\nWARNINGS:")
        for w in result.warnings:
            print(f"  ! {w}")
    print()
    _print_coverage("", result)
    return 0 if (result.validation and result.validation.passed) else 2


def _run_path(
    path: str, recursive: bool, apply: bool, db_path: str, source_version: str | None
) -> int:
    """Migrate every .py file under `path` that uses a deprecated API, showing diffs."""
    from qiskit_migration.migration.transform import MigrationTransformer

    files = iter_python_files(path, recursive)
    if not files:
        print(f"No .py files found at '{path}'.")
        return 0

    transformer = MigrationTransformer.from_settings(db_path)
    changed = skipped = errored = 0

    for fp in files:
        rel = str(fp)
        try:
            code = fp.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            errored += 1
            print(f"ERROR reading {rel}: {e}", file=sys.stderr)
            continue

        # Cheap offline gate: only spend an LLM call on files that touch deprecated APIs.
        try:
            _, deps = find_deprecations(code, transformer.store)
        except InputValidationError:
            skipped += 1
            continue
        if not deps:
            skipped += 1
            continue

        try:
            result = transformer.transform(code, source_version=source_version)
        except Exception as e:  # noqa: BLE001
            errored += 1
            print(f"ERROR migrating {rel}: {e}", file=sys.stderr)
            continue

        diff = unified_diff(code, result.ported_code, rel)
        if not diff:
            print(f"= {rel}: deprecations detected but no change produced")
            continue
        changed += 1
        print(diff)
        _print_coverage(f"# [{rel}] ", result)
        if apply:
            fp.write_text(result.ported_code, encoding="utf-8")
            print(f"# applied -> {rel}")
        print()

    print(
        f"\nScanned {len(files)} file(s): {changed} changed, "
        f"{skipped} skipped (no deprecations), {errored} errored."
    )
    if not apply and changed:
        print("(dry-run -- re-run with --apply to write the changes to disk)")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Migrated code (and Qiskit itself) routinely contains non-ASCII — e.g. a Parameter
    # named "θ". On a Windows cp1252 console, print() would raise UnicodeEncodeError; force
    # UTF-8 so the result is always printable. No-op where stdout is already UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Qiskit version migration assistant.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", help="Path to a single Python file to migrate.")
    group.add_argument("--code", help="Inline code string to migrate.")
    group.add_argument("--path", help="File or directory to migrate in place (with diffs).")
    parser.add_argument("--recursive", action="store_true", help="Recurse into subdirs for --path.")
    parser.add_argument("--apply", action="store_true", help="Write changes to disk (for --path).")
    parser.add_argument(
        "--offline", action="store_true", help="Only report deprecations (no network)."
    )
    parser.add_argument(
        "--build-store", action="store_true", help="Build the deprecation store and exit."
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="SQLite path for the deprecation store.")
    parser.add_argument("--docs-dir", default=_DEFAULT_DOCS, help="Docs corpus directory.")
    parser.add_argument("--source-version", help="Hint: the Qiskit version the code targets.")
    parser.add_argument("--json", action="store_true", help="Emit JSON (single-snippet mode).")
    parser.add_argument(
        "--runtime-deps",
        action="store_true",
        help="Run the code on the legacy Qiskit image and report the DeprecationWarnings it "
        "actually triggers, with Qiskit's own replacement hints (needs Docker + legacy image).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.build_store:
        total = build_deprecation_store(args.docs_dir, args.db)
        print(f"Deprecation store built: {total} records in '{args.db}'.")
        return 0

    # --- directory / file batch migration ---
    if args.path:
        _ensure_store(args.db, args.docs_dir)
        missing = _missing_keys(settings)
        if missing:
            sys.exit(f"Live migration needs: {', '.join(missing)} (see .env).")
        return _run_path(args.path, args.recursive, args.apply, args.db, args.source_version)

    # --- single snippet (inline / stdin) ---
    code = _read_code(args)

    if args.runtime_deps:  # needs Docker + the legacy image, but no store / network keys
        return _run_runtime_deps(code)

    store = _ensure_store(args.db, args.docs_dir)

    if args.offline:
        return _run_offline(code, store)

    missing = _missing_keys(settings)
    if missing:
        sys.exit(
            f"Full migration needs live services; missing keys: {', '.join(missing)}. "
            f"Use --offline for deprecation analysis without network."
        )
    return _run_full(code, args.db, args.source_version, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
