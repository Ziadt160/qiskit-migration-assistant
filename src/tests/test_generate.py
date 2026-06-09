"""Unit tests for the pluggable generator: prompt helpers + provider factory.

The live LLM call is exercised via the CLI/eval; here we test the pure helpers and
that the factory routes/validates correctly. No SDK construction hits the network.
"""

from __future__ import annotations

import pytest

import src.generation.generate as gen
from src.config import Settings
from src.generation.generate import (
    _format_context,
    _format_deprecations,
    _prompt_vars,
    get_generator,
)
from src.migration.deprecations import DeprecationRecord


def test_format_deprecations_lists_symbol_and_replacement():
    recs = [
        DeprecationRecord(
            "qiskit.execute", "removed", "0.46", "1.0", "backend.run", "note", "curated-seed"
        )
    ]
    out = _format_deprecations(recs)
    assert "qiskit.execute" in out
    assert "backend.run" in out


def test_format_deprecations_empty():
    assert "none" in _format_deprecations([]).lower()


def test_strip_code_fences():
    from src.generation.generate import _strip_code_fences

    assert _strip_code_fences("```python\nfrom qiskit import transpile\n```") == (
        "from qiskit import transpile"
    )
    assert _strip_code_fences("```\nx = 1\n```") == "x = 1"
    plain = "from qiskit import transpile"
    assert _strip_code_fences(plain) == plain


def test_format_context_includes_source_and_text():
    out = _format_context([{"source": "guides/x.mdx", "text": "hello world"}])
    assert "guides/x.mdx" in out
    assert "hello world" in out


def test_prompt_vars_shape():
    variables = _prompt_vars("CODE", [], [], None, None, "2.2")
    assert variables["target_version"] == "2.2"
    assert variables["code"] == "CODE"
    assert variables["feedback"] == "(none)"
    # Formatting the templates with these vars must not raise (no stray braces).
    gen._SYSTEM.format(**variables)
    gen._HUMAN.format(**variables)


def test_get_generator_unknown_provider(monkeypatch):
    monkeypatch.setattr(gen, "get_settings", lambda: Settings(_env_file=None, llm_provider="bogus"))
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_generator()


def test_get_generator_anthropic_requires_key(monkeypatch):
    monkeypatch.setattr(
        gen,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key=None),
    )
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        get_generator()


def test_get_generator_gemini_requires_key(monkeypatch):
    monkeypatch.setattr(
        gen,
        "get_settings",
        lambda: Settings(_env_file=None, llm_provider="gemini", gemini_api_key=None),
    )
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        get_generator()


def test_get_generator_ollama_no_key_needed(monkeypatch):
    pytest.importorskip("langchain_ollama")
    monkeypatch.setattr(
        gen, "get_settings", lambda: Settings(_env_file=None, llm_provider="ollama")
    )
    generator = get_generator()  # local; constructs without a key or network
    assert type(generator).__name__ == "OllamaGenerator"
