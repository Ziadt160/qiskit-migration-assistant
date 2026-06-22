# Deploy the hosted demo

A free, zero-key "paste old code → see the deprecations" demo, deployable two ways. Both run the
**offline detection** pipeline in-process (no API keys, no Docker, no cost).

## Files in this folder
- `app.py` — the Streamlit demo (imports the package, builds the store from the seed + harvested tiers).
- `requirements.txt` — installs the package from GitHub (code + bundled `data/*.json`).
- `README.md` — Hugging Face Space card (the YAML frontmatter configures the Space).

## Option A — Hugging Face Spaces (recommended)
1. Create a new Space: https://huggingface.co/new-space → SDK **Streamlit**.
2. Upload the three files from this folder (`app.py`, `requirements.txt`, `README.md`) to the
   Space repo (the frontmatter in `README.md` sets `sdk: streamlit` and `app_file: app.py`).
3. The Space builds (`pip install -r requirements.txt`, which pulls the package from GitHub) and
   launches automatically. First build takes a few minutes.
4. Share the Space URL — add it to the repo README and your launch posts.

## Option B — Streamlit Community Cloud
1. https://share.streamlit.io → "New app" → point it at this repo.
2. Set **Main file path** to `deploy/huggingface/app.py` and **Python requirements** to
   `deploy/huggingface/requirements.txt` (or add the package + streamlit to the app's deps).
3. Deploy.

## Enabling full migration (optional, advanced)
Offline detection needs nothing. To also run the **LLM rewrite + sandbox validation** on a host:
- Set provider keys as **Space secrets**: `PINECONE_API_KEY`, plus one of `GEMINI_API_KEY` /
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (+ `LLM_PROVIDER`), and `COHERE_API_KEY` for rerank.
- Free Spaces have **no Docker**, so set `SANDBOX_BACKEND=local` (runs `python -W error` in-process;
  install `qiskit==2.*` + `qiskit-aer` in `requirements.txt`) or `SANDBOX_BACKEND=none` to skip
  execution validation. Behavioral-equivalence (which needs the legacy + target Docker images) is
  not available on a free Space — keep that local.
- Retrieval needs a populated Pinecone index; without it, stick to the offline demo.

> Cost note: enabling full mode means every visitor can spend your LLM credits. Prefer the
> zero-key offline demo for a public Space, or gate full mode behind your own usage.
