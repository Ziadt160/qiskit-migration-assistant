"""End-to-end migration orchestration.

`find_deprecations` is the offline core (input guard -> symbols -> deprecation
lookup) usable with no network. `MigrationTransformer` adds the live stages
(retrieval + Gemini transform + static validation). Retriever/generator are
injected so the pipeline can be unit-tested with fakes.
"""

from __future__ import annotations

import logging

from src.config import get_settings
from src.migration.deprecations import DeprecationRecord, DeprecationStore
from src.migration.models import (
    DeprecationHit,
    MigrationResult,
    SandboxReport,
    ValidationReport,
)
from src.migration.report import compute_coverage
from src.migration.symbols import ExtractedSymbols, extract_symbols
from src.migration.validate_input import validate_input
from src.migration.validate_output import validate_output

logger = logging.getLogger(__name__)


def _build_feedback(execution: SandboxReport | None, report: ValidationReport) -> str:
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
    if report.deprecated_symbols:
        parts.append(
            "These symbols are still deprecated/removed on the target version and must "
            "be replaced: " + ", ".join(report.deprecated_symbols)
        )
    if not report.syntax_ok:
        parts.append("The ported code does not parse as valid Python.")
    return "\n\n".join(parts) or "(none)"


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
    ):
        settings = get_settings()
        self.store = store
        self.retriever = retriever
        self.generator = generator
        self.target_version = target_version or settings.qiskit_target_version
        self.sandbox = sandbox
        self.max_repairs = settings.max_repairs if max_repairs is None else max_repairs

    @classmethod
    def from_settings(cls, db_path: str = "app.db") -> MigrationTransformer:
        # Imported lazily so the offline path never requires the LLM/embedding deps.
        from src.generation.generate import get_generator
        from src.migration.retrieval import MigrationRetriever
        from src.migration.sandbox import get_sandbox

        return cls(
            DeprecationStore(db_path),
            MigrationRetriever.from_settings(),
            get_generator(),
            sandbox=get_sandbox(),
        )

    def transform(self, code: str, source_version: str | None = None) -> MigrationResult:
        warnings = validate_input(code)
        symbols = extract_symbols(code)
        deps = self.store.lookup(symbols.lookup_keys)
        chunks = self.retriever.retrieve(symbols, deps)

        # Initial generation, then a static + (optional) dynamic self-repair loop.
        feedback: str | None = None
        execution: SandboxReport | None = None
        attempts = 0
        llm_out = self.generator.transform(code, deps, chunks, source_version, feedback=feedback)
        report = validate_output(llm_out.ported_code, self.store, self.target_version)

        while True:
            execution = self.sandbox.run(llm_out.ported_code) if self.sandbox else None
            healthy = report.passed and (execution is None or execution.ok)
            if healthy or attempts >= self.max_repairs:
                break
            attempts += 1
            feedback = _build_feedback(execution, report)
            llm_out = self.generator.transform(
                code, deps, chunks, source_version, feedback=feedback
            )
            report = validate_output(llm_out.ported_code, self.store, self.target_version)

        return MigrationResult(
            target_version=self.target_version,
            source_version=source_version,
            ported_code=llm_out.ported_code,
            changes=llm_out.changes,
            warnings=warnings + list(llm_out.warnings),
            deprecations_found=[_to_hit(d) for d in deps],
            retrieval_sources=[c.get("source", "") for c in chunks],
            validation=report,
            execution=execution,
            repair_attempts=attempts,
            coverage=compute_coverage(llm_out.ported_code, deps, report),
        )
