"""Ingestion pipeline: load -> chunk -> embed (Cohere) -> upsert (Pinecone).

NOT a unit test — it calls external services. Run from the repo root:

    python -m scripts.run_ingestion                 # migration-relevant docs, wipes index first
    python -m scripts.run_ingestion --all           # ingest the entire corpus (large/expensive)
    python -m scripts.run_ingestion --no-clear       # add to the existing index instead of wiping
    python -m scripts.run_ingestion --doc-types release_note migration_guide

By default only the doc types the migration retriever actually queries are indexed
(see `MIGRATION_DOC_TYPES`) — a fraction of the corpus — and the index is wiped
first so stale vectors from a previous embedding model can't pollute retrieval.
"""

from __future__ import annotations

import argparse

from qiskit_migration.config import get_settings
from qiskit_migration.ingestion.chunking import QiskitMarkdownChunker
from qiskit_migration.ingestion.indexer import QiskitIndexer
from qiskit_migration.ingestion.loader import QiskitMarkdownLoader
from qiskit_migration.migration.retrieval import MIGRATION_DOC_TYPES


def run_full_pipeline(
    docs_dir: str = "documentation/docs",
    doc_types: set[str] | None = None,
    clear: bool = True,
    chunk_size: int = 1500,
    chunk_overlap: int = 150,
) -> int:
    """Ingest the corpus. `doc_types=None` means *all* types; otherwise only those."""
    settings = get_settings()
    loader = QiskitMarkdownLoader(docs_dir, current_version=settings.qiskit_target_version)
    chunker = QiskitMarkdownChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunked_docs = chunker.chunk_documents(loader.load(doc_types=doc_types))

    indexer = QiskitIndexer()
    if clear:
        indexer.clear()
    total = indexer.index_documents(chunked_docs, batch_size=96)

    scope = "ALL doc types" if doc_types is None else sorted(doc_types)
    print(f"Indexed {total} chunks into '{settings.pinecone_index}' (scope={scope}).")
    return total


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest Qiskit docs into Pinecone.")
    parser.add_argument("docs_dir", nargs="?", default="documentation/docs")
    parser.add_argument(
        "--all", action="store_true", help="Ingest every doc type (large/expensive)."
    )
    parser.add_argument(
        "--doc-types", nargs="*", default=None, help="Explicit doc types to ingest."
    )
    parser.add_argument(
        "--no-clear", action="store_true", help="Append to the index instead of wiping it first."
    )
    args = parser.parse_args(argv)

    if args.all:
        doc_types: set[str] | None = None
    elif args.doc_types:
        doc_types = set(args.doc_types)
    else:
        doc_types = set(MIGRATION_DOC_TYPES)

    run_full_pipeline(args.docs_dir, doc_types=doc_types, clear=not args.no_clear)


if __name__ == "__main__":
    main()
