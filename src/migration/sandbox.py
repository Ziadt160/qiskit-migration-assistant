"""Execute ported code against the target Qiskit to catch what static checks can't.

This is the headline correctness signal: code that imports and runs under
`qiskit==<target>` with `DeprecationWarning` promoted to an error is genuinely
migrated. Two backends:

  * `LocalSubprocessSandbox` — runs in a subprocess of the current interpreter.
    Fast, no isolation; for dev/CI where the target Qiskit is installed.
  * `DockerSandbox` — runs in an ephemeral, network-isolated, resource-capped
    container built from `Dockerfile.sandbox` (production; untrusted user code).

`-W error::DeprecationWarning` makes a deprecation surface as a non-zero exit, so
a migration that merely "still works with warnings" is correctly flagged.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from src.config import get_settings
from src.migration.models import SandboxReport

logger = logging.getLogger(__name__)

_MAX_CAPTURE = 4000
# Last traceback line like "ImportError: cannot import name 'execute'".
_ERROR_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Warning|Exception)):", re.MULTILINE)


def _error_type(stderr: str) -> str | None:
    matches = _ERROR_LINE_RE.findall(stderr)
    return matches[-1].split(".")[-1] if matches else None


class Sandbox(Protocol):
    def run(
        self, code: str, *, warnings_as_errors: bool = True, max_capture: int | None = None
    ) -> SandboxReport: ...


class LocalSubprocessSandbox:
    backend = "local"

    def __init__(self, timeout_s: int | None = None):
        self.timeout_s = timeout_s or get_settings().sandbox_timeout_s

    def run(
        self, code: str, *, warnings_as_errors: bool = True, max_capture: int | None = None
    ) -> SandboxReport:
        cap = max_capture or _MAX_CAPTURE
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snippet.py"
            path.write_text(code, encoding="utf-8")
            warn = ["-W", "error::DeprecationWarning"] if warnings_as_errors else []
            cmd = [sys.executable, *warn, str(path)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as e:
                return SandboxReport(
                    backend=self.backend,
                    ok=False,
                    timed_out=True,
                    stderr=(e.stderr or "")[:cap] if isinstance(e.stderr, str) else "",
                )
        return SandboxReport(
            backend=self.backend,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            error_type=None if proc.returncode == 0 else _error_type(proc.stderr),
            stdout=proc.stdout[:cap],
            stderr=proc.stderr[:cap],
        )


class DockerSandbox:
    backend = "docker"

    def __init__(self, image: str | None = None, timeout_s: int | None = None):
        settings = get_settings()
        self.image = image or settings.sandbox_image
        self.timeout_s = timeout_s or settings.sandbox_timeout_s

    def run(
        self, code: str, *, warnings_as_errors: bool = True, max_capture: int | None = None
    ) -> SandboxReport:
        cap = max_capture or _MAX_CAPTURE
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snippet.py"
            path.write_text(code, encoding="utf-8")
            warn = ["-W", "error::DeprecationWarning"] if warnings_as_errors else []
            cmd = [
                "docker",
                "run",
                "--rm",
                "--network=none",  # no network for untrusted code
                "--memory=1g",
                "--cpus=1",
                "--pids-limit=256",
                "--read-only",  # immutable rootfs...
                "--tmpfs",
                "/tmp:rw,size=256m",  # ...but a writable scratch dir
                "-e",
                "HOME=/tmp",  # qiskit writes ~/.qiskit on import
                "-e",
                "MPLCONFIGDIR=/tmp",
                "-v",
                f"{tmp}:/work:ro",
                self.image,
                "python",
                *warn,
                "/work/snippet.py",
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=self.timeout_s + 30
                )
            except subprocess.TimeoutExpired:
                return SandboxReport(backend=self.backend, ok=False, timed_out=True)
            except FileNotFoundError as e:
                logger.error("Docker not available: %s", e)
                return SandboxReport(
                    backend=self.backend, ok=False, stderr="docker executable not found"
                )
        return SandboxReport(
            backend=self.backend,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            error_type=None if proc.returncode == 0 else _error_type(proc.stderr),
            stdout=proc.stdout[:cap],
            stderr=proc.stderr[:cap],
        )


def get_sandbox(backend: str | None = None) -> Sandbox | None:
    """Construct the configured sandbox, or None when disabled."""
    backend = (backend or get_settings().sandbox_backend).lower()
    if backend == "local":
        return LocalSubprocessSandbox()
    if backend == "docker":
        return DockerSandbox()
    return None
