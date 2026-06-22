"""End-to-end migration orchestration.

`find_deprecations` is the offline core (input guard -> symbols -> deprecation
lookup) usable with no network. `MigrationTransformer` adds the live stages
(retrieval + Gemini transform + static validation). Retriever/generator are
injected so the pipeline can be unit-tested with fakes.
"""

from __future__ import annotations

import logging

from qiskit_migration.config import get_settings
from qiskit_migration.migration.deprecations import DeprecationRecord, DeprecationStore
from qiskit_migration.migration.models import (
    DeprecationHit,
    MigrationResult,
    SandboxReport,
    ValidationReport,
)
from qiskit_migration.migration.report import compute_coverage
from qiskit_migration.migration.symbols import ExtractedSymbols, extract_symbols
from qiskit_migration.migration.validate_input import validate_input
from qiskit_migration.migration.validate_output import validate_output

logger = logging.getLogger(__name__)


def _build_feedback(
    execution: SandboxReport | None,
    report: ValidationReport,
    runtime_records: list[DeprecationRecord] | None = None,
) -> str:
    """Compose actionable feedback for a repair attempt from the failures observed."""
    parts: list[str] = []
    if execution is not None and not execution.ok:
        if execution.timed_out:
            parts.append("The ported code timed out when executed.")
        else:
            parts.append(
                f"The ported code failed to run (error: {execution.error_type or 'unknown'}).\n"
                f"stderr:\n{execution.stderr.strip()[:1500]}"
            )
    # Root-2: the target library itself flagged these at runtime — authoritative + current.
    if runtime_records:
        lines = [
            f"- `{r.symbol}` is deprecated/removed on the target"
            + (f" -> use `{r.replacement}`" if r.replacement else " (see message)")
            for r in runtime_records
        ]
        parts.append(
            "The target Qiskit raised DeprecationWarnings at runtime. These MUST be migrated "
            "(apply the suggested replacement):\n" + "\n".join(lines)
        )
    if report.deprecated_symbols:
        parts.append(
            "These symbols are still deprecated/removed on the target version and must "
            "be replaced: " + ", ".join(report.deprecated_symbols)
        )
    if not report.syntax_ok:
        parts.append("The ported code does not parse as valid Python.")
    return "\n\n".join(parts) or "(none)"


def _merge_runtime_deprecations(
    existing: list[DeprecationRecord],
    execution: SandboxReport | None,
    base_deps: list[DeprecationRecord],
) -> list[DeprecationRecord]:
    """Learn deprecations from a failed sandbox run and add the new ones to the running set.

    The target library's own runtime warnings (e.g. a 2.1-era ``TwoLocal`` deprecation the
    static table never harvested) are parsed into records and merged, deduped by symbol
    against what we already know — closing the loop so detection stays current with the
    real target version, not a frozen snapshot.
    """
    if execution is None or execution.ok or not execution.stderr:
        return existing
    from qiskit_migration.migration.runtime_deprecations import deprecations_from_stderr, to_records

    known = {r.symbol for r in existing} | {d.symbol for d in base_deps}
    fresh = [
        r for r in to_records(deprecations_from_stderr(execution.stderr)) if r.symbol not in known
    ]
    return existing + fresh


def find_deprecations(
    code: str, store: DeprecationStore
) -> tuple[ExtractedSymbols, list[DeprecationRecord]]:
    """Offline: validate, extract symbols, and look up deprecations. No network."""
    validate_input(code)
    symbols = extract_symbols(code)
    return symbols, store.lookup(symbols.lookup_keys)


def _to_hit(rec: DeprecationRecord) -> DeprecationHit:
    return DeprecationHit(
        symbol=rec.symbol,
        status=rec.status,
        replacement=rec.replacement,
        since_version=rec.since_version,
        removed_in=rec.removed_in,
        note=rec.note,
        source=rec.source,
    )


class MigrationTransformer:
    def __init__(
        self,
        store,
        retriever,
        generator,
        target_version: str | None = None,
        sandbox=None,
        max_repairs: int | None = None,
        equivalence_checker=None,
    ):
        settings = get_settings()
        self.store = store
        self.retriever = retriever
        self.generator = generator
        self.target_version = target_version or settings.qiskit_target_version
        self.sandbox = sandbox
        self.max_repairs = settings.max_repairs if max_repairs is None else max_repairs
        # Optional callable (original_code, ported_code) -> EquivalenceReport. Injected so the
        # offline/test paths never spin up Docker; populated from settings when enabled.
        self.equivalence_checker = equivalence_checker

    @classmethod
    def from_settings(cls, db_path: str = "app.db") -> MigrationTransformer:
        # Imported lazily so the offline path never requires the LLM/embedding deps.
        from qiskit_migration.generation.generate import get_generator
        from qiskit_migration.migration.retrieval import MigrationRetriever
        from qiskit_migration.migration.sandbox import get_sandbox

        settings = get_settings()
        equivalence_checker = None
        if settings.equivalence_enabled:
            from qiskit_migration.migration.equivalence import (
                check_equivalence,
                default_equivalence_sandboxes,
            )

            old_sb, new_sb = default_equivalence_sandboxes()

            def equivalence_checker(original_code: str, ported_code: str):
                return check_equivalence(original_code, ported_code, old_sb, new_sb)

        return cls(
            DeprecationStore(db_path),
            MigrationRetriever.from_settings(),
            get_generator(),
            sandbox=get_sandbox(),
            equivalence_checker=equivalence_checker,
        )

    def transform(self, code: str, source_version: str | None = None) -> MigrationResult:
        warnings = validate_input(code)
        symbols = extract_symbols(code)
        deps = self.store.lookup(symbols.lookup_keys)

        # No-op safety: the architecture grounds "what changed" on the authoritative table,
        # so when it flags nothing there is nothing to migrate. Return the input verbatim
        # instead of letting the LLM rewrite — and possibly break — already-clean code, and
        # skip the retrieval + generation round-trip entirely. (A small model will happily
        # "tidy" correct imports into broken ones; the table, not the LLM, decides scope.)
        if not deps:
            return self._passthrough(code, source_version, warnings)

        chunks = self.retriever.retrieve(symbols, deps)

        # Initial generation, then a static + (optional) dynamic self-repair loop.
        feedback: str | None = None
        execution: SandboxReport | None = None
        attempts = 0
        # Root-2: deprecations the target library flags at runtime become authoritative deps,
        # so the loop is version-complete by construction (no static re-harvest needed).
        runtime_records: list[DeprecationRecord] = []
        llm_out = self.generator.transform(code, deps, chunks, source_version, feedback=feedback)
        report = validate_output(llm_out.ported_code, self.store, self.target_version)

        while True:
            execution = self.sandbox.run(llm_out.ported_code) if self.sandbox else None
            healthy = report.passed and (execution is None or execution.ok)
            if healthy or attempts >= self.max_repairs:
                break
            attempts += 1
            runtime_records = _merge_runtime_deprecations(runtime_records, execution, deps)
            feedback = _build_feedback(execution, report, runtime_records)
            llm_out = self.generator.transform(
                code, deps + runtime_records, chunks, source_version, feedback=feedback
            )
            report = validate_output(llm_out.ported_code, self.store, self.target_version)

        # Optional behavioral-equivalence check (old-on-old vs new-on-new). Best-effort:
        # an infra failure here must never sink an otherwise-successful migration.
        equivalence = None
        if self.equivalence_checker is not None:
            try:
                equivalence = self.equivalence_checker(code, llm_out.ported_code)
            except Exception:  # noqa: BLE001 - informational stage; degrade gracefully
                logger.exception("Behavioral-equivalence check failed; continuing without it.")

        return MigrationResult(
            target_version=self.target_version,
            source_version=source_version,
            ported_code=llm_out.ported_code,
            changes=llm_out.changes,
            warnings=warnings + list(llm_out.warnings),
            deprecations_found=[_to_hit(d) for d in (deps + runtime_records)],
            retrieval_sources=[c.get("source", "") for c in chunks],
            validation=report,
            execution=execution,
            repair_attempts=attempts,
            coverage=compute_coverage(llm_out.ported_code, deps + runtime_records, report),
            equivalence=equivalence,
        )

    def _passthrough(
        self, code: str, source_version: str | None, warnings: list[str]
    ) -> MigrationResult:
        """Return the input unchanged (no deprecations to migrate), no LLM call.

        Still runs static validation and, if a sandbox is configured, executes the code —
        an honest "does it run on the target?" signal that also flags the knowledge-gap case
        (an undetected old API surfaces here as a non-ok execution while the code is returned
        as-is, rather than being silently rewritten)."""
        report = validate_output(code, self.store, self.target_version)
        execution = self.sandbox.run(code) if self.sandbox else None
        note = (
            "No deprecated APIs detected against the target knowledge base; "
            "input returned unchanged."
        )
        return MigrationResult(
            target_version=self.target_version,
            source_version=source_version,
            ported_code=code,
            changes=[],
            warnings=warnings + [note],
            deprecations_found=[],
            retrieval_sources=[],
            validation=report,
            execution=execution,
            repair_attempts=0,
            coverage=compute_coverage(code, [], report),
        )
