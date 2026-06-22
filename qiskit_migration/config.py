"""Centralized, typed configuration.

Single source of truth for settings, loaded from environment / `.env`. Replaces
the scattered `os.environ.get(...)` calls and per-module `load_dotenv()` that the
original code used. Import `get_settings()` anywhere; it is cached so the `.env`
is parsed once per process.

Secret keys are optional at construction time (so the app and unit tests can
import this module without credentials); the components that actually need a key
validate its presence when they are instantiated, with a clear error.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- External service credentials (validated lazily by their clients) ---
    pinecone_api_key: str | None = None
    pinecone_index: str = "qiskit-documentation"
    cohere_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None  # Developer Platform key (separate from Claude Max)

    # --- Embeddings / models ---
    embedding_provider: str = "local"  # local (BGE on GPU/CPU) | cohere
    embedding_model: str = "BAAI/bge-large-en-v1.5"  # local model; 1024-d
    embedding_dimension: int = 1024
    embedding_device: str | None = None  # None -> auto (cuda if available, else cpu)
    embedding_batch_size: int = 64
    cohere_embedding_model: str = "embed-english-v3.0"  # used when provider=cohere
    rerank_enabled: bool = True
    rerank_model: str = "rerank-english-v3.0"  # Cohere reranker (query-time, low volume)

    # --- Generation LLM ---
    llm_provider: str = "gemini"  # gemini | anthropic | ollama | openai
    gemini_model: str = "gemini-2.5-flash"  # free-tier friendly; pro is limit 0 on free tier
    anthropic_model: str = "claude-sonnet-4-6"
    ollama_model: str = "qwen2.5-coder:7b"  # local, free, code-capable; fits 8GB VRAM
    ollama_base_url: str = "http://localhost:11434"
    # OpenAI-compatible provider — one generator for Groq | OpenRouter | Cerebras | GitHub Models.
    # Set base_url + key + model for your provider; many have a free tier. Prefer a tool/function-
    # calling-capable model so structured output works (e.g. GPT-4o, Llama-3.3-70B, Qwen3-Coder).
    openai_api_key: str | None = None
    openai_base_url: str | None = (
        None  # e.g. https://api.groq.com/openai/v1, https://openrouter.ai/api/v1
    )
    openai_model: str = "llama-3.3-70b-versatile"  # provider-specific id; override per provider
    # function_calling | json_schema | json_mode | text  (text = no structured output: extract a
    # code block from a plain chat reply, for models without tool/JSON support, e.g. DeepSeek-V3)
    openai_structured_method: str = "function_calling"
    # Cohere embedding throughput controls — raise the throttle for rate-limited trial keys.
    embed_throttle_s: float = 0.0
    embed_retry_cooldown_s: int = 65

    # --- Infra ---
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///app.db"  # jobs store (Postgres in prod)
    deprecations_db_path: str = "app.db"  # SQLite knowledge base (bundled, read-mostly)
    queue_eager: bool = False  # run jobs inline instead of via Redis/RQ (dev/test)
    job_timeout_s: int = 900  # RQ per-job timeout (model load + LLM + retries can be slow)

    # --- Migration / RAG parameters ---
    qiskit_target_version: str = "2.2"
    retrieval_top_k: int = 12
    rerank_top_n: int = 6
    max_input_chars: int = 20_000
    max_repairs: int = 2

    # --- Sandbox (dynamic validation of ported code) ---
    sandbox_backend: str = "none"  # none | local | docker
    sandbox_timeout_s: int = 30
    sandbox_image: str = "qiskit-migration-sandbox:latest"

    # --- Behavioral-equivalence check (old-on-old vs new-on-new; see equivalence.py) ---
    equivalence_enabled: bool = (
        False  # run the check inline in the transformer (opt-in; adds 2 runs)
    )
    legacy_sandbox_image: str = (
        "qiskit-migration-sandbox-legacy:latest"  # old Qiskit for the original
    )
    equivalence_fidelity_threshold: float = 0.999  # |<psi_old|psi_new>| above this == equivalent
    equivalence_max_qubits: int = 12  # cap statevector size (2**n amplitudes) per circuit

    # --- Hardening ---
    cache_enabled: bool = True
    cache_ttl_s: int = 86_400
    rate_limit_per_min: int = 60
    enable_metrics: bool = True

    # --- Observability (optional LangSmith tracing) ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str | None = None
    langchain_project: str | None = None
    langchain_endpoint: str | None = None

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()
