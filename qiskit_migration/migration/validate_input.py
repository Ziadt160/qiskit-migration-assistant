"""Input guardrails for user-submitted code.

Hard-fails (raise) on anything we should not even attempt to migrate; returns soft
warnings (e.g. an apparent secret in the pasted code) the caller can surface.
"""

from __future__ import annotations

import ast
import re

from qiskit_migration.config import get_settings

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[=:]\s*['\"][A-Za-z0-9_\-]{12,}['\"]"
)


class InputValidationError(ValueError):
    """Raised when submitted code cannot be accepted for migration."""


def validate_input(code: str, max_chars: int | None = None) -> list[str]:
    """Validate submitted code. Raises `InputValidationError` on hard failures;
    returns a list of soft warnings (possibly empty)."""
    max_chars = max_chars or get_settings().max_input_chars

    if not code or not code.strip():
        raise InputValidationError("No code was provided.")
    if len(code) > max_chars:
        raise InputValidationError(f"Code is too large ({len(code)} chars > limit {max_chars}).")
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise InputValidationError(f"Submitted code is not valid Python: {e}") from e

    warnings: list[str] = []
    if _SECRET_RE.search(code):
        warnings.append(
            "Possible hard-coded secret detected in the submitted code — "
            "remove credentials before sharing or migrating."
        )
    return warnings
