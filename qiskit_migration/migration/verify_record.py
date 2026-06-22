"""Execution-verified deprecation records — the trust gate for auto-harvested knowledge.

The deprecation table is the system's precision backbone, so a record may only enter
the trusted tier if the *real library* agrees with it. This module is that gate:

  * the OLD symbol must FAIL on the target Qiskit — confirming it is genuinely gone
    (or at least deprecated; the sandbox runs with ``-W error::DeprecationWarning``);
  * the REPLACEMENT must IMPORT CLEAN on the target — confirming it is a valid landing
    spot, not a hallucination or itself-deprecated.

Generators *propose* candidates (a Griffe API-diff between versions, release-note / LLM
extraction, the ``flake8-qiskit-migration`` ruleset, …); this module + the existing
:class:`~src.migration.sandbox.Sandbox` *dispose*. Only ``verified`` candidates should be
promoted to the curated/sandbox-verified tier; everything else is routed to human review.

Scope/limitation: a probe resolves a symbol by import + attribute access, so it confirms
**removed** symbols with high confidence (the bulk of 0.x→2.x breakage). A symbol that is
merely deprecated but still importable *without* warning-on-import, or a *conceptual*
replacement (e.g. ``backend.run``, ``qiskit_aer save instructions``) that is not a directly
importable name, is conservatively **rejected to review** rather than auto-trusted — the
gate never produces a false ``verified``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qiskit_migration.migration.models import SandboxReport
from qiskit_migration.migration.sandbox import Sandbox

# A dotted Python identifier path, e.g. ``qiskit.execute`` or ``QuantumCircuit.cnot``.
_SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")

# Errors that mean the symbol genuinely does not resolve (vs. a sandbox/infra failure,
# which must NOT be read as "removed" — otherwise an outage would mark everything gone).
_ABSENT_ERRORS = {"ImportError", "ModuleNotFoundError", "AttributeError"}

# Probe: resolve a dotted symbol by importing the longest importable module prefix, then
# walking the remaining segments as attributes. Bare ``Class.method`` paths (no importable
# module prefix) fall back to the ``qiskit`` namespace. Any failure to resolve raises and
# the process exits non-zero — which is exactly the signal the verifier reads.
_PROBE_TEMPLATE = """\
import importlib


def _resolve(dotted):
    parts = dotted.split(".")
    obj = None
    consumed = 0
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
            consumed = i
            break
        except Exception:
            continue
    if obj is None:
        import qiskit

        obj = qiskit
    for segment in parts[consumed:]:
        obj = getattr(obj, segment)
    return obj


_resolve({symbol!r})
"""


@dataclass
class RecordVerdict:
    """Outcome of executing a candidate {symbol -> replacement} against the target."""

    symbol: str
    replacement: str | None
    verified: bool
    reason: str
    old_present: bool  # did the OLD symbol still import cleanly on the target?
    old_absent: (
        bool  # did it genuinely fail to resolve (ImportError/AttributeError), not an infra error?
    )
    replacement_ok: bool | None  # did the replacement import clean? None = not probed
    old_report: SandboxReport | None = None
    new_report: SandboxReport | None = None


def make_probe(symbol: str) -> str:
    """Return runnable Python that resolves ``symbol`` and exits 0 iff it is accessible."""
    if not _SYMBOL_RE.match(symbol):
        raise ValueError(f"not a dotted identifier: {symbol!r}")
    return _PROBE_TEMPLATE.format(symbol=symbol)


def verify_candidate(symbol: str, replacement: str | None, sandbox: Sandbox) -> RecordVerdict:
    """Execution-verify one candidate record against the sandbox's target Qiskit.

    ``verified`` requires the old symbol to be absent/deprecated on the target AND, when a
    directly-importable replacement is given, for it to import cleanly. Conceptual or
    non-importable replacements are rejected to review, never silently trusted.
    """
    if not _SYMBOL_RE.match(symbol):
        return RecordVerdict(
            symbol,
            replacement,
            False,
            "rejected: symbol is not a dotted identifier",
            old_present=False,
            old_absent=False,
            replacement_ok=None,
        )

    old_report = sandbox.run(make_probe(symbol))
    old_present = bool(old_report.ok)
    # "Absent" requires a genuine resolution error — a timeout or sandbox/infra failure is
    # inconclusive and must never be promoted as a removal.
    old_absent = (
        not old_present
        and not getattr(old_report, "timed_out", False)
        and old_report.error_type in _ABSENT_ERRORS
    )

    new_report: SandboxReport | None = None
    replacement_ok: bool | None = None
    verifiable_replacement = bool(replacement) and _SYMBOL_RE.match(replacement) is not None
    if verifiable_replacement:
        new_report = sandbox.run(make_probe(replacement))  # type: ignore[arg-type]
        replacement_ok = bool(new_report.ok)

    if old_present:
        verified, reason = (
            False,
            "rejected: old symbol still imports cleanly on target (not removed/deprecated there)",
        )
    elif not old_absent:
        verified, reason = (
            False,
            "rejected: old probe inconclusive (sandbox/infra error, not a clean absence)",
        )
    elif replacement and not verifiable_replacement:
        verified, reason = (
            False,
            "rejected: replacement is not a directly importable symbol; needs human review",
        )
    elif verifiable_replacement and not replacement_ok:
        verified, reason = False, "rejected: replacement does not import on target"
    elif replacement:
        verified, reason = (
            True,
            "verified: old symbol absent on target; replacement imports cleanly",
        )
    else:
        verified, reason = True, "verified: old symbol absent on target (no replacement to check)"

    return RecordVerdict(
        symbol=symbol,
        replacement=replacement,
        verified=verified,
        reason=reason,
        old_present=old_present,
        old_absent=old_absent,
        replacement_ok=replacement_ok,
        old_report=old_report,
        new_report=new_report,
    )


def verify_candidates(candidates: list[dict], sandbox: Sandbox) -> list[RecordVerdict]:
    """Verify a batch of candidate dicts (each with ``symbol`` and optional ``replacement``).

    Generators emit candidates in this shape; promote only the ``verified`` verdicts.
    Runs serially (two sandbox executions per candidate) — fine for a curation pass;
    parallelise at the call site if throughput matters.
    """
    return [verify_candidate(c["symbol"], c.get("replacement"), sandbox) for c in candidates]
