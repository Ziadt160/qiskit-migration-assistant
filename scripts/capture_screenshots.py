"""Capture UI screenshots for the docs by driving the running web app with Playwright.

The app must already be served (see docs/HANDOFF.md §4), e.g. on http://localhost:8011/ui/.
This is doc tooling, not a runtime dependency:

    pip install playwright && playwright install chromium
    python scripts/capture_screenshots.py --url http://localhost:8011/ui/

Each scenario pastes an old-Qiskit snippet, runs the real migration (Ollama + RAG + sandbox),
waits for the result to render, and saves a PNG of the workbench to docs/screenshots/.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# (filename, source_version, old_code) — the four built-in UI examples.
SCENARIOS: list[tuple[str, str, str]] = [
    (
        "01-execute-aer",
        "0.46",
        "from qiskit import QuantumCircuit, execute, Aer\n"
        "qc = QuantumCircuit(2, 2)\n"
        "qc.h(0)\n"
        "qc.cx(0, 1)\n"
        "qc.measure([0, 1], [0, 1])\n"
        "backend = Aer.get_backend('qasm_simulator')\n"
        "result = execute(qc, backend, shots=1024).result()\n"
        "print(result.get_counts())\n",
    ),
    (
        "02-bind-parameters",
        "0.45",
        "from qiskit import QuantumCircuit\n"
        "from qiskit.circuit import Parameter\n"
        "theta = Parameter('t')\n"
        "qc = QuantumCircuit(1)\n"
        "qc.rx(theta, 0)\n"
        "bound = qc.bind_parameters({theta: 0.5})\n",
    ),
    (
        "03-opflow-operators",
        "0.43",
        "from qiskit.opflow import X, Z\nop = (X ^ X) + (Z ^ Z)\n",
    ),
    (
        "04-vqe-opflow-algorithms",
        "0.43",
        "from qiskit.opflow import X, Z, I\n"
        "from qiskit.algorithms.optimizers import SPSA\n"
        "hamiltonian = (X ^ X) + (Z ^ Z) + (I ^ I)\n"
        "optimizer = SPSA(maxiter=50)\n",
    ),
]


def _launch(p):
    """Use whatever Chromium-family browser is available (no forced download)."""
    for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(**kwargs)
        except Exception:
            continue
    raise RuntimeError("no msedge/chrome/chromium available for Playwright")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8011/ui/")
    ap.add_argument("--out", default="docs/screenshots")
    ap.add_argument("--landing-only", action="store_true", help="quick validation: hero only")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=2)

        page.goto(args.url, wait_until="load")
        page.wait_for_selector("#code")
        time.sleep(1.5)  # let the hero/api-status settle
        page.screenshot(path=str(out / "00-landing.png"))
        print("saved 00-landing", flush=True)
        if args.landing_only:
            browser.close()
            return

        for name, version, code in SCENARIOS:
            page.goto(args.url, wait_until="load")
            page.wait_for_selector("#code")
            page.fill("#code", code)
            if version:
                page.fill("#source-version", version)
            page.wait_for_selector("#run:not([disabled])", timeout=10_000)
            page.click("#run")
            page.wait_for_selector("#output .result", timeout=240_000)  # the real migration
            time.sleep(1.0)
            page.locator(".workbench").scroll_into_view_if_needed()
            page.locator(".workbench").screenshot(path=str(out / f"{name}.png"))
            print("saved", name, flush=True)

        browser.close()


if __name__ == "__main__":
    main()
