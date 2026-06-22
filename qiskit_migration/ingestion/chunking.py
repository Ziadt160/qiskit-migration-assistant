"""Two-pass Markdown chunker.

Pass 1 splits on Markdown headers (H1-H5) to preserve semantic structure; pass 2
applies recursive character splitting so chunks fit the embedding/context budget.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

try:
    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )
except ImportError:  # pragma: no cover - dependency guard
    logging.getLogger(__name__).error(
        "langchain-text-splitters not found. Install with: pip install langchain-text-splitters"
    )
    raise

from qiskit_migration.ingestion.loader import Document

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class QiskitMarkdownChunker:
    """Header-aware then size-aware chunking for Qiskit Markdown/MDX docs."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
            ("####", "Header 4"),
            ("#####", "Header 5"),
        ]
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            strip_headers=False,  # keep headers in the text for better RAG context
        )
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        logger.info(
            "Initialized QiskitMarkdownChunker (size=%s, overlap=%s)", chunk_size, chunk_overlap
        )

    def chunk_documents(self, documents: Iterator[Document]) -> Iterator[Document]:
        """Process a stream of Documents, yielding chunked Documents."""
        for doc in documents:
            try:
                header_splits = self.header_splitter.split_text(doc.content)
                chunks = self.recursive_splitter.split_documents(header_splits)

                for i, chunk in enumerate(chunks):
                    # Merge loader metadata with header metadata from the splitter.
                    chunk_metadata = doc.metadata.copy()
                    if chunk.metadata:
                        chunk_metadata.update(chunk.metadata)
                    chunk_metadata["chunk_index"] = i
                    yield Document(content=chunk.page_content, metadata=chunk_metadata)
            except Exception as e:  # noqa: BLE001 - log and skip unparseable docs
                source = doc.metadata.get("source", "Unknown")
                logger.error("Failed to chunk document %s: %s", source, e)
