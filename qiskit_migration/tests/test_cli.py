"""Unit tests for the CLI entry point (hermetic — offline path, no network)."""

from __future__ import annotations

import io

import qiskit_migration.migration.cli as cli
from qiskit_migration.migration.deprecations import DeprecationStore, load_seed_records


class _RecordingStream(io.StringIO):
    """A text stream that records the encoding it is reconfigured to (like a real console)."""

    encoding_set: str | None = None

    def reconfigure(self, *, encoding=None, **kwargs):
        self.encoding_set = encoding


def _seeded_db(tmp_path) -> str:
    db = tmp_path / "dep.db"
    store = DeprecationStore(str(db))
    store.create()
    store.upsert_many(load_seed_records())
    return str(db)


def test_main_forces_utf8_stdout(tmp_path, monkeypatch):
    # main() must promote stdout/stderr to UTF-8 so non-ASCII migrated code (e.g. a Parameter
    # named "θ") never triggers UnicodeEncodeError on a Windows cp1252 console.
    out, err = _RecordingStream(), _RecordingStream()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    rc = cli.main(
        [
            "--offline",
            "--code",
            "theta = 1  # θ",
            "--db",
            _seeded_db(tmp_path),
            "--docs-dir",
            str(tmp_path),
        ]
    )

    assert rc == 0
    assert out.encoding_set == "utf-8"
    assert err.encoding_set == "utf-8"


def test_main_reconfigure_is_resilient(tmp_path, monkeypatch):
    # A stream lacking reconfigure() (e.g. pytest's capture, a plain pipe) must not crash main().
    monkeypatch.setattr(cli.sys, "stdout", io.StringIO())
    monkeypatch.setattr(cli.sys, "stderr", io.StringIO())

    rc = cli.main(
        ["--offline", "--code", "x = 1", "--db", _seeded_db(tmp_path), "--docs-dir", str(tmp_path)]
    )

    assert rc == 0  # plain StringIO has no reconfigure(); the try/except swallows it
