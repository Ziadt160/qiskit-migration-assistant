"""Attach replacements to detection-only harvested records — sourced + sandbox-verified.

The harvester confirms *what* was removed but rarely *what replaces it* (0/1,153). The
community `flake8-qiskit-migration` plugin ships a structured map of removed import path ->
guidance message that states the replacement (e.g. "replace `qiskit.algorithms` with
`qiskit_algorithms`"). We mine it, construct a *precise* replacement — module renames are
applied member-wise, so `qiskit.algorithms.VQE` -> `qiskit_algorithms.VQE` — and
sandbox-verify the replacement imports on the target before attaching it. A wrong hint
never lands in the table: the same "verify, don't trust" gate as the harvester itself.

`flake8-qiskit-migration` is an optional dependency (the `[harvest]` extra), imported lazily.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from src.migration.sandbox import Sandbox, get_sandbox
from src.migration.verify_record import make_probe

# "replace OLD with NEW" — a rename we can apply member-wise to a moved module's contents.
_RENAME = re.compile(r"replace\s+`?([\w.]+)`?\s+with\s+`?([\w.]+)`?", re.I)
# Direct replacement statements — trusted only for an exact key match (describe the module).
_DIRECT = [
    re.compile(r"replace with\s+`?([\w.]+)`?", re.I),
    re.compile(r"moved to\s+`?([\w.]+)`?", re.I),
    re.compile(r"use(?:\s+alternative)?\s+`?([\w.]+)`?\s+instead", re.I),
    re.compile(r"use\s+alternative\s+`?([\w.]+)`?", re.I),
]


def load_flake8_map() -> dict[str, str]:
    """Merge flake8-qiskit-migration's removed-path -> guidance-message dicts (1.0 + 2.0)."""
    import flake8_qiskit_migration.deprecated_paths as p1
    import flake8_qiskit_migration.deprecated_paths_v2 as p2

    mapping: dict[str, str] = {}
    for mod in (p1, p2):
        for value in vars(mod).values():
            if isinstance(value, dict):
                for old, msg in value.items():
                    if isinstance(old, str) and isinstance(msg, str):
                        mapping.setdefault(old, msg)
    return mapping


def propose_replacement(symbol: str, mapping: dict[str, str]) -> str | None:
    """Best-effort replacement for `symbol` from the flake8 map.

    Matches the longest map key equal to or a dotted prefix of `symbol`. A "replace OLD with
    NEW" rename is applied member-wise (members of a moved module map to the new module); a
    direct replacement ("replace with / moved to / use X") is trusted only for an exact key
    match, since it describes the module, not its members.
    """
    key = None
    for candidate in mapping:
        if symbol == candidate or symbol.startswith(candidate + "."):
            if key is None or len(candidate) > len(key):
                key = candidate
    if key is None:
        return None
    message = mapping[key]
    rename = _RENAME.search(message)
    if rename:
        old, new = rename.group(1), rename.group(2)
        if symbol == old or symbol.startswith(old + "."):
            return symbol.replace(old, new, 1)
        return new
    if symbol == key:
        for pattern in _DIRECT:
            m = pattern.search(message)
            if m:
                return m.group(1)
    return None


def enrich_records(
    records: list[dict], sandbox: Sandbox, mapping: dict[str, str], on_progress=None
) -> dict:
    """Attach a flake8-sourced replacement to each record lacking one, iff it imports on the
    target. Mutates `records` in place; returns ``{proposed, verified}``."""
    proposed = verified = 0
    for i, rec in enumerate(records, 1):
        if not rec.get("replacement"):
            candidate = propose_replacement(rec["symbol"], mapping)
            if candidate:
                proposed += 1
                if sandbox.run(make_probe(candidate)).ok:  # replacement imports clean on target
                    rec["replacement"] = candidate
                    rec["note"] = f"Auto-harvested; sandbox-verified removed (use {candidate})."
                    verified += 1
        if on_progress is not None:
            on_progress(i, len(records), verified)
    return {"proposed": proposed, "verified": verified}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attach + verify replacements via the flake8 map.")
    parser.add_argument(
        "--in", dest="path", default="src/migration/data/harvested_deprecations.json"
    )
    parser.add_argument("--sandbox-backend", default="docker", help="local | docker")
    args = parser.parse_args(argv)

    sandbox = get_sandbox(args.sandbox_backend)
    if sandbox is None:
        print(f"No sandbox for backend {args.sandbox_backend!r}; verification needs one.")
        return 2
    path = Path(args.path)
    records = json.loads(path.read_text(encoding="utf-8"))
    mapping = load_flake8_map()
    print(f"Enriching {len(records)} records against {len(mapping)} flake8 entries...", flush=True)

    def on_progress(i: int, total: int, verified: int) -> None:
        if i % 50 == 0 or i == total:
            path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")  # checkpoint
            print(f"  [{i}/{total}] replacements verified: {verified}", flush=True)

    stats = enrich_records(records, sandbox, mapping, on_progress=on_progress)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    have = sum(1 for r in records if r.get("replacement"))
    print(
        f"Proposed {stats['proposed']}, sandbox-verified {stats['verified']}; "
        f"{have}/{len(records)} records now carry a replacement."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
