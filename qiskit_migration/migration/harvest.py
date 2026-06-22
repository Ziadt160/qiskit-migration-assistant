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

Two MINE modes feed the same VERIFY→PROMOTE stages:

  * ``griffe`` (default) — same-package API diff between two versions; finds *removals*.
  * ``cross-package`` — Griffe is blind to a symbol that moved to a *different* package
    (``qiskit.aqua`` → the standalone ecosystem). This mode enumerates the legacy package's
    API surface inside a throwaway container and name-matches each symbol to an index of the
    ecosystem on the target image; finds *moved-same-name* symbols (renames stay curated).

Run it::

    pip install -e '.[harvest]'
    # same-package removals
    python -m qiskit_migration.migration.harvest --old qiskit-terra==0.46.3 --new qiskit==2.0.2 \
        --sandbox-backend docker --limit 100 --promote
    # cross-package moves (legacy package → ecosystem)
    python -m qiskit_migration.migration.harvest --mode cross-package \
        --old qiskit-aqua==0.9.0 --old-root qiskit.aqua --enum-image python:3.9-slim --promote
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from qiskit_migration.migration.deprecations import DeprecationRecord, DeprecationStore
from qiskit_migration.migration.sandbox import Sandbox, get_sandbox
from qiskit_migration.migration.verify_record import RecordVerdict, verify_candidate

logger = logging.getLogger(__name__)

# "Use `cx` instead", "Instead, use QuantumCircuit.cx()", "replaced by PauliList", ...
_REPLACEMENT_PATTERNS = [
    re.compile(r"[Ii]nstead,?\s+use\s+`?([A-Za-z_][\w.]*)`?"),
    re.compile(r"[Uu]se\s+`?([A-Za-z_][\w.]*)`?\s+instead"),
    re.compile(r"(?:replaced by|in favor of)\s+`?([A-Za-z_][\w.]*)`?"),
]
_SOURCE_VERIFIED = "sandbox-verified"
# Common words a loose "use X" pattern grabs by mistake ("use the new API" -> "the").
_REPLACEMENT_STOPWORDS = {"the", "a", "an", "this", "it", "instead", "use", "using", "either"}


def _extract_replacement(text: str | None) -> str | None:
    """Pull a replacement symbol out of a deprecation message/docstring, if stated.

    Skips obvious stopword captures so we don't propose junk; the sandbox would reject it
    anyway, but filtering here saves a verification probe and keeps the candidate clean.
    """
    if not text:
        return None
    for pattern in _REPLACEMENT_PATTERNS:
        for cand in pattern.findall(text):
            cand = cand.rstrip("().")
            if len(cand) > 1 and cand.lower() not in _REPLACEMENT_STOPWORDS:
                return cand
    return None


def _is_public(object_path: str) -> bool:
    """True when no path segment is private (leading underscore)."""
    return bool(object_path) and not any(seg.startswith("_") for seg in object_path.split("."))


def _candidates_from_breakages(
    breakages: list[dict],
    removed_in: str,
    get_message=None,
    *,
    public_only: bool = True,
    dedupe_by_segment: bool = True,
) -> list[dict]:
    """Turn normalised Griffe breakages into candidate records (pure; Griffe-free).

    Each breakage is ``{"kind": <name>, "object_path": <dotted>}``. We keep object removals,
    optionally restrict to the public surface, attach a replacement hypothesis from
    ``get_message(object_path) -> str | None`` (a deprecation message/docstring, parsed by
    ``_extract_replacement``), and — by default — collapse same-last-segment duplicates
    (e.g. an inherited ``diagonal`` removed from a dozen subclasses) to the shortest,
    most-canonical path. Dedupe is detection-preserving: the table matches on the last
    segment, so the collapsed record carries the same signal with far less bloat.
    """
    seen: set[str] = set()
    chosen: dict[str, dict] = {}
    out: list[dict] = []
    for b in breakages:
        if b.get("kind") != "OBJECT_REMOVED":
            continue
        symbol = b.get("object_path", "")
        if not symbol or symbol in seen:
            continue
        if public_only and not _is_public(symbol):
            continue
        seen.add(symbol)
        cand = {
            "symbol": symbol,
            "replacement": _extract_replacement(get_message(symbol) if get_message else None),
            "status": "removed",
            "removed_in": removed_in,
        }
        if not dedupe_by_segment:
            out.append(cand)
            continue
        segment = symbol.rsplit(".", 1)[-1]
        current = chosen.get(segment)
        if current is None or symbol.count(".") < current["symbol"].count("."):
            chosen[segment] = cand
    if dedupe_by_segment:
        out = sorted(chosen.values(), key=lambda c: c["symbol"])
    return out


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

    def _old_obj(object_path: str):
        try:
            return old[object_path.split(".", 1)[1]]  # drop leading "qiskit."
        except Exception:
            return None

    # Griffe reports a removed *module* as a single breakage, not its contents — so a function
    # inside a removed module (e.g. graysynth in qiskit.transpiler.synthesis) would be invisible.
    # Expand each removed module to its public, direct members (one level; nested modules carry
    # their own breakages or get expanded via theirs).
    expanded = list(breakages)
    for b in breakages:
        if b["kind"] != "OBJECT_REMOVED":
            continue
        obj = _old_obj(b["object_path"])
        if obj is None:
            continue
        try:  # Griffe resolves aliases lazily and can raise on access, not just AttributeError.
            if not getattr(obj, "is_module", False):
                continue
            members = [n for n in getattr(obj, "members", {}) if not n.startswith("_")]
        except Exception:
            continue
        for name in members:
            expanded.append({"kind": "OBJECT_REMOVED", "object_path": f"{b['object_path']}.{name}"})

    def get_message(object_path: str) -> str | None:
        obj = _old_obj(object_path)
        if obj is None:
            return None
        try:
            # Prefer Qiskit's structured deprecation decorators over free-text docstrings.
            for dec in getattr(obj, "decorators", None) or []:
                value = str(getattr(dec, "value", ""))
                alias = re.search(r"new_alias\s*=\s*['\"]([A-Za-z_][\w.]*)['\"]", value)
                if alias:
                    return f"use {alias.group(1)}"
                msg = re.search(r"additional_msg\s*=\s*['\"](.*?)['\"]", value)
                if msg:
                    return msg.group(1)
            return obj.docstring.value if getattr(obj, "docstring", None) else None
        except Exception:
            return None

    removed_in = new_pkg_spec.partition("==")[2] or "2.0"
    candidates = _candidates_from_breakages(
        expanded, removed_in, get_message, public_only=public_only
    )
    return candidates[:limit] if limit else candidates


# --------------------------------------------------------------------------- #
# Stage 1' — cross-package name-matching (introspection, NOT Griffe)
#
# Griffe diffs ONE package across versions, so it is blind to a symbol that moved to a
# DIFFERENT package (`qiskit.aqua.algorithms.VQE` -> `qiskit_algorithms.VQE`). This mode
# instead enumerates the OLD package's API surface (introspected inside a throwaway
# container, since legacy packages won't install on the host/target) and name-matches each
# symbol against an index of the ecosystem packages on the TARGET image. It catches
# *moved-same-name* symbols (VQE, COBYLA, Grover, the optimizers); pure renames
# (QSVM -> QSVC) aren't name-matchable and stay curated. Verify/promote is shared with the
# Griffe path: the old import is absent on the target and the ecosystem replacement imports.
# --------------------------------------------------------------------------- #

# Introspector run INSIDE the old-package container: walk `root`, emit {name: most-public path}
# for every public class/function DEFINED in `root` (re-exports collapse to the shortest path).
_ENUM_SRC = """\
import importlib, inspect, json, pkgutil, sys, warnings
warnings.simplefilter("ignore")
root = sys.argv[1]
out = {}
try:
    pkg = importlib.import_module(root)
except Exception as e:
    print("ENUMJSON" + json.dumps({"__error__": repr(e)})); sys.exit(0)
mods = [pkg]
if hasattr(pkg, "__path__"):
    for mi in pkgutil.walk_packages(pkg.__path__, root + "."):
        try:
            mods.append(importlib.import_module(mi.name))
        except Exception:
            continue
for m in mods:
    for name, obj in inspect.getmembers(m, lambda o: inspect.isclass(o) or inspect.isfunction(o)):
        if name.startswith("_"):
            continue
        defmod = getattr(obj, "__module__", "") or ""
        if not defmod.startswith(root):
            continue
        access = m.__name__ + "." + name
        if name not in out or len(access) < len(out[name]):
            out[name] = access
print("ENUMJSON" + json.dumps(out))
"""

# Where to look for a moved symbol's new home, in priority order (first match wins).
DEFAULT_ECOSYSTEM_NAMESPACES = [
    "qiskit_algorithms",
    "qiskit_algorithms.optimizers",
    "qiskit_machine_learning.algorithms",
    "qiskit_machine_learning.kernels",
    "qiskit_machine_learning.circuit.library",
    "qiskit.circuit.library",
    "qiskit.quantum_info",
    "qiskit_nature.second_q.drivers",
    "qiskit_nature.second_q.circuit.library",
    "qiskit_nature.second_q.mappers",
    "qiskit_nature.second_q.algorithms",
    "qiskit_finance.applications",
    "qiskit_finance.circuit.library",
    "qiskit_optimization",
    "qiskit_optimization.algorithms",
    "qiskit_optimization.applications",
]

# Old-package error/base/utility names that name-match the ecosystem but aren't real migrations.
_CROSS_MATCH_SKIP = {
    "AquaError",
    "MissingOptionalLibraryError",
    "QiskitLogDomains",
    "QuantumAlgorithm",
    "Pluggable",
    "QuantumInstance",
    "build_logging_config",
    "get_logging_level",
    "get_qiskit_aqua_logging",
    "set_qiskit_aqua_logging",
}


def enumerate_old_symbols(
    install_spec: str, root_module: str, *, image: str = "python:3.9-slim", timeout: int = 600
) -> dict[str, str]:
    """Introspect an OLD package's public API inside a throwaway container.

    Legacy packages (e.g. ``qiskit-aqua==0.9.0``) won't install on the host or the modern
    target image, so we ``pip install`` them in a clean ``image`` and walk the package there.
    Returns ``{name: most-public dotted path}``. Touches Docker; not unit-tested (the pure
    matching in :func:`cross_package_candidates` is).
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "_enum.py").write_text(_ENUM_SRC, encoding="utf-8")
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp}:/work:ro",
            image,
            "sh",
            "-c",
            f"pip install --no-cache-dir -q {install_spec} >/dev/null 2>&1 "
            f"&& python /work/_enum.py {root_module}",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    line = next((ln for ln in (proc.stdout or "").splitlines() if ln.startswith("ENUMJSON")), None)
    if not line:
        raise RuntimeError(f"enumeration failed for {install_spec}: {(proc.stderr or '')[-500:]}")
    data = json.loads(line[len("ENUMJSON") :])
    if "__error__" in data:
        raise RuntimeError(f"could not import {root_module} in {image}: {data['__error__']}")
    return data


def build_ecosystem_index(namespaces: list[str], sandbox: Sandbox) -> dict[str, str]:
    """Index the target image's ecosystem API: ``{name: importable path}``.

    Introspects each namespace on the TARGET sandbox image (highest-priority namespace wins),
    giving the destinations a moved symbol can land in. Touches Docker; not unit-tested.
    """
    script = (
        "import json, importlib\n"
        f"namespaces = {list(namespaces)!r}\n"
        "idx = {}\n"
        "for ns in namespaces:\n"
        "    try:\n"
        "        m = importlib.import_module(ns)\n"
        "    except Exception:\n"
        "        continue\n"
        "    for n in dir(m):\n"
        "        if n.startswith('_'):\n"
        "            continue\n"
        "        obj = getattr(m, n, None)\n"
        "        if isinstance(obj, type) or callable(obj):\n"
        "            idx.setdefault(n, ns + '.' + n)\n"
        "print('ECOIDX' + json.dumps(idx))\n"
    )
    report = sandbox.run(script, warnings_as_errors=False, max_capture=2_000_000)
    line = next((ln for ln in (report.stdout or "").splitlines() if ln.startswith("ECOIDX")), None)
    if not line:
        raise RuntimeError(
            f"ecosystem index failed: {(report.stderr or report.stdout or '')[-500:]}"
        )
    return json.loads(line[len("ECOIDX") :])


def cross_package_candidates(
    old_symbols: dict[str, str],
    eco_index: dict[str, str],
    *,
    skip_names: set[str] | None = None,
    removed_in: str = "1.0",
    status: str = "moved",
) -> list[dict]:
    """Pure: name-match an old package's symbols against an ecosystem index → candidates.

    For each ``{name: old_path}``, when ``name`` is present in ``eco_index`` (and not a skipped
    utility/base name) emit ``{symbol: old_path, replacement: eco_index[name], status,
    removed_in}``. Catches *moved-same-name* symbols only — a rename won't name-match and needs
    curation. Downstream verification still confirms old-absent + replacement-imports, so a
    coincidental name collision that doesn't actually resolve is rejected, not promoted.
    """
    skip = skip_names or set()
    out: list[dict] = []
    for name in sorted(old_symbols):
        if name in skip or name not in eco_index:
            continue
        out.append(
            {
                "symbol": old_symbols[name],
                "replacement": eco_index[name],
                "status": status,
                "removed_in": removed_in,
            }
        )
    return out


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
    skip_symbols: set[str] | None = None,
    on_verified=None,
    on_progress=None,
    method: str = "Griffe API-diff",
) -> HarvestReport:
    """Stage 3+4: execution-verify candidates one-by-one and promote the survivors.

    **Streaming + resumable** so a killed run never loses everything: each verified removal is
    upserted to ``store`` and handed to ``on_verified(record)`` the instant it's confirmed,
    and ``skip_symbols`` (e.g. symbols already in a partial output file) are passed over so a
    re-run continues where the last left off. ``on_progress(i, total, verified)`` fires each
    step (wire it to a log line).

    A candidate is promoted when the sandbox confirms the old symbol is genuinely absent
    (``old_absent``); the replacement is attached only if it *also* verified, else dropped —
    a garbage replacement hypothesis never discards a valid removal. Pure w.r.t. Griffe.
    """
    skip = skip_symbols or set()
    records: list[DeprecationRecord] = []
    verdicts: list[RecordVerdict] = []
    promoted = 0
    total = len(candidates)
    for i, cand in enumerate(candidates, 1):
        if cand["symbol"] not in skip:
            verdict = verify_candidate(cand["symbol"], cand.get("replacement"), sandbox)
            verdicts.append(verdict)
            if verdict.old_absent:
                repl = verdict.replacement if verdict.replacement_ok else None
                status = cand.get("status", "removed")
                rec = DeprecationRecord(
                    symbol=cand["symbol"],
                    status=status,
                    removed_in=cand.get("removed_in"),
                    replacement=repl,
                    note=(
                        f"Auto-harvested via {method}; sandbox-verified {status}"
                        f"{f' (use {repl})' if repl else ''}."
                    ),
                    source=source,
                )
                records.append(rec)
                if store is not None:
                    promoted += store.upsert_many([rec])
                if on_verified is not None:
                    on_verified(rec)
        if on_progress is not None:
            on_progress(i, total, len(records))
    return HarvestReport(
        mined=total,
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


def harvest_cross_package(
    install_spec: str,
    root_module: str,
    sandbox: Sandbox,
    *,
    store: DeprecationStore | None = None,
    namespaces: list[str] | None = None,
    skip_names: set[str] | None = None,
    removed_in: str = "1.0",
    enum_image: str = "python:3.9-slim",
    limit: int | None = None,
) -> HarvestReport:
    """Full cross-package pipeline: enumerate an old package (container) → index the ecosystem
    (target image) → name-match → verify → promote. The complement to :func:`harvest` for
    symbols that moved to a *different* package (e.g. ``qiskit-aqua`` → the standalone ecosystem).
    """
    old_symbols = enumerate_old_symbols(install_spec, root_module, image=enum_image)
    eco_index = build_ecosystem_index(namespaces or DEFAULT_ECOSYSTEM_NAMESPACES, sandbox)
    candidates = cross_package_candidates(
        old_symbols,
        eco_index,
        skip_names=skip_names if skip_names is not None else _CROSS_MATCH_SKIP,
        removed_in=removed_in,
    )
    if limit:
        candidates = candidates[:limit]
    return harvest_candidates(candidates, sandbox, store=store, method="cross-package name-match")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harvest sandbox-verified deprecation records.")
    parser.add_argument(
        "--mode",
        choices=["griffe", "cross-package"],
        default="griffe",
        help="griffe: same-package API diff (removals). "
        "cross-package: name-match a legacy package's symbols to the ecosystem (moves).",
    )
    parser.add_argument(
        "--old",
        required=True,
        help="griffe: old pip spec (qiskit-terra==0.46.3). cross-package: legacy install spec "
        "to introspect (qiskit-aqua==0.9.0).",
    )
    parser.add_argument("--new", help="griffe mode: new pip spec, e.g. qiskit==2.0.2")
    parser.add_argument(
        "--old-root", help="cross-package mode: module to introspect, e.g. qiskit.aqua"
    )
    parser.add_argument(
        "--namespaces",
        help="cross-package mode: comma-separated ecosystem namespaces to match against "
        "(default: the built-in Qiskit ecosystem set).",
    )
    parser.add_argument(
        "--enum-image",
        default="python:3.9-slim",
        help="cross-package mode: image to install+introspect the legacy package in.",
    )
    parser.add_argument(
        "--removed-in", default="1.0", help="cross-package mode: version to record as removed_in."
    )
    parser.add_argument("--sandbox-backend", default="docker", help="local | docker")
    parser.add_argument("--db", default="app.db")
    parser.add_argument("--cache-dir", default="build/harvest_cache")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--promote", action="store_true", help="Write verified records to --db.")
    parser.add_argument(
        "--out", default=None, help="Write verified records to this JSON file (seed schema)."
    )
    args = parser.parse_args(argv)

    sandbox = get_sandbox(args.sandbox_backend)
    if sandbox is None:
        print(f"No sandbox for backend {args.sandbox_backend!r}; verification needs one.")
        return 2

    store = DeprecationStore(args.db) if args.promote else None
    if store is not None:
        store.create()

    if args.mode == "cross-package":
        if not args.old_root:
            parser.error("--old-root is required for --mode cross-package")
        namespaces = (
            [s.strip() for s in args.namespaces.split(",") if s.strip()]
            if args.namespaces
            else DEFAULT_ECOSYSTEM_NAMESPACES
        )
        old_symbols = enumerate_old_symbols(args.old, args.old_root, image=args.enum_image)
        eco_index = build_ecosystem_index(namespaces, sandbox)
        candidates = cross_package_candidates(old_symbols, eco_index, removed_in=args.removed_in)
        if args.limit:
            candidates = candidates[: args.limit]
        method = "cross-package name-match"
        print(
            f"Enumerated {len(old_symbols)} {args.old_root} symbols; ecosystem index "
            f"{len(eco_index)}; {len(candidates)} name-matched candidates.",
            flush=True,
        )
    else:
        if not args.new:
            parser.error("--new is required for --mode griffe")
        candidates = mine_candidates(args.old, args.new, cache_dir=args.cache_dir, limit=args.limit)
        method = "Griffe API-diff"

    # Resume: load any partial --out file and skip symbols already verified, so a re-run
    # continues a killed run instead of starting over.
    out_path = Path(args.out) if args.out else None
    saved: list[dict] = []
    if out_path and out_path.exists():
        try:
            saved = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            saved = []
    skip = {r["symbol"] for r in saved}
    # Count only the candidates already covered — `skip` is the whole --out file (which can be
    # far larger than this run's candidate set when appending to an existing catalog).
    already = sum(1 for c in candidates if c["symbol"] in skip)
    print(
        f"Mined {len(candidates)} candidates; {already} already done, "
        f"{len(candidates) - already} to verify.",
        flush=True,
    )

    def on_verified(rec: DeprecationRecord) -> None:
        # Persist after every confirmed record so a kill leaves a durable partial result.
        saved.append(asdict(rec))
        if out_path:
            out_path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")

    def on_progress(i: int, total: int, verified: int) -> None:
        if i % 25 == 0 or i == total:
            print(f"  [{i}/{total}] verified-removed total: {len(saved)}", flush=True)

    report = harvest_candidates(
        candidates,
        sandbox,
        store=store,
        skip_symbols=skip,
        on_verified=on_verified,
        on_progress=on_progress,
        method=method,
    )
    print(
        f"Done. New this run: {report.verified} verified-removed; total persisted: {len(saved)}.",
        flush=True,
    )
    if args.promote:
        print(f"Promoted {report.promoted} new records to {args.db} (source={_SOURCE_VERIFIED}).")
    if not args.promote and not out_path:
        print("Dry run (pass --promote and/or --out to persist).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
