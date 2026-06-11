"""Evaluation metrics for the migration assistant — two tiers.

ISOLATED (component-level):
  * deprecation-detection recall — does static analysis flag the APIs that changed?
  * reference cleanliness — do the gold references pass static validation?
  * retrieval recall — does hybrid retrieval surface docs about each changed API?
  * executable correctness of a code blob in the sandbox.

END-TO-END (full pipeline):
  * run the real transformer per case and score the produced code
    (static validity, deprecated-API removal, executable correctness).

The first two isolated metrics are offline/CI-gateable; the rest need the live
index / GPU / Gemini and are opt-in via `run_eval` flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.migration.deprecations import DeprecationStore
from src.migration.symbols import extract_symbols
from src.migration.validate_output import validate_output


@dataclass
class DetectionResult:
    recall: float
    total_expected: int
    total_found: int
    per_case: list[dict] = field(default_factory=list)


@dataclass
class CleanlinessResult:
    pass_rate: float
    failures: list[dict] = field(default_factory=list)


@dataclass
class ExecutableResult:
    pass_rate: float
    failures: list[dict] = field(default_factory=list)


@dataclass
class RetrievalResult:
    recall: float  # fraction of expected APIs with supporting context retrieved
    context_hit_rate: float  # fraction of cases with >= 1 supported expected API
    per_case: list[dict] = field(default_factory=list)


@dataclass
class EquivalenceEvalResult:
    """Behavioral-equivalence over the golden set: original-on-old vs reference-on-new."""

    n: int
    equivalent: int  # cases the check confirmed behaviorally equivalent
    undetermined: int  # a side failed to run / nothing comparable lined up
    not_equivalent: int  # a comparable circuit diverged (a real regression signal)
    per_case: list[dict] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """Fraction of *determinable* cases that were equivalent (undetermined excluded)."""
        determinable = self.equivalent + self.not_equivalent
        return self.equivalent / determinable if determinable else 1.0


@dataclass
class E2EResult:
    n: int
    validation_pass_rate: float  # ported code passes static validation
    change_applied_rate: float  # expected deprecated APIs no longer present in output
    executable_pass_rate: float | None  # output runs under target Qiskit (None if no sandbox)
    per_case: list[dict] = field(default_factory=list)


def _expected_matched(expected: str, deps) -> bool:
    last = expected.rsplit(".", 1)[-1]
    symbols = {d.symbol for d in deps}
    segments = {d.last_segment for d in deps}
    return expected in symbols or last in segments


def evaluate_detection(cases: list[dict], store: DeprecationStore) -> DetectionResult:
    total_expected = total_found = 0
    per_case: list[dict] = []
    for case in cases:
        symbols = extract_symbols(case["old_code"])
        deps = store.lookup(symbols.lookup_keys)
        expected = case["expected_apis_changed"]
        found = [e for e in expected if _expected_matched(e, deps)]
        missed = [e for e in expected if e not in found]
        per_case.append({"id": case["id"], "expected": expected, "found": found, "missed": missed})
        total_expected += len(expected)
        total_found += len(found)
    recall = total_found / total_expected if total_expected else 1.0
    return DetectionResult(recall, total_expected, total_found, per_case)


def evaluate_reference_cleanliness(
    cases: list[dict], store: DeprecationStore, target_version: str
) -> CleanlinessResult:
    failures: list[dict] = []
    for case in cases:
        report = validate_output(case["reference_ported_code"], store, target_version)
        if not report.passed:
            failures.append(
                {
                    "id": case["id"],
                    "syntax_ok": report.syntax_ok,
                    "deprecated_symbols": report.deprecated_symbols,
                    "errors": report.errors,
                }
            )
    pass_rate = (len(cases) - len(failures)) / len(cases) if cases else 1.0
    return CleanlinessResult(pass_rate, failures)


def evaluate_executable_correctness(
    cases: list[dict], sandbox, code_key: str = "reference_ported_code"
) -> ExecutableResult:
    """Run each case's code through the sandbox vs the target Qiskit (headline metric).

    `sandbox` is anything with `.run(code) -> SandboxReport` (see `src.migration.sandbox`).
    With `code_key="reference_ported_code"` this checks the gold references run clean;
    point it at produced output to score a live migration run.
    """
    failures: list[dict] = []
    for case in cases:
        report = sandbox.run(case[code_key])
        if not report.ok:
            failures.append(
                {
                    "id": case["id"],
                    "error_type": report.error_type,
                    "timed_out": report.timed_out,
                    "stderr": report.stderr[:200],
                }
            )
    pass_rate = (len(cases) - len(failures)) / len(cases) if cases else 1.0
    return ExecutableResult(pass_rate, failures)


def evaluate_behavioral_equivalence(
    cases: list[dict], old_sandbox, new_sandbox
) -> EquivalenceEvalResult:
    """Behavioral-equivalence eval: run each case's ORIGINAL code on the legacy Qiskit and
    its gold REFERENCE on the target, then compare prepared statevectors (see
    `src.migration.equivalence`). `old_sandbox`/`new_sandbox` are `Sandbox` instances
    targeting old/new Qiskit respectively (typically two Docker images).
    """
    from src.migration.equivalence import check_equivalence

    equivalent = undetermined = not_equivalent = 0
    per_case: list[dict] = []
    for case in cases:
        report = check_equivalence(
            case["old_code"], case["reference_ported_code"], old_sandbox, new_sandbox
        )
        if report.equivalent is True:
            equivalent += 1
            verdict = "equivalent"
        elif report.equivalent is False:
            not_equivalent += 1
            verdict = "NOT-equivalent"
        else:
            undetermined += 1
            verdict = "undetermined"
        per_case.append(
            {
                "id": case["id"],
                "verdict": verdict,
                "note": report.note,
                "comparisons": [c.model_dump() for c in report.comparisons],
            }
        )
    return EquivalenceEvalResult(
        n=len(cases),
        equivalent=equivalent,
        undetermined=undetermined,
        not_equivalent=not_equivalent,
        per_case=per_case,
    )


def _context_targets(expected: str, store: DeprecationStore) -> set[str]:
    """Strings whose presence in retrieved text means relevant context was found:
    the changed API's last segment plus its replacement's last segment."""
    last = expected.rsplit(".", 1)[-1]
    targets = {last}
    for rec in store.lookup({expected, last}):
        if rec.replacement:
            targets.add(rec.replacement.rsplit(".", 1)[-1])
    return {t for t in targets if len(t) > 1}


def evaluate_retrieval(
    cases: list[dict], migration_retriever, store: DeprecationStore
) -> RetrievalResult:
    """ISOLATED retrieval eval: does hybrid retrieval surface docs about each changed
    API (or its replacement)? Uses the live index; no LLM involved.

    `migration_retriever` is anything with `.retrieve(symbols, deps) -> list[chunk dict]`.
    """
    total_expected = total_found = cases_with_hit = 0
    per_case: list[dict] = []
    for case in cases:
        symbols = extract_symbols(case["old_code"])
        deps = store.lookup(symbols.lookup_keys)
        chunks = migration_retriever.retrieve(symbols, deps)
        blob = "\n".join(c.get("text", "") for c in chunks)

        found = [
            exp
            for exp in case["expected_apis_changed"]
            if any(t in blob for t in _context_targets(exp, store))
        ]
        total_expected += len(case["expected_apis_changed"])
        total_found += len(found)
        cases_with_hit += 1 if found else 0
        per_case.append(
            {
                "id": case["id"],
                "found": found,
                "missed": [e for e in case["expected_apis_changed"] if e not in found],
                "n_chunks": len(chunks),
            }
        )
    recall = total_found / total_expected if total_expected else 1.0
    hit_rate = cases_with_hit / len(cases) if cases else 1.0
    return RetrievalResult(recall, hit_rate, per_case)


def evaluate_end_to_end(cases: list[dict], transformer) -> E2EResult:
    """END-TO-END eval: run the full transformer per case and score the output.

    `transformer` is anything with `.transform(code, source_version) -> MigrationResult`.
    """
    n = len(cases)
    valid = exec_ran = exec_ok = 0
    change_num = change_den = 0
    per_case: list[dict] = []

    for case in cases:
        try:
            result = transformer.transform(
                case["old_code"], source_version=case.get("source_version")
            )
        except Exception as e:  # noqa: BLE001 - record per-case failure, keep evaluating
            change_den += len(case["expected_apis_changed"])
            per_case.append(
                {
                    "id": case["id"],
                    "valid": False,
                    "changes_applied": f"0/{len(case['expected_apis_changed'])}",
                    "executable": None,
                    "repairs": 0,
                    "error": str(e)[:160],
                }
            )
            continue

        is_valid = bool(result.validation and result.validation.passed)
        valid += 1 if is_valid else 0

        try:
            ported_keys = extract_symbols(result.ported_code).lookup_keys
        except SyntaxError:
            ported_keys = set()
        applied = [
            exp
            for exp in case["expected_apis_changed"]
            if exp not in ported_keys and exp.rsplit(".", 1)[-1] not in ported_keys
        ]
        change_num += len(applied)
        change_den += len(case["expected_apis_changed"])

        executable: bool | None = None
        if result.execution is not None:
            exec_ran += 1
            executable = result.execution.ok
            exec_ok += 1 if executable else 0

        per_case.append(
            {
                "id": case["id"],
                "valid": is_valid,
                "changes_applied": f"{len(applied)}/{len(case['expected_apis_changed'])}",
                "executable": executable,
                "repairs": getattr(result, "repair_attempts", 0),
            }
        )

    return E2EResult(
        n=n,
        validation_pass_rate=valid / n if n else 1.0,
        change_applied_rate=change_num / change_den if change_den else 1.0,
        executable_pass_rate=(exec_ok / exec_ran) if exec_ran else None,
        per_case=per_case,
    )
