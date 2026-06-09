/* Qiskit Migration Assistant — front-end logic.
   Thin client of the migration API: POST /migrate, poll GET /jobs/{id}, render the result. */

"use strict";

// API is served from the same origin as this page (FastAPI mounts the UI at /ui).
const API = "";
const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 240000;

const EXAMPLES = {
  "execute + Aer  (Qiskit ≤ 0.46)":
    "from qiskit import QuantumCircuit, execute, Aer\n" +
    "qc = QuantumCircuit(2, 2)\n" +
    "qc.h(0)\n" +
    "qc.cx(0, 1)\n" +
    "qc.measure([0, 1], [0, 1])\n" +
    "backend = Aer.get_backend('qasm_simulator')\n" +
    "result = execute(qc, backend, shots=1024).result()\n" +
    "print(result.get_counts())\n",
  "bind_parameters  (Qiskit ≤ 0.45)":
    "from qiskit import QuantumCircuit\n" +
    "from qiskit.circuit import Parameter\n" +
    "theta = Parameter('t')\n" +
    "qc = QuantumCircuit(1)\n" +
    "qc.rx(theta, 0)\n" +
    "bound = qc.bind_parameters({theta: 0.5})\n",
  "opflow operators  (Qiskit ≤ 0.43)":
    "from qiskit.opflow import X, Z\n" +
    "op = (X ^ X) + (Z ^ Z)\n",
  "VQE: opflow + algorithms  (Qiskit ≤ 0.43)":
    "from qiskit.opflow import X, Z, I\n" +
    "from qiskit.algorithms.optimizers import SPSA\n" +
    "hamiltonian = (X ^ X) + (Z ^ Z) + (I ^ I)\n" +
    "optimizer = SPSA(maxiter=50)\n",
};

const PROGRESS_STEPS = [
  "Detecting deprecations",
  "Retrieving release notes",
  "Generating migration",
  "Validating output",
  "Running in sandbox",
];

const $ = (sel) => document.querySelector(sel);
const codeEl = $("#code");
const runBtn = $("#run");
const outputEl = $("#output");
const examplesEl = $("#examples");
const sourceEl = $("#source-version");

let progressTimer = null;

/* ---------- helpers ---------- */

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const PY_KEYWORDS = new Set(
  ("from import as def class return if elif else for while in with try except finally raise " +
    "and or not is None True False lambda yield global nonlocal assert pass break continue " +
    "async await del").split(" ")
);
const PY_BUILTINS = new Set(
  "print range len list dict set tuple int float str bool enumerate zip map filter sum abs".split(" ")
);

// Lightweight, dependency-free Python highlighter. Operates token-by-token and escapes
// each piece, so it is safe against HTML injection from server-provided code.
function highlightPython(code) {
  const re =
    /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b\d+\.?\d*\b)|([A-Za-z_]\w*)/g;
  let out = "";
  let last = 0;
  let m;
  while ((m = re.exec(code)) !== null) {
    out += esc(code.slice(last, m.index));
    if (m[1]) out += `<span class="tok-com">${esc(m[1])}</span>`;
    else if (m[2]) out += `<span class="tok-str">${esc(m[2])}</span>`;
    else if (m[3]) out += `<span class="tok-num">${esc(m[3])}</span>`;
    else if (m[4]) {
      const w = m[4];
      if (PY_KEYWORDS.has(w)) out += `<span class="tok-kw">${w}</span>`;
      else if (PY_BUILTINS.has(w)) out += `<span class="tok-bi">${w}</span>`;
      else out += esc(w);
    }
    last = re.lastIndex;
  }
  out += esc(code.slice(last));
  return out;
}

// Line-level diff via longest-common-subsequence (snippets are small).
function lineDiff(aText, bText) {
  const a = aText.replace(/\n$/, "").split("\n");
  const b = bText.replace(/\n$/, "").split("\n");
  const n = a.length, mLen = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(mLen + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = mLen - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const rows = [];
  let i = 0, j = 0;
  while (i < n && j < mLen) {
    if (a[i] === b[j]) { rows.push([" ", a[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { rows.push(["-", a[i]]); i++; }
    else { rows.push(["+", b[j]]); j++; }
  }
  while (i < n) rows.push(["-", a[i++]]);
  while (j < mLen) rows.push(["+", b[j++]]);
  return rows;
}

// Turn the unified LCS rows into two aligned columns for a side-by-side view.
function buildSplit(rows) {
  const left = [], right = [];
  let ln = 1, rn = 1, pendDel = [], pendAdd = [];
  const flush = () => {
    const n = Math.max(pendDel.length, pendAdd.length);
    for (let k = 0; k < n; k++) {
      const d = pendDel[k], a = pendAdd[k];
      left.push(d !== undefined ? { num: ln++, cls: "del", text: d } : { num: "", cls: "empty", text: "" });
      right.push(a !== undefined ? { num: rn++, cls: "add", text: a } : { num: "", cls: "empty", text: "" });
    }
    pendDel = []; pendAdd = [];
  };
  for (const [t, line] of rows) {
    if (t === " ") {
      flush();
      left.push({ num: ln++, cls: "ctx", text: line });
      right.push({ num: rn++, cls: "ctx", text: line });
    } else if (t === "-") pendDel.push(line);
    else pendAdd.push(line);
  }
  flush();
  return { left, right };
}

function renderSplit(original, ported) {
  const rows = lineDiff(original, ported);
  if (!rows.some((r) => r[0] !== " ")) {
    return `<div class="split-empty">No textual changes between the original and migrated code.</div>`;
  }
  const { left, right } = buildSplit(rows);
  let cells = `<span class="dh l">Original</span><span class="dh r">Migrated</span>`;
  for (let i = 0; i < left.length; i++) {
    const L = left[i], R = right[i];
    cells +=
      `<span class="dn">${L.num}</span>` +
      `<span class="dc ${L.cls}">${L.text ? highlightPython(L.text) : ""}</span>` +
      `<span class="dn sep">${R.num}</span>` +
      `<span class="dc ${R.cls}">${R.text ? highlightPython(R.text) : ""}</span>`;
  }
  return `<div class="split">${cells}</div>`;
}

function codeBlock(code) {
  return `<div class="tab-body"><button class="copy-btn" data-copy>Copy</button><div class="codewrap"><pre class="code">${highlightPython(code)}</pre></div></div>`;
}

/* ---------- progress ---------- */

function showProgress() {
  clearProgress();
  outputEl.innerHTML =
    `<div class="progress">
      <p class="ptitle"><span class="spinner"></span> Migrating…</p>
      <ul class="steps">
        ${PROGRESS_STEPS.map(
          (s, k) => `<li data-step="${k}"><span class="ic">${k + 1}</span>${esc(s)}</li>`
        ).join("")}
      </ul>
    </div>`;
  let active = 0;
  const mark = (idx) => {
    outputEl.querySelectorAll(".steps li").forEach((li, k) => {
      li.classList.toggle("done", k < idx);
      li.classList.toggle("active", k === idx);
      if (k === idx) li.querySelector(".ic").textContent = "●";
      else if (k < idx) li.querySelector(".ic").textContent = "✓";
      else li.querySelector(".ic").textContent = k + 1;
    });
  };
  mark(0);
  // Faux-advance through the steps; the final step holds until the real result arrives.
  progressTimer = setInterval(() => {
    if (active < PROGRESS_STEPS.length - 1) mark(++active);
  }, 2600);
}

function clearProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
}

/* ---------- rendering ---------- */

function bannerHtml(result) {
  const v = result.validation || {};
  const passed = v.syntax_ok && !(v.deprecated_symbols || []).length && !(v.errors || []).length;
  if (passed) return `<div class="banner ok">✓ Migration complete — ported code passed static validation.</div>`;
  return `<div class="banner warn">⚠ Migration complete — review the findings below.</div>`;
}

function metricsHtml(result) {
  const cov = result.coverage || {};
  const handled = `${cov.handled ?? 0}/${cov.total ?? 0}`;
  const valOk = cov.validation_passed;
  return `<div class="metrics">
    <div class="metric"><div class="m-num">${handled}</div><div class="m-lbl">APIs handled</div></div>
    <div class="metric"><div class="m-num ${valOk ? "good" : "bad"}">${valOk ? "PASS" : "FAIL"}</div><div class="m-lbl">Validation</div></div>
    <div class="metric"><div class="m-num">${result.repair_attempts ?? 0}</div><div class="m-lbl">Self-repairs</div></div>
  </div>`;
}

function tabsHtml(original, ported) {
  return `<div class="tabs">
      <button class="tab active" data-tab="code">Ported code</button>
      <button class="tab" data-tab="diff">Side-by-side diff</button>
    </div>
    <div data-pane="code">${codeBlock(ported || "")}</div>
    <div data-pane="diff" hidden><div class="tab-body"><div class="codewrap">${renderSplit(original, ported || "")}</div></div></div>`;
}

function changesHtml(result) {
  const changes = result.changes || [];
  if (!changes.length) return "";
  const items = changes
    .map((c) => {
      const cite = c.citation ? `<span class="cite">${esc(c.citation)}</span>` : "";
      return `<div class="change">
        <div class="swap"><span class="old">${esc(c.old)}</span><span class="arr">→</span><span class="new">${esc(c.new)}</span></div>
        <div class="why">${esc(c.reason)}</div>${cite}
      </div>`;
    })
    .join("");
  return `<div class="section"><h3>Changes <span class="count">(${changes.length})</span></h3>${items}</div>`;
}

function depsHtml(result) {
  const deps = result.deprecations_found || [];
  if (!deps.length) return "";
  const items = deps
    .map((d) => {
      const repl = d.replacement || "no direct replacement";
      return `<div class="dep-item"><code>${esc(d.symbol)}</code><span class="status">${esc(d.status)}</span> → <code>${esc(repl)}</code>${d.note ? `<span class="note">${esc(d.note)}</span>` : ""}</div>`;
    })
    .join("");
  return `<details class="dep"><summary>Deprecations detected (${deps.length})</summary>${items}</details>`;
}

function sandboxHtml(result) {
  const ex = result.execution;
  if (!ex) return "";
  if (ex.ok) return `<div class="sandbox-line ok">✓ Sandbox: executed cleanly on Qiskit ${esc(result.target_version || "")}.</div>`;
  return `<div class="sandbox-line bad">✗ Sandbox: ${esc(ex.error_type || "failed to run")}${ex.timed_out ? " (timed out)" : ""}.</div>`;
}

function warningsHtml(result) {
  const w = result.warnings || [];
  if (!w.length) return "";
  return `<div class="section"><h3>Warnings</h3><ul class="warns">${w.map((x) => `<li>⚠️ ${esc(x)}</li>`).join("")}</ul></div>`;
}

function renderResult(result, original) {
  clearProgress();
  outputEl.innerHTML = `<div class="result">
    ${bannerHtml(result)}
    ${metricsHtml(result)}
    ${tabsHtml(original, result.ported_code)}
    ${changesHtml(result)}
    ${depsHtml(result)}
    ${sandboxHtml(result)}
    ${warningsHtml(result)}
  </div>`;
  wireResultEvents();
}

function renderError(msg) {
  clearProgress();
  outputEl.innerHTML = `<div class="result"><div class="banner err">✗ ${esc(msg)}</div></div>`;
}

function wireResultEvents() {
  outputEl.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const which = tab.dataset.tab;
      outputEl.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      outputEl.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== which));
    });
  });
  outputEl.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const pre = btn.parentElement.querySelector("pre.code");
      try {
        await navigator.clipboard.writeText(pre.innerText);
        btn.textContent = "Copied!";
        setTimeout(() => (btn.textContent = "Copy"), 1400);
      } catch {
        btn.textContent = "Copy failed";
      }
    });
  });
}

/* ---------- API ---------- */

async function submitMigration(code, sourceVersion) {
  const resp = await fetch(`${API}/migrate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ code, source_version: sourceVersion || null }),
  });
  if (resp.status === 400) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || "Input rejected.");
  }
  if (resp.status === 429) throw new Error("Rate limit exceeded — please wait a moment and retry.");
  if (!resp.ok) throw new Error(`Server returned ${resp.status}.`);
  return (await resp.json()).job_id;
}

async function pollJob(jobId) {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const resp = await fetch(`${API}/jobs/${encodeURIComponent(jobId)}`);
    if (!resp.ok) throw new Error(`Polling failed (${resp.status}).`);
    const body = await resp.json();
    if (body.status === "completed" || body.status === "failed") return body;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  throw new Error("Migration timed out.");
}

async function run() {
  const code = codeEl.value.trim();
  if (!code) return;
  const original = codeEl.value;
  runBtn.disabled = true;
  runBtn.classList.add("loading");
  runBtn.querySelector(".btn-label").textContent = "Migrating…";
  showProgress();
  try {
    const jobId = await submitMigration(code, sourceEl.value.trim());
    const job = await pollJob(jobId);
    if (job.status === "failed") renderError(`Migration failed: ${job.error || "unknown error"}`);
    else if (job.result) renderResult(job.result, original);
    else renderError("Job completed but returned no result.");
  } catch (e) {
    renderError(e.message || String(e));
  } finally {
    runBtn.disabled = !codeEl.value.trim();
    runBtn.classList.remove("loading");
    runBtn.querySelector(".btn-label").textContent = "Migrate";
  }
}

async function checkStatus() {
  const pill = $("#api-status");
  const txt = $("#api-status-text");
  try {
    const r = await fetch(`${API}/healthz`, { cache: "no-store" });
    if (r.ok) { pill.className = "pill pill-ok"; txt.textContent = "API online"; return; }
    throw new Error();
  } catch {
    pill.className = "pill pill-down";
    txt.textContent = "API offline";
  }
}

/* ---------- wiring ---------- */

function init() {
  for (const name of Object.keys(EXAMPLES)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    examplesEl.appendChild(opt);
  }
  examplesEl.addEventListener("change", () => {
    const v = examplesEl.value;
    if (v && EXAMPLES[v]) {
      codeEl.value = EXAMPLES[v];
      runBtn.disabled = false;
      codeEl.focus();
    }
  });
  codeEl.addEventListener("input", () => (runBtn.disabled = !codeEl.value.trim()));
  runBtn.addEventListener("click", run);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (!runBtn.disabled) run(); }
    if (e.key === "Escape" && !progressTimer) {
      const empty = $("#empty-state");
      if (empty) return;
      location.hash = "";
    }
  });
  checkStatus();
  setInterval(checkStatus, 15000);
}

init();
