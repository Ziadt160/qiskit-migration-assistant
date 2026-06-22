# Contributing

Thanks for helping improve the Qiskit Migration Assistant. The project's whole value is being
**verified, not guessed** — so the most valuable contribution is a *sandbox-verified* migration
record. Here's how to help.

## Ways to contribute

- **Report a missed migration** — paste old Qiskit code the tool left deprecated, wrong, or
  un-migrated. Open an issue with the input, the output you got, and what you expected.
- **Add a verified migration record** — teach the tool a new old→new mapping (see below).
- **Improve docs or examples** — add a before/after case under [`examples/`](examples/).
- **Code** — pipeline, retrieval, sandbox, evaluation, or the harvester.

## Adding a migration record (the core contribution)

Deprecation knowledge lives in two tiers:

- `src/migration/data/known_deprecations.json` — the **curated seed** (hand-verified, high-value).
- `src/migration/data/harvested_deprecations.json` — **auto-harvested, sandbox-verified** records.

A record looks like:

```json
{
  "symbol": "qiskit.aqua.algorithms.QSVM",
  "status": "moved",
  "since_version": "0.20",
  "removed_in": "1.0",
  "replacement": "qiskit_machine_learning.algorithms.QSVC",
  "note": "Aqua QSVM moved to qiskit-machine-learning as QSVC (kernel-based).",
  "source": "curated-seed"
}
```

Rules for a good record:

1. **The replacement must actually import on Qiskit 2.x.** Don't guess a module path — verify it.
2. **Be specific.** When one removed module fans out to several packages (e.g. `qiskit.aqua`),
   give the concrete destination class, not just the package.
3. **Be honest about removals with no replacement** — set `replacement: null` rather than inventing one.

Two routes:

- **Manual (curated seed):** add the record to `known_deprecations.json`, then prove it:
  ```bash
  pip install -e ".[harvest]"
  python -m src.migration.harvest --help   # see verify/promote flags
  ```
- **Automated (harvester):** let the engine mine + verify it for you.
  - Same-package removals across versions (Griffe diff):
    ```bash
    python -m src.migration.harvest --old qiskit-terra==0.46.3 --new qiskit==2.0.2 \
        --sandbox-backend docker --out src/migration/data/harvested_deprecations.json
    ```
  - Cross-package moves (a legacy package → the ecosystem), e.g. `qiskit.aqua`:
    ```bash
    python -m src.migration.harvest --mode cross-package \
        --old qiskit-aqua==0.9.0 --old-root qiskit.aqua --sandbox-backend docker \
        --out src/migration/data/harvested_deprecations.json
    ```
  Both only promote records the sandbox confirms (old symbol absent on the target; replacement imports clean).

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before you open a PR

All of these must be green (CI checks them):

```bash
ruff check .           # lint
ruff format --check .  # formatting (separate from lint — both must pass)
pytest                 # unit tests
python -m src.eval.run_eval --db build/eval.db   # eval gate: detection recall + reference cleanliness
```

The **eval gate** is the project's contract — a change may not lower detection recall or reference
cleanliness. If you add records, the gate must stay green.

## PR guidelines

- One focused change per PR; explain *why*, not just *what*.
- Add a test for behavior changes; add an `examples/` case for a new migration class.
- Keep migrated code claims verified — never let the docs/marketing outrun what the tool proves.
