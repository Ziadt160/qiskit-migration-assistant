"""Manual retrieval smoke test (calls Cohere + Pinecone). Run from the repo root:

    python -m scripts.manual_search

Not a unit test — it hits external services.
"""

from __future__ import annotations

from src.retrieval.search import QiskitRetriever


def main() -> None:
    retriever = QiskitRetriever()
    query = "How do I build a GHZ state quantum circuit?"
    print(f"\nQuestion: {query}\n" + "=" * 60)

    results = retriever.search(query, top_k=3, doc_type_filter="current_api")
    for i, res in enumerate(results, 1):
        print(f"\nRESULT {i} (score={res['score']:.4f}) [{res['doc_type']} v{res.get('version')}]")
        print(f"source: {res['source']}")
        print(f"headers: {res['headers']}")
        print("-" * 60)
        print(res["text"][:300] + "...")


if __name__ == "__main__":
    main()
