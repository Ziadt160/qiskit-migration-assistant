"""Streamlit front-end — a thin client of the migration API.

Submits pasted Qiskit code to POST /migrate, polls GET /jobs/{id}, and renders the
ported code, a diff vs the original, a coverage summary, per-change rationale,
warnings, and the validation verdict. The heavy lifting (retrieval, LLM, validation)
happens server-side in the worker.
"""

from __future__ import annotations

import difflib
import os
import time

import httpx
import streamlit as st

API_URL = os.environ.get("MIGRATION_API_URL", "http://localhost:8000")
POLL_INTERVAL_S = 1.5
POLL_TIMEOUT_S = 240

st.set_page_config(page_title="Qiskit Migration Assistant", page_icon="🛠️", layout="wide")
st.title("🛠️ Qiskit Migration Assistant")
st.markdown(
    "Paste Qiskit code written for an **older version** and get it ported to the "
    "latest release, with a cited explanation of every change."
)

with st.sidebar:
    st.header("Options")
    source_version = st.text_input("Source version (optional)", placeholder="e.g. 0.46")
    st.caption(f"API: `{API_URL}`")

old_code = st.text_area(
    "Old Qiskit code",
    height=320,
    placeholder="from qiskit import QuantumCircuit, execute, Aer\n...",
)


def _submit(code: str, src: str | None) -> str:
    # Generous timeout: in eager mode the server runs the migration inline before responding.
    resp = httpx.post(
        f"{API_URL}/migrate",
        json={"code": code, "source_version": src or None},
        timeout=240,
    )
    if resp.status_code == 400:
        raise ValueError(resp.json().get("detail", "Invalid input"))
    resp.raise_for_status()
    return resp.json()["job_id"]


def _poll(job_id: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = httpx.get(f"{API_URL}/jobs/{job_id}", timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError("Migration timed out.")


def _render(result: dict, original: str) -> None:
    validation = result.get("validation") or {}
    coverage = result.get("coverage") or {}
    passed = validation.get("syntax_ok") and not validation.get("deprecated_symbols")

    if passed:
        st.success("Migration complete — ported code passed static validation.")
    else:
        st.warning("Migration complete — review the findings below.")

    # Coverage summary
    if coverage:
        c1, c2, c3 = st.columns(3)
        handled = f"{coverage.get('handled', 0)}/{coverage.get('total', 0)}"
        c1.metric("APIs handled", handled)
        c2.metric("Validation", "PASS" if coverage.get("validation_passed") else "FAIL")
        c3.metric("Self-repairs", result.get("repair_attempts", 0))
        if coverage.get("unresolved"):
            st.error("Still deprecated in output: " + ", ".join(coverage["unresolved"]))

    ported = result.get("ported_code", "")
    tab_code, tab_diff = st.tabs(["Ported code", "Diff"])
    with tab_code:
        st.code(ported, language="python")
    with tab_diff:
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                ported.splitlines(),
                fromfile="original.py",
                tofile="migrated.py",
                lineterm="",
            )
        )
        st.code(diff or "(no textual changes)", language="diff")

    changes = result.get("changes") or []
    if changes:
        st.subheader("Changes")
        for ch in changes:
            cite = f"  \n  ↳ _source: {ch['citation']}_" if ch.get("citation") else ""
            st.markdown(f"- `{ch['old']}` → `{ch['new']}` — {ch['reason']}{cite}")

    execution = result.get("execution")
    if execution:
        if execution.get("ok"):
            st.caption(f"✅ Sandbox: ran on Qiskit {result.get('target_version', '')}")
        else:
            st.caption(f"❌ Sandbox: {execution.get('error_type') or 'failed to run'}")

    deps = result.get("deprecations_found") or []
    if deps:
        with st.expander(f"Deprecations detected ({len(deps)})"):
            for d in deps:
                repl = d.get("replacement") or "no direct replacement"
                note = d.get("note", "")
                st.markdown(f"**`{d['symbol']}`** [{d['status']}] → `{repl}`  \n{note}")

    warnings = result.get("warnings") or []
    if warnings:
        st.subheader("Warnings")
        for w in warnings:
            st.markdown(f"- ⚠️ {w}")


if st.button("Migrate", type="primary", disabled=not old_code.strip()):
    try:
        with st.spinner("Submitting and migrating…"):
            job_id = _submit(old_code, source_version)
            job = _poll(job_id)
        if job["status"] == "failed":
            st.error(f"Migration failed: {job.get('error', 'unknown error')}")
        elif job.get("result"):
            _render(job["result"], old_code)
        else:
            st.error("Job completed but returned no result.")
    except ValueError as e:
        st.error(f"Input rejected: {e}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Error contacting migration API: {e}")
