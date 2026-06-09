"""Unit tests for the local subprocess sandbox (runs real subprocesses, no network)."""

from __future__ import annotations

from src.migration.sandbox import LocalSubprocessSandbox, get_sandbox


def test_clean_code_runs_ok():
    report = LocalSubprocessSandbox(timeout_s=20).run("print('hello sandbox')")
    assert report.ok
    assert report.returncode == 0
    assert "hello sandbox" in report.stdout


def test_import_error_is_captured():
    report = LocalSubprocessSandbox(timeout_s=20).run("import definitely_not_a_real_module_xyz")
    assert not report.ok
    assert report.error_type in ("ModuleNotFoundError", "ImportError")


def test_deprecation_warning_is_promoted_to_failure():
    code = "import warnings\nwarnings.warn('old api', DeprecationWarning)\n"
    report = LocalSubprocessSandbox(timeout_s=20).run(code)
    assert not report.ok
    assert report.error_type == "DeprecationWarning"


def test_get_sandbox_none_when_disabled():
    assert get_sandbox("none") is None


def test_get_sandbox_local():
    assert isinstance(get_sandbox("local"), LocalSubprocessSandbox)
