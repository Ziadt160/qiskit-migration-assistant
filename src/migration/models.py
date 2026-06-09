"""Typed models shared across the migration pipeline.

`LLMTransformOutput` doubles as the structured-output schema handed to Gemini, so
the model returns validated objects instead of free text we have to parse.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CodeChange(BaseModel):
    old: str = Field(description="The old API or usage that was replaced.")
    new: str = Field(description="The new API or usage it was replaced with.")
    reason: str = Field(description="Why it changed (deprecation/removal/move).")
    since_version: str | None = Field(
        default=None, description="Qiskit version where the old API was deprecated/removed."
    )
    citation: str | None = Field(
        default=None, description="Release note / migration guide source backing this change."
    )


class LLMTransformOutput(BaseModel):
    """Exactly what the LLM must return."""

    ported_code: str = Field(description="The full migrated code for the target Qiskit version.")
    changes: list[CodeChange] = Field(
        default_factory=list, description="One entry per API change applied."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Anything that could not be migrated confidently from the provided context.",
    )


class DeprecationHit(BaseModel):
    symbol: str
    status: str
    replacement: str | None = None
    since_version: str | None = None
    removed_in: str | None = None
    note: str = ""
    source: str = ""


class ValidationReport(BaseModel):
    syntax_ok: bool
    deprecated_symbols: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.syntax_ok and not self.deprecated_symbols and not self.errors


class SandboxReport(BaseModel):
    backend: str
    ok: bool
    returncode: int | None = None
    error_type: str | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""


class CoverageSummary(BaseModel):
    """How much of the detected deprecation surface the migration actually resolved."""

    handled: int
    total: int
    unresolved: list[str] = Field(default_factory=list)  # detected APIs still in the output
    validation_passed: bool = False


class MigrationResult(BaseModel):
    target_version: str
    source_version: str | None = None
    ported_code: str = ""
    changes: list[CodeChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    deprecations_found: list[DeprecationHit] = Field(default_factory=list)
    retrieval_sources: list[str] = Field(default_factory=list)
    validation: ValidationReport | None = None
    execution: SandboxReport | None = None
    repair_attempts: int = 0
    coverage: CoverageSummary | None = None
