"""Markdown/MDX loader for the Qiskit documentation corpus.

Emits a stream of `Document` objects with version-aware metadata. The previous
implementation only special-cased the single path fragment ``api/qiskit/2.2`` and
tagged everything else ``general`` — which meant the 9 historical API versions in
the corpus were indistinguishable to the retriever. For a migration assistant we
*want* the historical versions (to know what old code used), but every chunk must
be precisely tagged with its ``version`` and ``doc_type`` so retrieval can target
the right era (e.g. "what replaced this removed API").
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# e.g. ".../api/qiskit/0.46/circuit.mdx" or ".../api/qiskit/2.2/index.mdx"
_API_VERSION_RE = re.compile(r"api/qiskit/(\d+\.\d+)/")
# e.g. ".../api/qiskit/release-notes/0.45.mdx"
_RELEASE_NOTE_RE = re.compile(r"api/qiskit/release-notes/([\w.\-]+)\.mdx?$", re.IGNORECASE)


@dataclass
class Document:
    content: str
    metadata: dict[str, Any]


class QiskitMarkdownLoader:
    def __init__(self, base_dir: str, current_version: str = "2.2"):
        self.base_dir = Path(base_dir)
        self.current_version = current_version
        logger.info("Base directory: %s (current version=%s)", self.base_dir, current_version)
        if not self.base_dir.exists() or not self.base_dir.is_dir():
            raise ValueError(f"The directory {self.base_dir} does not exist.")

    def _extract_metadata(self, file_path: Path) -> dict[str, Any]:
        path_str = file_path.as_posix()
        version: str | None = None

        release_note = _RELEASE_NOTE_RE.search(path_str)
        api_version = _API_VERSION_RE.search(path_str)

        if release_note:
            doc_type = "release_note"
            version = release_note.group(1)
        elif "migrat" in path_str.lower() or "upgrade-from-open" in path_str.lower():
            doc_type = "migration_guide"
        elif api_version:
            version = api_version.group(1)
            doc_type = "current_api" if version == self.current_version else "api_versioned"
        elif "/guides/" in path_str or path_str.startswith("guides/"):
            doc_type = "guide"
        elif "/tutorials/" in path_str or path_str.startswith("tutorials/"):
            doc_type = "tutorial"
        else:
            doc_type = "general"

        metadata: dict[str, Any] = {
            "source": str(file_path.relative_to(self.base_dir).as_posix()),
            "doc_type": doc_type,
            "file_extension": file_path.suffix,
        }
        if version:
            metadata["version"] = version
        return metadata

    def load(self, doc_types: set[str] | None = None) -> Iterator[Document]:
        """Yield Documents. If `doc_types` is given, only those doc types are read.

        Metadata is path-derived, so filtering happens *before* file content is read —
        cheap enough to scan the whole corpus to pull just (say) release notes.
        """
        valid_extensions = {".md", ".mdx"}

        for file_path in self.base_dir.rglob("*"):
            if file_path.suffix.lower() in valid_extensions and file_path.is_file():
                metadata = self._extract_metadata(file_path)
                if doc_types is not None and metadata["doc_type"] not in doc_types:
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                    if not content.strip():
                        continue
                    yield Document(content=content, metadata=metadata)
                except Exception as e:  # noqa: BLE001 - log and skip unreadable files
                    logger.error("Failed to read file %s: %s", file_path, e)
