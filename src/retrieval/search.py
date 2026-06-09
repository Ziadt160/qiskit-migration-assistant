"""Vector retrieval over Pinecone using Cohere query embeddings.

Supports version-aware metadata filtering (``doc_type`` and ``version``), which is
what makes migration retrieval possible: e.g. restrict to ``release_note`` /
``migration_guide`` chunks, or to the API docs of a specific source version. The
higher-level hybrid + rerank orchestration lives in ``src.migration`` (M3); this
class is the thin, reusable vector-search primitive.
"""

from __future__ import annotations

import logging
from typing import Any

from pinecone import Pinecone

from src.config import get_settings
from src.embeddings import get_embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class QiskitRetriever:
    def __init__(self, index_name: str | None = None):
        settings = get_settings()
        if not settings.pinecone_api_key:
            raise ValueError("Missing PINECONE_API_KEY in environment/.env")

        self.settings = settings
        self.pc = Pinecone(api_key=settings.pinecone_api_key)
        self.index = self.pc.Index(index_name or settings.pinecone_index)
        self.embeddings = get_embedder()

    @staticmethod
    def _build_filter(
        doc_type_filter: str | list[str] | None,
        version_filter: str | list[str] | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        flt: dict[str, Any] = {}
        if doc_type_filter:
            flt["doc_type"] = (
                {"$in": doc_type_filter}
                if isinstance(doc_type_filter, list)
                else {"$eq": doc_type_filter}
            )
        if version_filter:
            flt["version"] = (
                {"$in": version_filter}
                if isinstance(version_filter, list)
                else {"$eq": version_filter}
            )
        if extra:
            flt.update(extra)
        return flt

    def search(
        self,
        query: str,
        top_k: int | None = None,
        doc_type_filter: str | list[str] | None = None,
        version_filter: str | list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Embed `query` and return the top matching chunks (with metadata)."""
        top_k = top_k or self.settings.retrieval_top_k
        query_embedding = self.embeddings.embed_query(query)
        pinecone_filter = self._build_filter(doc_type_filter, version_filter, metadata_filter)

        if pinecone_filter:
            logger.info("Applying metadata filter: %s", pinecone_filter)

        results = self.index.query(
            vector=query_embedding,
            top_k=top_k,
            filter=pinecone_filter or None,
            include_metadata=True,
        )

        chunks: list[dict[str, Any]] = []
        for match in results.get("matches", []):
            meta = match.get("metadata", {})
            chunks.append(
                {
                    "score": match["score"],
                    "text": meta.get("text", ""),
                    "source": meta.get("source", "Unknown"),
                    "doc_type": meta.get("doc_type"),
                    "version": meta.get("version"),
                    "headers": {k: v for k, v in meta.items() if "Header" in k},
                }
            )
        return chunks
