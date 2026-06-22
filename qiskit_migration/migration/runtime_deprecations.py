"""Runtime deprecation capture — ground detection in what the *real library* warns about.

Qiskit's own upgrade guidance is "test on 0.46 first": 0.46 is the last 0.x release and
exists mainly to *emit a DeprecationWarning for every API that 1.0 removes*. This module
automates that advice. It runs the user's code on a legacy Qiskit image with warnings
captured (not promoted to errors), then parses the emitted messages into structured records.

Why this complements the static deprecation table (``deprecations.py``):

  * **Runtime-true & usage-specific** — it reports exactly the deprecations *this code*
    actually triggers, including ones the static table never harvested.
  * **Authoritative replacements for free** — Qiskit's warning text carries the official
    fix ("... Use ``assign_parameters()`` instead"), higher-quality than a heuristic guess,
    and ideal extra context to hand the LLM.
  * **No false positives** — a warning only fires if the deprecated path is genuinely hit.

The trade-off vs. the static table: it only sees deprecations that *the chosen image's*
Qiskit knows about (run on 0.46 to surface the 1.0 wave; on 2.x to surface 2.x-era ones)
and only along code paths that execute. So it augments static detection, it doesn't replace
it. Same sandbox infrastructure as the behavioral-equivalence check.
"""

from __future__ import annotations

import json
import logging
import re

from qiskit_migration.config import get_settings
from qiskit_migration.migration.deprecations import DeprecationRecord
from qiskit_migration.migration.models import RuntimeDeprecation, RuntimeDeprecationReport
from qiskit_migration.migration.sandbox import Sandbox

logger = logging.getLogger(__name__)

# Deprecation surfaced in a sandbox traceback (a `-W error::DeprecationWarning` run): the
# offending warning lands as the final ``DeprecationWarning: <message>`` line.
_STDERR_WARN_RE = re.compile(r"(?:Deprecation|PendingDeprecation|Future)Warning: (.+)")

_CAPTURE = 2_000_000  # warning dumps can be large; override the sandbox's default 4 KB cap
_BEGIN = "__RTDEP1__"
_END = "__RTEND1__"
_STARTED = "__RTSTART__"  # printed once the harness is set up — proves the run actually began
_RE = re.compile(re.escape(_BEGIN) + r"(.*?)" + re.escape(_END))

# Categories that signal an upcoming/current removal worth migrating.
_DEPRECATION_CATEGORIES = {"DeprecationWarning", "PendingDeprecationWarning", "FutureWarning"}

# Prepended to the user code: install a showwarning hook that emits each warning *immediately*
# (flushed), one sentinel-wrapped JSON record per line. Emitting incrementally — rather than
# buffering and dumping at exit — means a long-running script that times out or crashes still
# yields every warning it raised before dying (e.g. all the import/construction deprecations,
# which fire before a slow optimization loop). Uses only stdlib present in every Qiskit image.
_HARNESS = """\
import warnings as __w, json as __json


def __rt_hook(message, category, filename, lineno, file=None, line=None):
    try:
        print(
            "__RTDEP1__"
            + __json.dumps(
                {
                    "category": getattr(category, "__name__", str(category)),
                    "message": str(message),
                    "filename": str(filename),
                    "lineno": int(lineno) if lineno is not None else None,
                }
            )
            + "__RTEND1__",
            flush=True,
        )
    except Exception:
        pass


__w.showwarning = __rt_hook
for __cat in (DeprecationWarning, PendingDeprecationWarning, FutureWarning):
    __w.simplefilter("always", __cat)
print("__RTSTART__", flush=True)


# ===================== user code below =====================
"""

# Number of lines the harness prepends, so a warning's lineno can be mapped back to the
# user's source (only meaningful when the warning fires inside the snippet file itself).
_HARNESS_LINES = _HARNESS.count("\n")

# Symbol in double backticks: ``qiskit.opflow.X`` or ``QuantumCircuit.bind_parameters()``.
_BACKTICK_RE = re.compile(r"``([^`]+)``")
_SINCE_RE = re.compile(r"as of (?:qiskit[\w-]*) ([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)
_REMOVED_RE = re.compile(r"removed in (?:the )?(?:qiskit )?([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)
# Replacement hint: "Use X instead" / "use ``X`` instead" / "Use the function ``X``".
_USE_RE = re.compile(r"[Uu]se (?:the \w+ )?`?`?([A-Za-z_][\w.]*(?:\(\))?)`?`?", re.IGNORECASE)
# Articles/determiners the Use-regex may grab by mistake ("use *the* simulators").
_STOPWORDS = {"the", "a", "an", "this", "that", "it", "them", "these", "those", "function", "class"}


def build_capture_harness(user_code: str) -> str:
    """Return ``user_code`` with the warning-capture harness prepended."""
    return _HARNESS + user_code


def parse_warnings(stdout: str) -> list[dict]:
    """Collect every per-warning record the harness emitted (incremental, one per sentinel)."""
    records: list[dict] = []
    for raw in _RE.findall(stdout or ""):
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _clean_symbol(raw: str) -> str:
    """Trim a backticked symbol to a bare dotted path (drop call parens / leading module noise)."""
    sym = raw.strip().rstrip("()")
    # Qiskit often gives the fully-qualified internal path; keep it but drop a trailing call.
    return sym


def _parse_message(message: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract (symbol, replacement, since_version, removed_in) from a deprecation message."""
    backticked = _BACKTICK_RE.findall(message)
    symbol = _clean_symbol(backticked[0]) if backticked else None

    since = _SINCE_RE.search(message)
    removed = _REMOVED_RE.search(message)

    replacement: str | None = None
    # Prefer a second backticked token that appears after "use"/"instead"; else the Use-regex.
    if len(backticked) > 1 and re.search(r"\b(use|instead|migrat)", message, re.IGNORECASE):
        replacement = _clean_symbol(backticked[1])
    else:
        m = _USE_RE.search(message)
        if m:
            replacement = m.group(1).rstrip("()")
    # Drop a captured stopword (e.g. "use *the* simulators ...") — it's noise, not a symbol.
    if replacement and replacement.lower() in _STOPWORDS:
        replacement = None
    if replacement == symbol:
        replacement = None
    return (
        symbol,
        replacement,
        (since.group(1) if since else None),
        (removed.group(1) if removed else None),
    )


def deprecations_from_stderr(stderr: str) -> list[RuntimeDeprecation]:
    """Recover deprecation records from a sandbox traceback (a warnings-as-errors run).

    The pipeline's validation sandbox runs with ``-W error::DeprecationWarning``, so the
    first offending deprecation aborts the run with a ``DeprecationWarning: <message>``
    line. This parses those messages — the *target library's own* authoritative signal,
    current for whatever version the sandbox runs — into structured records.
    """
    out: list[RuntimeDeprecation] = []
    seen: set[tuple[str | None, str | None]] = set()
    for raw in _STDERR_WARN_RE.findall(stderr or ""):
        message = raw.strip()
        symbol, replacement, since, removed = _parse_message(message)
        key = (symbol, removed)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            RuntimeDeprecation(
                symbol=symbol,
                replacement=replacement,
                since_version=since,
                removed_in=removed,
                category="DeprecationWarning",
                message=message,
            )
        )
    return out


def to_records(rdeps: list[RuntimeDeprecation]) -> list[DeprecationRecord]:
    """Convert captured runtime deprecations into authoritative-tier ``DeprecationRecord``s.

    Lets the migration loop treat what the *target library itself* flagged as first-class
    deprecation knowledge (``source="runtime-sandbox"``) — version-current by construction,
    no static harvest required. Records without a parseable symbol are dropped.
    """
    records: list[DeprecationRecord] = []
    for d in rdeps:
        if not d.symbol:
            continue
        records.append(
            DeprecationRecord(
                symbol=d.symbol,
                status="deprecated",
                since_version=d.since_version,
                removed_in=d.removed_in,
                replacement=d.replacement,
                note=d.message[:300],
                source="runtime-sandbox",
            )
        )
    return records


def capture_runtime_deprecations(code: str, sandbox: Sandbox) -> RuntimeDeprecationReport:
    """Execute ``code`` on ``sandbox`` (an old-Qiskit image) and structure the warnings it emits.

    Run with ``warnings_as_errors=False`` so the code runs to completion and the harness can
    collect every warning, then dedup by (symbol, removed_in) keeping the first message.
    """
    report = sandbox.run(
        build_capture_harness(code), warnings_as_errors=False, max_capture=_CAPTURE
    )
    backend = getattr(sandbox, "backend", "unknown")
    started = _STARTED in (report.stdout or "")
    raw = parse_warnings(report.stdout)

    if not started and not raw:
        return RuntimeDeprecationReport(
            backend=backend,
            ran=False,
            deprecations=[],
            note="Code did not start on the legacy image "
            f"(error_type={report.error_type}, timed_out={report.timed_out}).",
        )

    seen: set[tuple[str | None, str | None]] = set()
    deps: list[RuntimeDeprecation] = []
    for w in raw:
        if w.get("category") not in _DEPRECATION_CATEGORIES:
            continue
        message = w.get("message", "")
        symbol, replacement, since, removed = _parse_message(message)
        key = (symbol, removed)
        if key in seen:
            continue
        seen.add(key)
        lineno = w.get("lineno")
        filename = w.get("filename") or ""
        user_lineno = (
            lineno - _HARNESS_LINES
            if lineno and filename.endswith("snippet.py") and lineno > _HARNESS_LINES
            else None
        )
        deps.append(
            RuntimeDeprecation(
                symbol=symbol,
                replacement=replacement,
                since_version=since,
                removed_in=removed,
                category=w.get("category", "DeprecationWarning"),
                message=message.strip(),
                user_lineno=user_lineno,
            )
        )

    partial = " (code timed out — captured up to that point)" if report.timed_out else ""
    note = (
        f"{len(deps)} distinct runtime deprecation(s) captured on the legacy image{partial}."
        if deps
        else f"Code ran with no deprecation warnings on the legacy image{partial}."
    )
    return RuntimeDeprecationReport(backend=backend, ran=True, deprecations=deps, note=note)


def legacy_sandbox() -> Sandbox:
    """Construct the Docker sandbox for the pinned legacy Qiskit image."""
    from qiskit_migration.migration.sandbox import DockerSandbox

    return DockerSandbox(image=get_settings().legacy_sandbox_image)
