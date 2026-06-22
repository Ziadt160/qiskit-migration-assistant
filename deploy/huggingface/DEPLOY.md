# Deploy the hosted demo

A free, zero-key "paste old code → see the deprecations" demo on Hugging Face Spaces (Gradio SDK).
It runs the **offline detection** pipeline in-process — no API keys, no Docker, no cost.

## Files in this folder
- `app.py` — the Gradio demo (imports the package, builds the store from the seed + harvested tiers).
- `requirements.txt` — installs the package from GitHub (code + bundled `data/*.json`). Gradio
  itself is provided by the Space SDK, so it is **not** listed here.
- `README.md` — reference Space card (Hugging Face generates its own when you create the Space).

## Deploy (Hugging Face Spaces — Gradio)
1. Create a new Space: https://huggingface.co/new-space → SDK **Gradio** → template **Blank**,
   hardware **CPU Basic (Free)**. Hugging Face generates a starter `app.py`, `requirements.txt`,
   and `README.md` (the README has the correct `sdk_version` — keep it).
2. In the Space → **Files** → replace the generated `app.py` and `requirements.txt` with the two
   from this folder. Leave the generated `README.md` as-is.
3. The Space rebuilds (`pip install -r requirements.txt` pulls the package from GitHub) and
   launches. First build takes a few minutes.
4. Share the Space URL — add it to the repo README and your launch posts.

## Enabling full migration (optional, advanced)
Offline detection needs nothing. To also run the **LLM rewrite + sandbox validation** on a host:
- Set provider keys as **Space secrets**: `PINECONE_API_KEY`, plus one of `GEMINI_API_KEY` /
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (+ `LLM_PROVIDER`), and `COHERE_API_KEY` for rerank.
- Free Spaces have **no Docker**, so set `SANDBOX_BACKEND=local` (runs `python -W error` in-process;
  add `qiskit==2.*` + `qiskit-aer` to `requirements.txt`) or `SANDBOX_BACKEND=none` to skip
  execution validation. Behavioral-equivalence (which needs the legacy + target Docker images) is
  not available on a free Space — keep that local.
- Retrieval needs a populated Pinecone index; without it, stick to the offline demo.

> Cost note: enabling full mode means every visitor can spend your LLM credits. Prefer the
> zero-key offline demo for a public Space, or gate full mode behind your own usage.
