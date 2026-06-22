"""Unit tests for the typed settings (no network, no secrets required)."""

from __future__ import annotations

from qiskit_migration.config import Settings


def test_defaults_load_without_secrets(monkeypatch):
    for key in ("PINECONE_API_KEY", "COHERE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    # `_env_file=None` ignores any real local .env so we test the declared defaults.
    settings = Settings(_env_file=None)

    assert settings.pinecone_index == "qiskit-documentation"
    assert settings.embedding_provider == "local"
    assert settings.embedding_model == "BAAI/bge-large-en-v1.5"
    assert settings.embedding_dimension == 1024
    assert settings.qiskit_target_version == "2.2"
    # Secrets are optional at construction time; clients validate them when used.
    assert settings.pinecone_api_key is None
    assert settings.cohere_api_key is None


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("PINECONE_INDEX", "custom-index")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "768")
    monkeypatch.setenv("COHERE_API_KEY", "test-key")

    settings = Settings(_env_file=None)

    assert settings.pinecone_index == "custom-index"
    assert settings.embedding_dimension == 768
    assert settings.cohere_api_key == "test-key"
