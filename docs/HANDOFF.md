# Qiskit Migration Assistant — Handoff / Runbook

**Single source of truth for resuming work.** Read this top-to-bottom and you can pick the project up cold.

---

## 0. Resume here — current state (last worked 2026-06-10)

**Branch:** `claude/keen-bhabha-8a286e` — **14 commits ahead of `main`, all pushed, open as PR #2** (https://github.com/Ziadt160/qiskit-migration-assistant/pull/2, *not* merged). `main` is still the pre-this-session state, so **merge PR #2 before sharing the repo publicly.** Working tree clean; **118 unit tests pass**; lint/format clean; golden gate green (**detection 32/32, cleanliness 1.000**).

**What this session built (the arc):**
1. **Held-out adversarial eval** (`run_eval --adversarial`, `src/eval/dataset/adversarial.py`) — exposed the golden eval's circularity; measures *real* coverage. Closed the first 0/13 gap.
2. **Autonomous knowledge harvester** (`src/migration/harvest.py` + `verify_record.py`) — Griffe API-diff `0.46→2.x` → sandbox-verify → promote. Grew the trusted table **28 → 1,181** records (~40×, every one execution-verified). Crash-safe (streaming/resume via `--out`).
3. **Replacements** (`src/migration/replacements.py`) — `flake8-qiskit-migration` map + migration-guide rename tables → **102 sandbox-verified replacements**.
4. **Deterministic safety-net** (`transform._apply_known_replacements`) — auto-repairs LLM misses using the table's verified replacements (adopts only if it then validates *and* runs). Fixed a real `ModuleNotFoundError` (`qiskit.algorithms` left unfixed by the 7B model).
5. **Verified live:** retrieval recall **1.000/1.000** (Pinecone+BGE+Cohere), full E2E correct (Ollama+RAG+Docker). UI screenshots in `docs/screenshots/` + reusable `scripts/capture_screenshots.py`.
6. **Fixed §12 weaknesses:** sandbox container cleanup + `--cap-drop=ALL`/`no-new-privileges`, removed import-time `logging.basicConfig` (4 modules), UI poll timeout 240s→930s.

**Bring the stack up (for the UI / live testing):**
```bash
cd <worktree>
cp "C:\Evoth Labs\RAGProject\.env" .env   # worktree lacks the gitignored .env (keys live in the main repo)
# Ollama must be serving (qwen2.5-coder:7b) + Docker up (image qiskit-migration-sandbox:latest)
LLM_PROVIDER=ollama QUEUE_EAGER=true SANDBOX_BACKEND=docker \
  python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8011   # -> http://localhost:8011/ui/
```
(Eager mode needs no Redis/worker; the `cache ... 6379` warning is harmless. §4 has the PowerShell variant + async mode.)

**Gotchas learned this session:**
- **Don't swap the LLM to a chatty model** (e.g. `gpt-oss:120b-cloud`): it migrates better but breaks the structured-output parser (`OUTPUT_PARSING_FAILURE`). `qwen2.5-coder:7b` follows the schema; the safety-net covers its misses.
- **Run the harvest crash-safe** (`harvest.py … --out file.json` streams + resumes) — a naive batch run got killed ~8 min in and lost everything; that's why streaming/resume exists.
- **The auto-harvested tier matches by FULL symbol only** (precision). Never give it last-segment matching — it collides by name with live APIs (`qiskit.pulse.cx` vs `QuantumCircuit.cx`) and breaks golden cleanliness (1.0→0.667). Curated seed keeps last-segment matching.
- Port **8000** on this machine is taken by an unrelated app — use **8011**.

**Next moves (prioritized):**
1. **Merge PR #2** → `main` reflects the work (do before sharing publicly).
2. **Cycle the adversarial loop:** `adversarial.py` is at 3/3 (gap 0 — the harvest already covers it). Refill with new held-out cases the harvest *doesn't* reach to keep the probe measuring a frontier.
3. **Grow coverage:** periodic `harvest --promote`; extend replacements with RAG-over-guides for the long tail (same sandbox-verify gate).
4. **Remaining §12 weaknesses:** `/metrics` auth + populate `user_id` (quick); Redis-backed rate limiter (bigger — own branch); make `mypy` blocking in CI (risky — needs a type-cleanup pass first).

---

## 1. What this is

A production-grade **RAG system that ports Qiskit code from older versions to the latest (2.x)**. You paste old Qiskit code (or point it at a file/folder) and it returns migrated code plus a cited, per-change rationale — grounded in the official deprecation/release-note record and validated by executing the result against `qiskit==2.x`.

**Status:** Working end-to-end, fully local & free. All milestones (M1–M7) + several extensions done. Unit suite passes (116); lint clean. **The deprecation table now auto-grows:** 30 curated seed records + **1,153 sandbox-verified auto-harvested** records (full-symbol matched, **102 with sandbox-verified replacements**). **Open-sourced** — public on GitHub at **https://github.com/Ziadt160/qiskit-migration-assistant** (branch `main`); MIT licensed; git author `Ziad <ziadt160@gmail.com>`. Push uses Windows Git Credential Manager (no `gh` CLI installed).

**Key results (golden eval — now 29 cases, covering every curated deprecation except `qiskit.pulse`):**
| Metric | Score | Tier |
|---|---|---|
| Deprecation-detection recall | 1.00 (32/32) | deterministic / offline (`--seed-only`) |
| Reference cleanliness | 1.00 (29/29) | deterministic / offline (`--seed-only`) |
| **References executable on Qiskit 2.2.3** | **13/14** | Docker; measured on the pre-graduation 14 — the newly-graduated cases are **not yet Docker-executed** |
| Held-out adversarial coverage | gap-probe (currently 3/3 frontier — harvested tier covers it; refill to keep probing) | deterministic / offline (`--adversarial`, non-gating) |
| Retrieval recall / context-hit | 1.00 / 1.00 | live, measured on original 8 |
| E2E validation / changes-applied (local qwen2.5-coder) | 1.00 / 1.00 | live, measured on original 8 |

The deterministic tiers (detection, cleanliness) are offline-reproducible by anyone — this is the **verifiable, publishable gate**. The executable row was measured at 14 and has **not** been re-run for the 13 graduated cases (some references need `qiskit-aer`/`qiskit-ibm-runtime`). The retrieval/E2E rows were measured on the original 8 and have **not** been re-run at 27 (needs Pinecone + Ollama up).

---

## 2. Architecture

```
old code ──▶ AST symbol extraction (symbols.py)
        ├──▶ deprecation lookup — authoritative table (deprecations.py, SQLite)
        ├──▶ hybrid retrieval (retrieval.py): Pinecone vector search + Cohere rerank
        ├──▶ LLM structured transform (generate.py): Ollama | Claude | Gemini → LLMTransformOutput
        ├──▶ static validation (validate_output.py): parses + no leaked deprecated APIs
        └──▶ sandbox execution + self-repair (sandbox.py): run vs qiskit==target, feed errors back
```

- **Embeddings:** local `BAAI/bge-large-en-v1.5` on GPU (default), 1024-d — matches the Pinecone index. Cohere is an alternate. Pluggable via `EMBEDDING_PROVIDER`.
- **Vector store:** managed **Pinecone** (index `qiskit-documentation`, dim 1024, cosine) — **12,163 vectors** ingested (current_api + release_notes + migration_guides + guides).
- **Rerank:** Cohere (query-time, low volume) or no-op.
- **LLM:** pluggable via `LLM_PROVIDER` — `ollama` (local/free, default in practice), `anthropic` (Claude), `gemini`.
- **Deprecation knowledge:** curated seed (`src/migration/data/known_deprecations.json`) + heuristic release-note parser → SQLite table (`app.db`).
- **Serving:** FastAPI (`/migrate`, `/jobs/{id}`, `/healthz`, `/readyz`, `/metrics`) + RQ worker + Streamlit UI.

---

## 3. Environment & prerequisites

- **Python 3.14** on the host (`C:\Python314`). NOTE: heavy compiled wheels lag — **`qiskit`/`qiskit-aer` have no 3.14 wheels**, so executable validation runs in **Docker (python 3.12)**, not on the host. Production Docker images pin **3.12**.
- **GPU:** NVIDIA RTX 4060 Ti (8 GB). torch `2.12.0+cu126` installed (CUDA). BGE-large (~1.3 GB) + a 7B Ollama model (~4.7 GB) ≈ 6 GB — fits, but if OOM set `EMBEDDING_DEVICE=cpu`.
- **Ollama** 0.21.0 installed (runs as a Windows service), with `qwen2.5-coder:7b` and `deepseek-r1:8b` pulled. Reachable at `http://localhost:11434`; verify with `curl http://localhost:11434/api/tags`.
- **Docker Desktop** — used only for the executable sandbox and the optional Redis/Postgres compose stack. **It tends to shut down**; when it's down, run the API in **eager mode** (no Redis/worker needed).
- **`.env`** (gitignored) holds the keys. Present: `PINECONE_API_KEY`, `PINECONE_INDEX`, `COHERE_API_KEY`, `HF_TOKEN`, `GEMINI_API_KEY`, `LANGCHAIN_*`. **Not present:** `ANTHROPIC_API_KEY`, `LLM_PROVIDER`. See `.env.example` for the full list.

### To make local/free the default, add these two lines to `.env`:
```
LLM_PROVIDER=ollama
EMBEDDING_PROVIDER=local
```
(Otherwise the config default `LLM_PROVIDER` is `gemini`; we've been overriding it per-command with `LLM_PROVIDER=ollama`.)

### Install
```powershell
pip install -e ".[dev]"
# already installed in this env: torch(cu126), sentence-transformers, cohere, pinecone,
# langchain-google-genai, langchain-ollama, anthropic, streamlit, fastapi, rq, sqlalchemy, ...
# For CUDA torch on a fresh machine: pip install torch --index-url https://download.pytorch.org/whl/cu126
```

---

## 4. Quick start — bring the running stack back up

> **Fastest verified path (used to confirm the live E2E this session):** `.claude/launch.json` defines a `web` config that runs the API on **:8011** with `LLM_PROVIDER=ollama`, `QUEUE_EAGER=true`, `SANDBOX_BACKEND=docker` (so retrieval + LLM + Docker sandbox all run inline). Open **http://localhost:8011/ui/**. A real `execute + Aer` migration completed end-to-end in ~105 s first run (model load), validation PASS, coverage 3/3, **sandbox `ok=True`** (ran on Qiskit 2.2.3).

The background processes do **not** persist across sessions. To resume the **frontend** manually (eager mode is simplest and needs no Redis/worker):

```powershell
cd "C:\Evoth Labs\RAGProject"

# 1. Start Ollama (stops when idle)
Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden

# 2. API in eager mode (runs migrations inline; no Redis/worker needed) + UI, detached
$env:LLM_PROVIDER='ollama'; $env:QUEUE_EAGER='true'; $env:MIGRATION_API_URL='http://localhost:8000'
Start-Process python -ArgumentList '-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port','8000' -WindowStyle Hidden -RedirectStandardOutput build\api.out.log -RedirectStandardError build\api.err.log
Start-Process python -ArgumentList '-m','streamlit','run','src/app/chatbot.py','--server.port','8501','--server.address','localhost','--server.headless','true' -WindowStyle Hidden -RedirectStandardOutput build\ui.out.log -RedirectStandardError build\ui.err.log
```
Then open the UI. **Two front ends, both talking to the same API:**
- **Bundled web app (primary):** the API serves it at **http://localhost:8000/ui/** (root `/` redirects there). No extra process — it's static files in `src/app/web/` mounted by `create_app()` via `StaticFiles`. Custom HTML/CSS/JS (no framework/CDN); brand assets (`assets/{logo,mark,favicon,hero}.png`) generated with Canva. So the Streamlit `Start-Process` line above is optional now.
- **Streamlit (alternative):** **http://localhost:8501**.

First request is ~30–40 s (model load), then ~15–30 s.

**Async (production) mode** instead of eager — needs Redis (Docker) + a worker:
```powershell
docker compose up -d redis            # publishes Redis on host :6380
$env:LLM_PROVIDER='ollama'; $env:REDIS_URL='redis://localhost:6380/0'
Start-Process python -ArgumentList '-m','src.worker.run' -WindowStyle Hidden    # RQ SimpleWorker (Windows: no os.fork)
# ...then start the API WITHOUT QUEUE_EAGER, same REDIS_URL.
```

**Stop everything:** kill the processes on ports 8000/8501 and the `src.worker.run` python process (see `Start-Process`/`Stop-Process` in `docs`), and `docker compose down` if you started compose.

---

## 5. How to run each mode (CLI)

All from the repo root. Prefix with `LLM_PROVIDER=ollama` (bash) / set `$env:LLM_PROVIDER='ollama'` (PowerShell) unless you put it in `.env`.

```bash
# Build the deprecation knowledge base from the docs corpus (offline, ~seconds)
python -m src.migration.cli --build-store

# Offline: just report deprecations in a snippet (NO network, NO LLM)
python -m src.migration.cli --offline --file old.py

# Migrate one snippet (full pipeline)
python -m src.migration.cli --file old.py
python -m src.migration.cli --code "from qiskit import execute" --json

# Migrate a FILE or DIRECTORY with per-file diffs (dry-run by default)
python -m src.migration.cli --path ./my_project --recursive
python -m src.migration.cli --path ./my_project --recursive --apply   # write changes to disk

# Re-index the corpus into Pinecone (only when switching embedding models; wipes index first)
python -m scripts.run_ingestion                    # migration-relevant doc types only
python -m scripts.run_ingestion --all              # entire corpus (large/expensive)

# Evaluation
python -m src.eval.run_eval --seed-only                                  # offline gate (CI)
python -m src.eval.run_eval --seed-only --adversarial                    # + held-out coverage-gap probe (non-gating)
python -m src.eval.run_eval --retrieval                                  # + live retrieval recall
LLM_PROVIDER=ollama python -m src.eval.run_eval --e2e                     # + full pipeline
python -m src.eval.run_eval --executable --sandbox-backend docker        # run gold refs vs qiskit
LLM_PROVIDER=ollama SANDBOX_BACKEND=docker python -m src.eval.run_eval --e2e   # execute generated code
```

`--path` only runs the LLM on files that actually use a deprecated API (cheap offline pre-filter); it skips junk dirs (`.venv`, `__pycache__`, `build`, `dist`, ...). Dry-run prints diffs; `--apply` writes them.

---

## 6. Provider matrix

| Concern | Default | Options | Switch |
|---|---|---|---|
| Embeddings | `local` (BGE on GPU) | local, cohere | `EMBEDDING_PROVIDER` |
| LLM (generation) | config says `gemini`; we use `ollama` | gemini, anthropic, ollama | `LLM_PROVIDER` |
| Rerank | Cohere if key + `RERANK_ENABLED` | else no-op | `RERANK_ENABLED` |
| Sandbox | `none` | none, local, docker | `SANDBOX_BACKEND` |

**LLM notes:** Gemini free tier on this account = `pro` 0/day, `flash` 20/day (exhausts fast). Claude needs a paid **Developer Platform** key (`ANTHROPIC_API_KEY`) — **Claude Max ≠ API credits**. **Ollama is the free/unlimited local choice** and scored best on the eval. Free cloud alternatives researched: Groq, OpenRouter, Cerebras.

---

## 7. Operational gotchas (read before debugging)

- **Switching embedding models requires re-ingestion.** Vectors from different models live in different spaces. `scripts/run_ingestion` **wipes the index first** (`indexer.clear()`). The index currently holds BGE vectors.
- **Ollama server stops when idle** → "connection refused" on 11434. Restart with `ollama serve`.
- **Docker Desktop shuts down** → Redis (`:6380`) + sandbox gone. Use **eager mode** for the API when Docker is down.
- **RQ on Windows:** the default worker uses `os.fork` (absent on Windows) — `src/worker/run.py` uses `SimpleWorker`. Per-job timeout is `JOB_TIMEOUT_S=900` (model load + LLM can be slow).
- **SQLAlchemy `create_all()` doesn't migrate schema.** If you change the `jobs` table, drop it first (`DROP TABLE jobs` in `app.db`) — production needs Alembic.
- **`.env` has spaces** around some `=` (`PINECONE_API_KEY =...`). pydantic-settings and Docker `env_file` both handle it fine.
- **Heuristic release-note parser has residual false positives.** The curated seed (`known_deprecations.json`) is authoritative and outranks parsed records (`_score`). `_CURRENT_ALLOWLIST` in `deprecations.py` prevents flagging current core APIs (e.g. `transpile`).
- **Small models wrap code in ```` ```python ```` fences** → `_strip_code_fences()` in `generate.py` cleans all providers' output.
- **`documentation/`** is a separate, large Qiskit-docs checkout (gitignored). Needed to build the store + ingest, not at request time.
- **Provider client libs are declared deps now.** `anthropic` + `langchain-ollama` were used but undeclared in `pyproject.toml` (only `langchain-google-genai`/Gemini was) → CI failed because `AnthropicGenerator.__init__` does `import anthropic` before the key check, raising `ModuleNotFoundError` instead of the expected `ValueError`. Fixed by declaring both as core deps. Lesson: any new provider's SDK must be a declared dep.
- **`ollama serve` exits 1 if Ollama already runs as a Windows service** (port 11434 in use) — that's fine, it's already serving. Check with `curl http://localhost:11434/api/tags`.
- **GitHub Actions logs need auth (`gh` not installed here).** To debug a CI failure, reproduce it locally in the CI image: `docker run --rm -v "C:\Evoth Labs\RAGProject:/app" -w /app python:3.12-slim sh -c "pip install -e '.[dev]' -q; pytest -q"`.
- **Web-UI diff is side-by-side** (ORIGINAL | MIGRATED grid, client-side LCS in `app.js`); palette softened (muted lavender/mint). Brand assets are Canva PNGs post-processed with Pillow (transparent export is plan-gated). `_WEB_DIR` in `api/main.py` resolves to `src/app/web`; Docker `COPY src ./src` bundles it.

---

## 8. File map

| Path | Role |
|---|---|
| `src/config.py` | All settings (`get_settings()`), `.env`-driven |
| `src/embeddings.py` | Pluggable embedders (`LocalBGEEmbedder`/`CohereEmbedder`) + rerankers; `get_embedder()`/`get_reranker()` |
| `src/ingestion/{loader,chunking,indexer}.py` | Load docs → version-aware metadata → chunk → embed → upsert to Pinecone |
| `src/migration/symbols.py` | AST extraction of Qiskit API symbols from code |
| `src/migration/deprecations.py` | Curated seed + release-note parser + SQLite store + lookup |
| `src/migration/retrieval.py` | Hybrid retrieval (symbol/replacement-targeted + semantic) + rerank |
| `src/generation/generate.py` | Gemini/Claude/Ollama generators + `get_generator()`; structured `LLMTransformOutput` |
| `src/migration/validate_input.py` / `validate_output.py` | Input guardrails / static output validation |
| `src/migration/sandbox.py` | `LocalSubprocessSandbox` + `DockerSandbox` (read-only, no-network, tmpfs) |
| `src/migration/verify_record.py` | Execution-verification gate: probe a candidate `{symbol→replacement}` in the sandbox (old must be genuinely absent, replacement must import) → `RecordVerdict`; `verify_candidate`/`verify_candidates`. Trust gate for auto-harvested deprecation records (§12.1). |
| `src/migration/harvest.py` | Autonomous harvester (Stage 1→4): Griffe API-diff → candidate removed symbols → sandbox-verify → promote as `source="sandbox-verified"`. `mine_candidates` (lazy Griffe, `[harvest]` extra), `harvest_candidates`, `harvest`, CLI `python -m src.migration.harvest`. |
| `src/migration/replacements.py` | Attach replacements to detection-only harvested records from two sources — the `flake8-qiskit-migration` import map (member-wise rename) + the migration-guide markdown rename tables (`load_guide_replacements`, URL-derived symbols) — each sandbox-verified before attaching. `propose_replacement`/`propose_from_guide`/`enrich_records`; CLI `python -m src.migration.replacements --docs-dir ...`. |
| `src/migration/transform.py` | Orchestrator: input→symbols→deps→retrieve→generate→validate→sandbox→self-repair; `find_deprecations()` (offline) |
| `src/migration/report.py` | `iter_python_files`, `unified_diff`, `compute_coverage` |
| `src/migration/cli.py` | CLI: `--offline`, `--file/--code`, `--path [--recursive --apply]`, `--build-store` |
| `src/migration/models.py` | Pydantic models: `LLMTransformOutput`, `MigrationResult`, `CoverageSummary`, `ValidationReport`, `SandboxReport` |
| `src/api/main.py` | FastAPI app (factory `create_app`) |
| `src/worker/{run,tasks,queue}.py` | RQ worker + job runner (cached transformer) + queue (eager fallback) |
| `src/db/db.py` | SQLAlchemy `JobStore` (SQLite/Postgres) |
| `src/cache.py` / `src/observability.py` | Result cache (Redis/no-op) / Prometheus metrics |
| `src/app/web/{index.html,styles.css,app.js,assets/}` | Bundled single-page web UI (served by the API at `/ui`); Canva-generated brand assets |
| `src/app/chatbot.py` | Streamlit UI — alternative front end (Ported/Diff tabs, coverage row) |
| `src/eval/{dataset/golden.py,metrics.py,run_eval.py}` | Golden set + metrics + gate runner |
| `scripts/run_ingestion.py` / `scripts/manual_search.py` | Manual ingestion / retrieval smoke (live) |
| `Dockerfile.{api,worker,ui,sandbox}` / `docker-compose.yml` / `Makefile` | Containers + compose + make targets |
| `.github/workflows/{ci,deploy}.yml` | CI (lint/type/test/eval gate/build) + deploy |

---

## 9. Quality gates

```bash
ruff check . && ruff format --check .     # lint + format (CI gates on these)
pytest -q                                 # full unit suite (hermetic; externals mocked)
python -m src.eval.run_eval --seed-only   # offline eval gate (detection recall + ref cleanliness)
```
CI (`.github/workflows/ci.yml`): ruff → mypy (non-blocking) → pytest → eval gate → docker build.

---

## 10. What's done / what's next

**Done:** the full pipeline (M1–M7), local GPU embeddings, three LLM providers, two-tier eval (isolated + executable), Docker sandbox executable verification, file/repo migration + diff + coverage.

**Done this session (2026-06-09/10):**
- **Open-sourced** — public on GitHub (`main`), MIT, CI **green** (test + docker-build).
- **Golden eval expanded 8 → 14 cases** (covers every curated deprecation except `qiskit.pulse`); deterministic gate re-verified (detection 17/17, cleanliness 14/14, references 13/14 executable on Qiskit 2.2.3).
- **New web UI** (`src/app/web/`, served by the API at `/ui`): hero, examples, progress stepper, metrics, **side-by-side diff**, cited changes, sandbox verdict; soft modern theme; Canva brand assets. Streamlit kept as the alternative.
- **Live full E2E verified through the browser** (Ollama + Pinecone + Docker sandbox): correct migration, validation PASS, sandbox `ok=True`.
- **Fixed a real dependency bug** (`anthropic`/`langchain-ollama` undeclared) — found via CI, root-caused by reproducing CI in Docker.
- **Code review done** → see **§12** for the prioritized roadmap.

**Done this session (2026-06-10, cont.) — adversarial eval + first closed gap:**
- **Held-out adversarial eval landed** (`src/eval/dataset/adversarial.py`, `run_eval --adversarial`, `src/tests/test_adversarial_eval.py`): a **non-gating** coverage-gap probe built from deprecations **deliberately absent from the seed**. An integrity test enforces the held-out invariant (fails if the seed ever covers a case → graduate it to golden).
- **Ran the full loop once.** First measurement: seed detected **0/13** of the held-out cases (every category 0%) — concrete proof the seed was thin. Then **closed that gap**: curated 13 records into `known_deprecations.json` (**seed 15→28**), **graduated all 13 into the golden set** (**golden 14→27**), and re-verified the deterministic gate stays green (**detection 30/30, cleanliness 27/27**). One small code fix needed: the old `qiskit.tools.parallel_map` and modern `qiskit.utils.parallel_map` share a last segment → added `parallel_map` to `_GENERIC_SEGMENTS` so the modern path isn't false-flagged (old one still matches by full symbol).
- **Pushed the frontier out:** replaced the adversarial set with **5 new corpus-verified held-out cases** (diagonal, squ, converters.ast_to_dag, transpiler.synthesis graysynth/cnot_synth) the now-bigger seed still misses → diagnostic reads **0/5**. The loop is repeatable.

**Done this session (cont.) — automating seed growth (the "knowledge harvester"):**
- **Researched the best way to auto-grow the trusted table** (deep-research pass, fact-checked). Verdict: a **mine → propose → verify → promote** pipeline. Stage 1 (mine "what broke"): **Griffe** static API-diff between two PyPI versions. Stage 2 (propose replacement): Qiskit's own `deprecate_arg(new_alias=…)`/`deprecate_func` decorators + the community **`flake8-qiskit-migration`** ruleset (QKT100-202, covers 0.x→1.0→2.0 import moves) + LLM extraction for prose. Stage 3 (verify): the existing Docker sandbox. Stage 4: promote to a tiered-trust table, measure with the adversarial eval. No official IBM auto-rewrite tool exists — building this is genuinely the move.
- **Griffe spike — validated Stage 1 on real Qiskit.** Diffed `qiskit 0.46.3 → 2.0.2` statically (no install needed; public API is pure-Python so the Rust internals don't block it): **2,858 breaking changes, 2,521 object removals, ~17/18 recall** on our known deprecations (only `PauliTable` truly missed; `BasicAer` caught at module level). ~**69 top-level public removals** vs the 28 curated → the table covers **~1% of the real surface**, now mechanically quantified. Caveats found: Griffe under-reports top-level re-export aliases and over-reports "moved-but-still-importable" symbols — i.e. it's high-recall but **noisy**, which is exactly why Stage 3 is mandatory.
- **Built Stage 3 — the execution-verification gate** (`src/migration/verify_record.py` + tests). `verify_candidate(symbol, replacement, sandbox)` probes any symbol form (module / module.member / bare `Class.method`) and returns a `RecordVerdict`. Hardened after the live run: gate on `old_absent` (a *genuine* ImportError/AttributeError — a sandbox timeout/outage is **inconclusive**, never promoted), and a bad replacement hypothesis no longer sinks a valid removal (the replacement is just dropped).
- **Built + proved the whole harvester end-to-end (Stage 1→4)** (`src/migration/harvest.py` + `src/tests/test_harvest.py`; `griffe` added as the `[harvest]` extra, lazy-imported). `mine_candidates` (Griffe diff → public removed symbols + best-effort replacement from the old docstring) → `harvest_candidates` (sandbox-verify → promote as `source="sandbox-verified"`, a new tier between curated and parser in `_score`). **Live run, real Docker + Qiskit 2.x:** mined **2,509** public removed candidates from `0.46→2.0`; a 12-candidate batch verified **12/12 removed** — including `qasm`/`bind_parameters` (which it *rediscovered* from the seed — cross-validation).
- **Hardened the miner (3 refinements) → closes the full gap.** A first live run only closed 3/5 of the adversarial frontier, which surfaced: (1) **module expansion** — Griffe reports a removed *module* as one breakage, so expand it to its public members (catches `graysynth`/`cnot_synth` inside the removed `qiskit.transpiler.synthesis`); (2) **dedup by last segment** keeping the canonical shortest path — collapses inherited-method explosions (`diagonal`/`squ` × ~15 classes), cutting candidates **2,509 → 1,177**, detection-preserving; (3) **decorator-first replacements** (`new_alias`/`additional_msg`) over docstrings, with a stopword guard. **Re-run result: the harvester drives the adversarial frontier `0/5 → 5/5` end-to-end, zero curation.** Remaining weak spot: replacement extraction is still thin (decorators absent for many 0.46 symbols) — detection coverage grows regardless, and bad replacements are dropped by the verifier, so records stay clean.
- **Ran the full harvest + auto-loaded it (durably).** Hardened the CLI first (a 1h batch run was killed and lost everything because it wrote only at the end) → **streaming writes + resume + progress logging** (`--out` JSON, crash-safe). Full run: **1,177 candidates → 1,153 sandbox-verified removals** (24 correctly rejected as still-importable), **1,132 new vs the 28-record seed (~40× coverage)**, 21 rediscovered. Persisted to `src/migration/data/harvested_deprecations.json`, loaded alongside the seed by `build_deprecation_store`/`_ensure_store`.
- **Precision catch + fix (the gate earned its keep).** Auto-trusting all 1,153 with last-segment matching **broke cleanliness 1.0 → 0.667**: harvested removals collide by *name* with current APIs (`qiskit.pulse.cx` vs live `QuantumCircuit.cx`, `qiskit.algorithms.VQE` vs `qiskit_algorithms.VQE`, …). Fix: the **`sandbox-verified` tier matches by FULL SYMBOL only**, never last-segment (`DeprecationStore.lookup`) — names aren't hand-vetted like the seed. **Validated: gate stays PASS (detection 30/30, cleanliness 1.000), adversarial diagnostic 0/5 → 3/5** (import-form removals auto-detected; the 2 method-form ones, `diagonal`/`squ`, correctly stay seed-growth candidates). **109 unit tests pass.** Replacements: **0/1,153** — extraction remains the one unsolved piece.

**Top next moves (prioritized — full rationale in §12):**
1. **Refill the adversarial probe (it's at 0-gap) + extend replacement coverage.** The loop fully cycled: replacement extraction landed (`src/migration/replacements.py`, two sandbox-verified sources — flake8 import map → 88, migration-guide rename tables → 14; **0 → 102 replacements**), and the last 2 method-form frontier misses (`diagonal`/`squ`) were **graduated into the seed + golden** (golden 27→29, detection 32/32). The adversarial diagnostic now reads **3/3 (gap 0)** — the harvested tier covers the remaining frontier — so **refill `adversarial.py` with new held-out cases the harvest *doesn't* reach** to keep the probe measuring a real gap. Further replacement coverage: true vector-RAG over the guides for unstructured prose (same verify gate) — lower ROI, most remaining records have no documented replacement. *(Re-running the harvest is now crash-safe + resumable via `--out`.)*
2. **Local vector-store option + shippable index** — makes "fully local & free" literally true (today Pinecone is the one piece a fresh cloner can't run).
3. **Sandbox container cleanup on timeout** — small fix; closes the only real operational hazard (orphaned containers).
4. **Behavioral-equivalence check** (old-on-old vs new-on-new) — the standout differentiator; sandbox infra already exists.

**Broader backlog (build on demand):** technical post; CONTRIBUTING + UI screenshot/GIF in README; notebook (`.ipynb`) support; source-version auto-detection; generalize to a 2nd library (Pandas 1→2); multi-hop version planning; VS Code extension / pre-commit / GitHub Action; Groq/OpenRouter via an OpenAI-compatible generator.

---

## 11. External accounts / services

- **Pinecone** (managed vector DB) — `PINECONE_API_KEY`, index `qiskit-documentation`. Required for retrieval.
- **Cohere** (rerank only now) — `COHERE_API_KEY`. Optional (degrades to no-op rerank). Note: the key is a **trial** key (100k tokens/min) — fine for query-time rerank, was the reason we moved embeddings local.
- **Ollama** (local LLM) — no account; `qwen2.5-coder:7b` pulled.
- **Anthropic** (optional, paid) — `ANTHROPIC_API_KEY` for Claude; ~$0.026/migration on Sonnet 4.6.
- **Gemini** (optional, free-tier limited) — `GEMINI_API_KEY`.
- **HuggingFace** — `HF_TOKEN` (model downloads).

---

## 12. Code review — strengths, weaknesses & prioritized roadmap

_Full read-through of the core on 2026-06-10. One-line thesis: **the system around the LLM is excellent; the knowledge it's grounded in is the bottleneck.** The architecture was designed so curating more knowledge is cheap and immediately leveraged — now it's time to collect on that._

**Strengths (keep these):** the architecture shrinks the LLM's job (authoritative table decides *what* changed; retrieval supplies evidence; LLM only rewrites; two validators catch lies). Verification-first with honest numbers. Strong hygiene — DI everywhere, graceful degradation (cache/reranker/Redis → no-op/eager), lazy provider imports, shared structured-output schema across providers. Excellent docs.

**Weaknesses, ranked by impact:**

1. **The knowledge base is the moat, and it's thin — and the eval can't see that.** `known_deprecations.json` has ~15 records, and the 14 golden cases are derived from those same records → detection recall is **circular** (it measures "lookup works on APIs the seed knows," not real-world coverage). Qiskit 0.x→2.x has hundreds of breaking changes (`QuantumInstance`, `qiskit.test.mock`, `qc.cnot()`, old `transpile` kwargs, `qiskit.tools.visualization`, …). **Fix:** build a **held-out adversarial eval** from real old-Qiskit code (textbooks, pre-1.0 GitHub repos) you did *not* curate the seed from; measure the coverage gap; use it to drive seed growth. _This is the #1 priority._ **(Instrument built AND first gap closed — 2026-06-10. `src/eval/dataset/adversarial.py` + `run_eval --adversarial` (non-gating) + held-out invariant test. Baseline was 0/13; curated those 13 into the seed (15→28), graduated them into golden (14→27, gate still 1.00 at 30/30 detection + 27/27 cleanliness), and refilled the probe with 5 new held-out cases now reading 0/5. The loop — measure gap → curate → graduate → refill — is proven and repeatable; remaining work is just more turns of it.)** **Automating it (so curation isn't hand-work): researched + Stage 1 and Stage 3 built. Pipeline = mine→propose→verify→promote. Griffe API-diff (Stage 1) validated on real Qiskit — `0.46→2.0` surfaces ~2,500 removals at ~17/18 recall, quantifying the table at ~1% coverage; it's high-recall but noisy. The execution-verification gate (Stage 3, `src/migration/verify_record.py`) and the full autonomous driver (`src/migration/harvest.py`, Stage 1→4) are **built, tested, and proven end-to-end on real Docker + Qiskit 2.x** — a 12-candidate batch verified 12/12 removed, rediscovering seed entries. The "sandbox-verified" trust tier (`_score`) is wired. Remaining is operational, not architectural: run a full harvest pass to promote the ~2,500 verified removals (then watch the adversarial eval climb off ~1%), and strengthen the still-weak replacement extraction (decorator `additional_msg`/`new_alias`, the `flake8-qiskit-migration` map, or RAG) — detection coverage already grows without it.**
2. **"Fully local & free" has a Pinecone asterisk.** A fresh cloner can't run retrieval without a Pinecone account + key + re-ingesting a separately-cloned corpus → only `--offline` works out of the box. **Fix:** a local vector backend (Chroma/FAISS/sqlite-vec) behind the existing pluggable pattern + a shippable pre-built index.
3. **Docker sandbox leaks containers on timeout.** `sandbox.py:111` `subprocess.run(timeout=…)` kills the `docker` CLI, not the container — an LLM infinite loop orphans a 1-CPU/1GB container. **Fix:** run with `--name` and `docker rm -f` on timeout (or wrap the in-container cmd with coreutils `timeout`). While there add `--cap-drop=ALL --security-opt=no-new-privileges`; note `SANDBOX_BACKEND=local` runs LLM output on the host with zero isolation (convention-only guard). **✓ FIXED (2026-06-10):** `DockerSandbox.run` now uses `--name` + `docker rm -f` on timeout and adds `--cap-drop=ALL --security-opt=no-new-privileges` (verified: qiskit 2.2.3 still runs, no orphaned containers).
4. **API isn't multi-instance-safe / no auth.** Rate limiter (`api/main.py:44`) is in-process memory keyed on direct client IP → resets on restart, and behind a proxy every request shares the proxy IP (one user exhausts all). `/metrics` unauthenticated; `user_id` in schema but never populated; cache key is code+target only (a better prompt/seed won't invalidate stale cached results — 1-day TTL mostly saves it). Fine for single-VM as documented; fix before real exposure.
5. **Smaller/real:** repair loop feeds only the *latest* failure (can oscillate A→B→A and burn all repairs) — **partially mitigated** by the deterministic safety-net (`transform._apply_known_replacements`) which applies the table's verified replacement when the LLM leaves a known-deprecated symbol, adopting it only if it then validates + runs; it also sandbox-runs code that already failed static validation (wasted run). ~~UI poll timeout (240s) < server job timeout (900s)~~ **✓ FIXED:** UI `POLL_TIMEOUT_MS` now 930s, matching `job_timeout_s`. ~~`retrieval/search.py` calls `logging.basicConfig()` at import~~ **✓ FIXED:** removed from `search.py` + the 3 ingestion modules (libraries no longer configure root logging). Last-segment matching relies on hand-maintained `_GENERIC_SEGMENTS`/`_CURRENT_ALLOWLIST` stoplists. CI mypy non-blocking; no coverage; docker-build builds images it never runs.

**Recommended order:** (1) adversarial eval → (2) local vector store + shipped index → (3) sandbox cleanup + cap-drop → (4) behavioral-equivalence check. Items 1–2 unlock the OSS story; 3 is a tiny safety PR; 4 is the differentiator.
</content>
