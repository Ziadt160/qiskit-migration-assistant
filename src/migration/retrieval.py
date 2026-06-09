"""Hybrid retrieval for migration: symbol/replacement-targeted + semantic, reranked.

The deprecation store already gives authoritative `symbol -> replacement` mappings;
this layer fetches the *prose* (migration guides, release notes, current API docs)
that explains and exemplifies those replacements, so the LLM can ground concrete
code on real documentation. Results are reranked with Cohere against the primary
migration intent.
"""

from __future__ import annotations

import logging

from src.config import Settings, get_settings
from src.embeddings import Reranker, get_reranker
from src.migration.deprecations import DeprecationRecord
from src.migration.symbols import ExtractedSymbols
from src.retrieval.search import QiskitRetriever

logger = logging.getLogger(__name__)

# Doc types worth consulting for a migration (skip historical-version API and
# other-package noise). Ingestion indexes exactly these — keep the two in sync.
MIGRATION_DOC_TYPES = ["migration_guide", "release_note", "current_api", "guide"]


class MigrationRetriever:
    def __init__(
        self,
        retriever: QiskitRetriever,
        settings: Settings | None = None,
        reranker: Reranker | None = None,
    ):
        self.retriever = retriever
        self.settings = settings or get_settings()
        self.reranker = reranker or get_reranker()

    @classmethod
    def from_settings(cls) -> MigrationRetriever:
        return cls(QiskitRetriever())

    def _queries(self, symbols: ExtractedSymbols, deps: list[DeprecationRecord]) -> list[str]:
        target = self.settings.qiskit_target_version
        top_syms = sorted(s for s in symbols.qualified if s.startswith("qiskit"))[:6]
        primary = (
            f"Migrate Qiskit code to version {target}. "
            f"APIs used: {', '.join(top_syms) or 'qiskit'}."
        )
        queries = [primary]
        for dep in deps[:5]:
            target_api = dep.replacement or dep.symbol
            queries.append(f"How to use {target_api} in Qiskit {target} (replacing {dep.symbol}).")
        return queries

    def retrieve(self, symbols: ExtractedSymbols, deps: list[DeprecationRecord]) -> list[dict]:
        queries = self._queries(symbols, deps)
        primary = queries[0]
        per_query_k = max(3, self.settings.retrieval_top_k // 2)

        pool: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for query in queries:
            for chunk in self.retriever.search(
                query, top_k=per_query_k, doc_type_filter=MIGRATION_DOC_TYPES
            ):
                key = (chunk.get("source", ""), chunk.get("text", "")[:120])
                if key not in seen:
                    seen.add(key)
                    pool.append(chunk)

        if not pool:
            return []

        ranked = self.reranker.rerank(
            primary, [c["text"] for c in pool], top_n=self.settings.rerank_top_n
        )
        out: list[dict] = []
        for idx, score in ranked:
            chunk = dict(pool[idx])
            chunk["rerank_score"] = score
            out.append(chunk)
        return out
