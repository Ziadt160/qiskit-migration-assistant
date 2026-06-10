"""Autonomous deprecation-record harvester — Stage 1→3 of the knowledge pipeline (§12.1).

Grows the trusted table *without* hand-curation by chaining four stages:

  1. MINE      Griffe statically diffs two Qiskit versions → public symbols that were
               removed/moved (high recall, but noisy: it also flags moved-but-still-
               importable symbols, which stage 3 filters out).
  2. PROPOSE   a best-effort replacement hypothesis from the old version's deprecation
               docstring ("Use ``X`` instead"); ``None`` when no hint is found.
  3. VERIFY    each candidate is executed against the target Qiskit in the sandbox
               (`verify_record`): the old symbol must FAIL, any replacement must IMPORT.
  4. PROMOTE   only verified candidates enter the store, tagged ``source="sandbox-verified"``
               (ranks below ``curated-seed``, above the heuristic parser — see ``_score``).

`mine_candidates` is the only stage that touches Griffe / the network; it imports Griffe
lazily so the rest of the system and the unit tests never require it. The orchestration
(`harvest_candidates`) and the parsing helpers are pure and hermetically tested.

Run it::

    pip install -e '.[harvest]'
    python -m src.migration.harvest --old qiskit-terra==0.46.3 --new qiskit==2.0.2 \
        --sandbox-backend docker --limit 100 --promote
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.migration.deprecations import DeprecationRecord, DeprecationStore
from src.migration.sandbox import Sandbox, get_sandbox
from src.migration.verify_record import RecordVerdict, verify_candidates

logger = logging.getLogger(__name__)

# "Use `cx` instead", "Instead, use QuantumCircuit.cx()", "replaced by PauliList", ...
_REPLACEMENT_PATTERNS = [
    re.compile(r"[Ii]nstead,?\s+use\s+`?([A-Za-z_][\w.]*)`?"),
    re.compile(r"[Uu]se\s+`?([A-Za-z_][\w.]*)`?\s+instead"),
    re.compile(r"(?:replaced by|in favor of)\s+`?([A-Za-z_][\w.]*)`?"),
]
_SOURCE_VERIFIED = "sandbox-verified"


def _extract_replacement(text: str | None) -> str | None:
    """Pull a replacement symbol out of a deprecation message/docstring, if stated."""
    if not text:
        return None
    for pattern in _REPLACEMENT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).rstrip("().")
    return None


def _is_public(object_path: str) -> bool:
    """True when no path segment is private (leading underscore)."""
    return bool(object_path) and not any(seg.startswith("_") for seg in object_path.split("."))


def _candidates_from_breakages(
    breakages: list[dict],
    removed_in: str,
    get_docstring=None,
    *,
    public_only: bool = True,
) -> list[dict]:
    """Turn normalised Griffe breakages into candidate records (pure; Griffe-free).

    Each breakage is ``{"kind": <name>, "object_path": <dotted>}``. We keep object
    removals, optionally restrict to the public surface, dedupe by symbol, and attach a
    replacement hypothesis via ``get_docstring(object_path) -> str | None``.
    """
    seen: set[str] = set()
    candidates: list[dict] = []
    for b in breakages:
        if b.get("kind") != "OBJECT_REMOVED":
            continue
        symbol = b.get("object_path", "")
        if not symbol or symbol in seen:
            continue
        if public_only and not _is_public(symbol):
            continue
        seen.add(symbol)
        doc = get_docstring(symbol) if get_docstring else None
        candidates.append(
            {
                "symbol": symbol,
                "replacement": _extract_replacement(doc),
                "status": "removed",
                "removed_in": removed_in,
            }
        )
    return candidates


# --------------------------------------------------------------------------- #
# Stage 1 — live mining (lazy Griffe; downloads wheels, no execution)
# --------------------------------------------------------------------------- #


def _download_and_extract(pkg_spec: str, cache_dir: Path) -> Path:
    """pip-download a wheel for ``pkg_spec`` (any platform) and unzip it; return the dir
    containing the importable ``qiskit/`` source tree. Static analysis only — never installed,
    so the host interpreter/platform is irrelevant (works on 3.14 with no Qiskit wheels)."""
    import subprocess

    target = cache_dir / pkg_spec.replace("==", "-").replace("/", "_")
    qiskit_pkg = target / "qiskit"
    if qiskit_pkg.is_dir():
        return target
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            pkg_spec,
            "--no-deps",
            "--only-binary",
            ":all:",
            "--python-version",
            "312",
            "--implementation",
            "cp",
            "--abi",
            "abi3",
            "--platform",
            "manylinux2014_x86_64",
            "-d",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(target.glob("*.whl"))
    if not wheels:
        raise RuntimeError(f"no wheel downloaded for {pkg_spec}")
    zipfile.ZipFile(wheels[0]).extractall(target)
    return target


def mine_candidates(
    old_pkg_spec: str,
    new_pkg_spec: str,
    *,
    cache_dir: str = "build/harvest_cache",
    public_only: bool = True,
    limit: int | None = None,
) -> list[dict]:
    """Stage 1+2: Griffe-diff two Qiskit versions → candidate removed-symbol records.

    ``*_pkg_spec`` are pip specifiers; note the 0.x public API ships in ``qiskit-terra``
    (e.g. ``old="qiskit-terra==0.46.3"``, ``new="qiskit==2.0.2"``). Requires the
    ``harvest`` extra (Griffe).
    """
    import griffe  # lazy: only the harvester needs it

    cache = Path(cache_dir)
    old_dir = _download_and_extract(old_pkg_spec, cache)
    new_dir = _download_and_extract(new_pkg_spec, cache)
    old = griffe.load("qiskit", search_paths=[str(old_dir)], allow_inspection=False)
    new = griffe.load("qiskit", search_paths=[str(new_dir)], allow_inspection=False)

    breakages = [
        {
            "kind": getattr(d.get("kind"), "name", str(d.get("kind"))),
            "object_path": d.get("object_path", ""),
        }
        for d in (b.as_dict() for b in griffe.find_breaking_changes(old, new))
    ]

    def get_docstring(object_path: str) -> str | None:
        try:
            relative = object_path.split(".", 1)[1]  # drop leading "qiskit."
            obj = old[relative]
            return obj.docstring.value if obj.docstring else None
        except Exception:
            return None

    removed_in = new_pkg_spec.partition("==")[2] or "2.0"
    candidates = _candidates_from_breakages(
        breakages, removed_in, get_docstring, public_only=public_only
    )
    return candidates[:limit] if limit else candidates


# --------------------------------------------------------------------------- #
# Stage 3+4 — verify & promote (pure orchestration; testable with a fake sandbox)
# --------------------------------------------------------------------------- #


@dataclass
class HarvestReport:
    mined: int
    verified: int
    promoted: int
    records: list[DeprecationRecord] = field(default_factory=list)
    verdicts: list[RecordVerdict] = field(default_factory=list)


def harvest_candidates(
    candidates: list[dict],
    sandbox: Sandbox,
    *,
    store: DeprecationStore | None = None,
    source: str = _SOURCE_VERIFIED,
) -> HarvestReport:
    """Stage 3+4: execution-verify candidates and promote the survivors.

    Each candidate is ``{symbol, replacement, status, removed_in}``. A candidate is promoted
    when the sandbox confirms the old symbol is genuinely absent on the target
    (``old_absent``); the replacement is attached only if it *also* verified, else dropped.
    When ``store`` is given the records are upserted with ``source`` (a tier below the curated
    seed). Pure w.r.t. Griffe — inject any sandbox.
    """
    verdicts = verify_candidates(candidates, sandbox)
    records: list[DeprecationRecord] = []
    for cand, verdict in zip(candidates, verdicts, strict=True):
        # Gate on the *removal* being verified, not the replacement: a removed symbol with a
        # bad/garbage replacement hypothesis is still a valid detection record — we just drop
        # the unverified replacement rather than discarding the whole record.
        if not verdict.old_absent:
            continue
        repl = verdict.replacement if verdict.replacement_ok else None
        records.append(
            DeprecationRecord(
                symbol=cand["symbol"],
                status=cand.get("status", "removed"),
                removed_in=cand.get("removed_in"),
                replacement=repl,
                note=(
                    f"Auto-harvested via Griffe API-diff; sandbox-verified removed"
                    f"{f' (use {repl})' if repl else ''}."
                ),
                source=source,
            )
        )
    promoted = 0
    if store is not None and records:
        promoted = store.upsert_many(records)
    return HarvestReport(
        mined=len(candidates),
        verified=len(records),
        promoted=promoted,
        records=records,
        verdicts=verdicts,
    )


def harvest(
    old_pkg_spec: str,
    new_pkg_spec: str,
    sandbox: Sandbox,
    *,
    store: DeprecationStore | None = None,
    cache_dir: str = "build/harvest_cache",
    limit: int | None = None,
) -> HarvestReport:
    """Full pipeline: mine (Griffe) → verify (sandbox) → promote (store)."""
    candidates = mine_candidates(old_pkg_spec, new_pkg_spec, cache_dir=cache_dir, limit=limit)
    return harvest_candidates(candidates, sandbox, store=store)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harvest sandbox-verified deprecation records.")
    parser.add_argument("--old", required=True, help="old pip spec, e.g. qiskit-terra==0.46.3")
    parser.add_argument("--new", required=True, help="new pip spec, e.g. qiskit==2.0.2")
    parser.add_argument("--sandbox-backend", default="docker", help="local | docker")
    parser.add_argument("--db", default="app.db")
    parser.add_argument("--cache-dir", default="build/harvest_cache")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--promote", action="store_true", help="Write verified records to --db.")
    args = parser.parse_args(argv)

    sandbox = get_sandbox(args.sandbox_backend)
    if sandbox is None:
        print(f"No sandbox for backend {args.sandbox_backend!r}; verification needs one.")
        return 2

    store = DeprecationStore(args.db) if args.promote else None
    if store is not None:
        store.create()

    report = harvest(
        args.old, args.new, sandbox, store=store, cache_dir=args.cache_dir, limit=args.limit
    )
    print(f"Mined {report.mined} candidates → {report.verified} sandbox-verified.")
    for rec in report.records:
        repl = f" -> {rec.replacement}" if rec.replacement else ""
        print(f"  + {rec.symbol}{repl}")
    if args.promote:
        print(f"Promoted {report.promoted} records to {args.db} (source={_SOURCE_VERIFIED}).")
    else:
        print("Dry run (pass --promote to write to the store).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
