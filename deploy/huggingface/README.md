---
title: Qiskit Migration Assistant
emoji: ⚛️
colorFrom: purple
colorTo: blue
sdk: gradio
app_file: app.py
pinned: false
license: mit
---

# Qiskit Migration Assistant — live demo

Paste legacy Qiskit (0.x / 1.x) code and see every deprecated or removed symbol, with its
verified Qiskit 2.x replacement — grounded in a sandbox-verified deprecation table.

This Space runs **offline detection only** (no API keys, no cost, no Docker). The full pipeline
— LLM rewrite, Docker-sandbox execution, and behavioral-equivalence checks — runs from the repo:
https://github.com/Ziadt160/qiskit-migration-assistant

> Note: when you create a **Blank Gradio** Space, Hugging Face generates its own README with the
> correct `sdk_version`. Keep that generated README — you only need to replace `app.py` and
> `requirements.txt`.
