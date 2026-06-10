"""Run the migration evaluation and gate on thresholds (used by CI).

    python -m src.eval.run_eval               # offline gate (detection + cleanliness)
    python -m src.eval.run_eval --seed-only    # skip corpus parse, curated seed only
    python -m src.eval.run_eval --min-recall 0.9

Exits non-zero if any gate fails.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from src.config import get_settings
from src.eval.dataset.golden import load_golden
from src.eval.metrics import evaluate_detection, evaluate_reference_cleanliness
from src.migration.deprecations import (
    DeprecationStore,
    build_deprecation_store,
    load_harvested_records,
    load_seed_records,
)


def _ensure_store(db_path: str, docs_dir: str, seed_only: bool) -> DeprecationStore:
    store = DeprecationStore(db_path)
    try:
        count = store.count()
    except sqlite3.OperationalError:
        count = 0
    if count == 0:
        if seed_only or not Path(docs_dir).is_dir():
            store.create()
            store.upsert_many(load_seed_records())
            store.upsert_many(load_harvested_records())  # auto-grown sandbox-verified tier
        else:
            build_deprecation_store(docs_dir, db_path)
    return store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate and gate the migration assistant.")
    parser.add_argument("--db", default="app.db")
    parser.add_argument("--docs-dir", default="documentation/docs")
    parser.add_argument("--seed-only", action="store_true", help="Use curated seed only.")
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument(
        "--executable",
        action="store_true",
        help="Also run reference code through the sandbox (needs target Qiskit installed).",
    )
    parser.add_argument("--sandbox-backend", default="local", help="local | docker")
    parser.add_argument(
        "--retrieval",
        action="store_true",
        help="Isolated retrieval eval against the live index (needs ingestion done).",
    )
    parser.add_argument(
        "--e2e",
        action="store_true",
        help="End-to-end eval: run the full transformer per case (uses Gemini quota).",
    )
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Held-out coverage probe: report detection gap on APIs NOT in the seed "
        "(diagnostic only — never affects the gate).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    cases = load_golden()
    store = _ensure_store(args.db, args.docs_dir, args.seed_only)

    detection = evaluate_detection(cases, store)
    cleanliness = evaluate_reference_cleanliness(cases, store, settings.qiskit_target_version)

    print(f"Cases: {len(cases)}")
    print(
        f"Deprecation-detection recall: {detection.recall:.3f} "
        f"({detection.total_found}/{detection.total_expected})"
    )
    for case in detection.per_case:
        if case["missed"]:
            print(f"  MISS [{case['id']}]: {case['missed']}")
    print(f"Reference cleanliness pass-rate: {cleanliness.pass_rate:.3f}")
    for failure in cleanliness.failures:
        print(f"  DIRTY [{failure['id']}]: {failure}")

    gate_pass = detection.recall >= args.min_recall and cleanliness.pass_rate == 1.0

    if args.executable:
        from src.eval.metrics import evaluate_executable_correctness
        from src.migration.sandbox import get_sandbox

        sandbox = get_sandbox(args.sandbox_backend)
        if sandbox is None:
            print("Executable check requested but sandbox backend is 'none'.")
            gate_pass = False
        else:
            executable = evaluate_executable_correctness(cases, sandbox)
            print(f"Executable-correctness pass-rate: {executable.pass_rate:.3f}")
            for failure in executable.failures:
                print(f"  FAIL [{failure['id']}]: {failure['error_type']}")
            gate_pass = gate_pass and executable.pass_rate == 1.0

    if args.retrieval:
        from src.eval.metrics import evaluate_retrieval
        from src.migration.retrieval import MigrationRetriever

        ret = evaluate_retrieval(cases, MigrationRetriever.from_settings(), store)
        print(
            f"\n[ISOLATED] Retrieval recall: {ret.recall:.3f} | "
            f"context-hit-rate: {ret.context_hit_rate:.3f}"
        )
        for case in ret.per_case:
            if case["missed"]:
                print(f"  MISS [{case['id']}]: {case['missed']} (from {case['n_chunks']} chunks)")

    if args.e2e:
        from src.eval.metrics import evaluate_end_to_end
        from src.migration.transform import MigrationTransformer

        e2e = evaluate_end_to_end(cases, MigrationTransformer.from_settings(args.db))
        exec_rate = "n/a" if e2e.executable_pass_rate is None else f"{e2e.executable_pass_rate:.3f}"
        print(
            f"\n[E2E] validation={e2e.validation_pass_rate:.3f} | "
            f"changes-applied={e2e.change_applied_rate:.3f} | executable={exec_rate}"
        )
        for case in e2e.per_case:
            print(
                f"  [{case['id']}] valid={case['valid']} changes={case['changes_applied']} "
                f"exec={case['executable']} repairs={case['repairs']}"
            )

    if args.adversarial:
        _report_adversarial(store)

    print("\nGATE:", "PASS" if gate_pass else "FAIL")
    return 0 if gate_pass else 1


def _report_adversarial(store: DeprecationStore) -> None:
    """Held-out coverage probe: how much of the *real* migration surface does the
    current knowledge base actually detect?

    Diagnostic only — the adversarial set is curated from APIs deliberately absent
    from the seed, so misses are expected. We never gate on it; the missed symbols
    are the worklist for growing the seed (HANDOFF §12.1).
    """
    from src.eval.dataset.adversarial import load_adversarial

    cases = load_adversarial()
    result = evaluate_detection(cases, store)
    by_id = {c["id"]: c for c in cases}

    found = result.total_found
    expected = result.total_expected
    print(
        f"\n[ADVERSARIAL] Held-out detection coverage: {found}/{expected} "
        f"(gap: {expected - found} undetected) - recall {result.recall:.3f}, DIAGNOSTIC ONLY."
    )

    # Per-category breakdown so the gap is actionable, not just a number.
    buckets: dict[str, list[int]] = {}
    for case in result.per_case:
        category = by_id[case["id"]].get("category", "uncategorized")
        tallies = buckets.setdefault(category, [0, 0])
        tallies[0] += len(case["found"])
        tallies[1] += len(case["expected"])
    for category in sorted(buckets):
        hit, total = buckets[category]
        print(f"  {category:<22} {hit}/{total} detected")

    missed = [api for case in result.per_case for api in case["missed"]]
    if missed:
        print("  Seed-growth candidates (undetected APIs):")
        for api in missed:
            print(f"    - {api}")


if __name__ == "__main__":
    sys.exit(main())
