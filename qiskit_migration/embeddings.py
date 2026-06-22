"""Embeddings + reranking, pluggable by provider.

Default provider is a LOCAL BGE model (sentence-transformers, GPU if available):
no API, no rate limits, no cost, 1024-d to match the existing Pinecone index. This
is the single vectorization entry point, so ingestion and retrieval can never use
different models. Cohere remains available as an alternate embedder and as the
(query-time, low-volume) reranker.

Choose via `EMBEDDING_PROVIDER` (local | cohere) and `RERANK_ENABLED`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from qiskit_migration.config import get_settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Embedders
# --------------------------------------------------------------------------- #


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _auto_device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001 - torch may be absent
        return "cpu"


class LocalBGEEmbedder:
    """Local BAAI/bge-large-en-v1.5 via sentence-transformers.

    BGE retrieval is asymmetric: queries get an instruction prefix, passages don't.
    Embeddings are L2-normalized to pair with the index's cosine metric.
    """

    _QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        device = settings.embedding_device or _auto_device()
        logger.info("Loading local embedder '%s' on %s...", settings.embedding_model, device)
        self._model = SentenceTransformer(settings.embedding_model, device=device)
        self._batch = settings.embedding_batch_size

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = self._model.encode(
            list(texts),
            batch_size=self._batch,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        vec = self._model.encode(
            [self._QUERY_INSTRUCTION + text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        return vec.tolist()


class CohereEmbedder:
    """Hosted Cohere embeddings (alternate provider)."""

    _BATCH = 96  # Cohere caps a single embed call at 96 inputs.

    def __init__(self) -> None:
        import cohere

        settings = get_settings()
        if not settings.cohere_api_key:
            raise ValueError("Missing COHERE_API_KEY in environment/.env")
        self._client = cohere.Client(settings.cohere_api_key)
        self.model = settings.cohere_embedding_model
        self._throttle_s = settings.embed_throttle_s

    @retry(
        stop=stop_after_attempt(8),
        wait=wait_exponential(multiplier=2, min=5, max=70),
        reraise=True,
    )
    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        if self._throttle_s:
            time.sleep(self._throttle_s)  # pace calls to respect (trial) rate limits
        resp = self._client.embed(texts=list(texts), model=self.model, input_type=input_type)
        return [list(v) for v in resp.embeddings]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        batch: list[str] = []
        for text in texts:
            batch.append(text)
            if len(batch) >= self._BATCH:
                out.extend(self._embed(batch, "search_document"))
                batch = []
        if batch:
            out.extend(self._embed(batch, "search_document"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "search_query")[0]


def get_embedder() -> Embedder:
    provider = get_settings().embedding_provider.lower()
    if provider in ("local", "huggingface", "bge", "sentence-transformers"):
        return LocalBGEEmbedder()
    if provider == "cohere":
        return CohereEmbedder()
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {provider!r} (use 'local' or 'cohere').")


# --------------------------------------------------------------------------- #
# Rerankers (query-time, low volume)
# --------------------------------------------------------------------------- #


class Reranker(Protocol):
    def rerank(
        self, query: str, documents: Sequence[str], top_n: int | None = None
    ) -> list[tuple[int, float]]: ...


class NoOpReranker:
    """Identity reranker — keeps the input (vector-score) order."""

    def rerank(
        self, query: str, documents: Sequence[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        n = top_n or len(documents)
        return [(i, 0.0) for i in range(min(n, len(documents)))]


class CohereReranker:
    def __init__(self) -> None:
        import cohere

        settings = get_settings()
        self._client = cohere.Client(settings.cohere_api_key)
        self.model = settings.rerank_model

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    def rerank(
        self, query: str, documents: Sequence[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        resp = self._client.rerank(
            model=self.model,
            query=query,
            documents=list(documents),
            top_n=top_n or len(documents),
        )
        return [(r.index, r.relevance_score) for r in resp.results]


def get_reranker() -> Reranker:
    settings = get_settings()
    if settings.rerank_enabled and settings.cohere_api_key:
        try:
            return CohereReranker()
        except Exception as e:  # noqa: BLE001 - never let rerank init break retrieval
            logger.warning("Cohere reranker unavailable (%s); using no-op reranker.", e)
    return NoOpReranker()
