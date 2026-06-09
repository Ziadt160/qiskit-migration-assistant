"""Embed document chunks and upsert them into Pinecone.

Embeddings come from the shared `get_embedder()` (local BGE by default, or Cohere),
so the index and the retriever are guaranteed to use the same vectorizer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from pinecone import Pinecone, ServerlessSpec

from src.config import get_settings
from src.embeddings import get_embedder
from src.ingestion.loader import Document

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class QiskitIndexer:
    def __init__(self, index_name: str | None = None):
        settings = get_settings()
        if not settings.pinecone_api_key:
            raise ValueError("Missing PINECONE_API_KEY in environment/.env")

        self.index_name = index_name or settings.pinecone_index
        self.dimension = settings.embedding_dimension
        self.pc = Pinecone(api_key=settings.pinecone_api_key)
        self.embeddings = get_embedder()

        self._setup_pinecone_index()
        self.index = self.pc.Index(self.index_name)

    def _setup_pinecone_index(self) -> None:
        existing = [info["name"] for info in self.pc.list_indexes()]
        if self.index_name not in existing:
            logger.info("Creating new Pinecone index: %s...", self.index_name)
            self.pc.create_index(
                name=self.index_name,
                dimension=self.dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
        else:
            logger.info("Connected to Pinecone index: %s", self.index_name)

    def clear(self) -> None:
        """Delete all vectors from the index.

        Essential before re-indexing with a different embedding model: Cohere and
        the old BGE vectors share a dimension but live in different semantic spaces,
        so leaving stale vectors behind corrupts retrieval.
        """
        logger.info("Clearing all vectors from index '%s'...", self.index_name)
        self.index.delete(delete_all=True)

    @staticmethod
    def _get_batches(iterator: Iterator[Document], batch_size: int) -> Iterator[list[Document]]:
        batch: list[Document] = []
        for doc in iterator:
            batch.append(doc)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def _process_batch(self, document_batch: list[Document]) -> int:
        """Embed + upsert one batch. Raises on failure so the caller can retry it."""
        texts = [doc.content for doc in document_batch]
        embeddings = self.embeddings.embed_documents(texts)

        vectors = []
        for i, doc in enumerate(document_batch):
            safe_source = doc.metadata.get("source", "unknown").replace("/", "_").replace("\\", "_")
            chunk_id = f"{safe_source}_chunk_{doc.metadata.get('chunk_index', i)}"
            metadata = doc.metadata.copy()
            metadata["text"] = doc.content  # store text so retrieval can return it
            vectors.append({"id": chunk_id, "values": embeddings[i], "metadata": metadata})

        self.index.upsert(vectors=vectors)
        return len(vectors)

    def index_documents(self, documents: Iterator[Document], batch_size: int = 96) -> int:
        total_upserted = 0
        failed: list[list[Document]] = []

        for batch_num, document_batch in enumerate(self._get_batches(documents, batch_size)):
            try:
                total_upserted += self._process_batch(document_batch)
                logger.info("Upserted batch %s (total: %s chunks)", batch_num + 1, total_upserted)
            except Exception as e:  # noqa: BLE001 - collect and retry after the main pass
                logger.warning("Batch %s failed (%s); queued for retry.", batch_num + 1, e)
                failed.append(document_batch)

        # A failed batch is usually a transient rate limit — retry once after a cooldown
        # rather than silently leaving a hole in the index.
        dropped = 0
        if failed:
            cooldown = get_settings().embed_retry_cooldown_s
            logger.info("Retrying %s failed batch(es) after %ss cooldown...", len(failed), cooldown)
            time.sleep(cooldown)
            for batch in failed:
                try:
                    total_upserted += self._process_batch(batch)
                except Exception as e:  # noqa: BLE001
                    dropped += len(batch)
                    logger.error("Dropping %s chunks after retry failure: %s", len(batch), e)

        if dropped:
            logger.warning("Finished with %s chunks DROPPED — index is incomplete.", dropped)
        logger.info("Ingestion complete. %s vectors stored in Pinecone.", total_upserted)
        return total_upserted
