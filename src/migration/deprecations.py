"""Structured deprecation knowledge — the precision backbone of the migrator.

Two layers:
  * A **curated seed** (`data/known_deprecations.json`) of hand-verified, high-value
    migrations (execute removal, bind_parameters -> assign_parameters, Aer move, ...)
    so the system is correct on the common cases regardless of parser fidelity.
  * A **heuristic parser** over the corpus's release notes / migration guides that
    mines additional `{symbol -> replacement}` records from the "Deprecation Notes",
    "Removal Notes" and "Upgrade Notes" sections.

Both are merged into a SQLite table queried by exact symbol or last-segment match,
so an extracted symbol like bare `execute` still resolves to `qiskit.execute`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SEED_PATH = Path(__file__).parent / "data" / "known_deprecations.json"
_RELEVANT_DOC_TYPES = {"release_note", "migration_guide"}

# Status ranking for choosing the best record when several match a symbol.
_STATUS_RANK = {"removed": 3, "moved": 3, "changed": 2, "deprecated": 1}

# Ubiquitous method/attribute names that would spuriously match module-level records
# via last-segment matching (e.g. the `.result()` call vs the `qiskit.result` module).
# These still match on a full-symbol basis, just not by trailing segment alone.
_GENERIC_SEGMENTS = {
    "result",
    "run",
    "data",
    "name",
    "measure",
    "draw",
    "copy",
    "counts",
    "config",
    "keys",
    "values",
    "items",
    "get",
    "set",
    "value",
}

# Core APIs that are current in the target version and must NEVER be reported as
# deprecated — a guard against heuristic-parser false positives (e.g. a release note
# mentioning `transpile` in a removal context made the parser flag `transpile`, which
# is the canonical modern entry point).
_CURRENT_ALLOWLIST = {
    "transpile",
    "generate_preset_pass_manager",
    "QuantumCircuit",
    "QuantumRegister",
    "ClassicalRegister",
    "Parameter",
    "ParameterVector",
    "assign_parameters",
    "SparsePauliOp",
    "Statevector",
    "Operator",
    "PassManager",
}


@dataclass
class DeprecationRecord:
    symbol: str
    status: str
    since_version: str | None = None
    removed_in: str | None = None
    replacement: str | None = None
    note: str = ""
    source: str = ""

    @property
    def last_segment(self) -> str:
        return self.symbol.rsplit(".", 1)[-1]


# --------------------------------------------------------------------------- #
# Heuristic release-note parser
# --------------------------------------------------------------------------- #

_HEADER_RE = re.compile(r"^#{2,6}\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*\*\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SINCE_RE = re.compile(r"deprecat\w*\s+(?:in|since|on)\s+Qiskit\s+([0-9]+\.[0-9]+)", re.IGNORECASE)

# Tie the symbol to the removal/deprecation verb so we only record the thing that
# was *actually* removed/deprecated — not the first backticked token in the bullet
# (which is often just contextual, e.g. the class a removed method belongs to).
# First matching pattern wins.
_NOUN = r"(?:method|function|module|class|attribute|property|object|argument|parameter|option)\s+"
_SUBJECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"`([^`]+)`[^`]{0,80}?\b(?:has|have|had)\s+been\s+removed", re.IGNORECASE),
        "removed",
    ),
    (re.compile(r"`([^`]+)`[^`]{0,50}?\b(?:was|were|is|are)\s+removed", re.IGNORECASE), "removed"),
    (re.compile(rf"\bremoved\s+(?:the\s+)?(?:{_NOUN})?`([^`]+)`", re.IGNORECASE), "removed"),
    (
        re.compile(r"`([^`]+)`[^`]{0,80}?\b(?:has|have|had)\s+been\s+deprecated", re.IGNORECASE),
        "deprecated",
    ),
    (
        re.compile(r"`([^`]+)`[^`]{0,50}?\b(?:was|were|is|are)\s+deprecated", re.IGNORECASE),
        "deprecated",
    ),
    (re.compile(rf"\bdeprecated\s+(?:the\s+)?(?:{_NOUN})?`([^`]+)`", re.IGNORECASE), "deprecated"),
]
_REPL_PATTERNS = [
    re.compile(r"(?:use|using)\s+`([^`]+)`\s+(?:instead|as a drop-in)", re.IGNORECASE),
    re.compile(r"`([^`]+)`\s+instead", re.IGNORECASE),
    re.compile(r"(?:replaced by|in favor of)\s+`([^`]+)`", re.IGNORECASE),
]
_RELEVANT_SECTION_KEYWORDS = ("deprecat", "removal", "removed", "upgrade")
_SYMBOL_STOPLIST = {
    "DeprecationWarning",
    "dict",
    "list",
    "None",
    "True",
    "False",
    "{}",
    "tuple",
    "set",
    "str",
    "int",
    "float",
    "bool",
    "Warning",
    "logging",
}


def _clean_token(token: str) -> str:
    return token.strip().rstrip("()").strip()


def _looks_like_symbol(token: str) -> bool:
    token = _clean_token(token)
    if len(token) < 2 or token in _SYMBOL_STOPLIST:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", token))


def _strip_markdown(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text)  # [label](url) -> label
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _extract_subject(bullet: str) -> tuple[str, str] | None:
    """Return (symbol, status) for the API the bullet removes/deprecates, or None."""
    for pattern, status in _SUBJECT_PATTERNS:
        m = pattern.search(bullet)
        if m:
            symbol = _clean_token(m.group(1))
            if _looks_like_symbol(symbol):
                return symbol, status
    return None


def _pick_replacement(bullet: str, subject: str) -> str | None:
    for pattern in _REPL_PATTERNS:
        for cand in pattern.findall(bullet):
            cand = _clean_token(cand)
            if _looks_like_symbol(cand) and cand != subject:
                return cand
    return None


def parse_release_note(text: str, version: str, source: str) -> list[DeprecationRecord]:
    """Best-effort extraction of deprecation/removal records from one note's text."""
    records: list[DeprecationRecord] = []
    in_relevant_section = False
    bullet_lines: list[str] = []

    def flush(bullet: str) -> None:
        bullet = bullet.strip()
        if not bullet:
            return
        subject = _extract_subject(bullet)
        if subject is None:
            return
        symbol, status = subject
        since = _SINCE_RE.search(bullet)
        since_version = since.group(1) if since else None
        records.append(
            DeprecationRecord(
                symbol=symbol,
                status=status,
                # For a removal, since_version is when it was *deprecated* (if stated);
                # the note's own version is when it was removed.
                since_version=since_version
                if status == "removed"
                else (since_version or version or None),
                removed_in=(version or None) if status == "removed" else None,
                replacement=_pick_replacement(bullet, symbol),
                note=_strip_markdown(bullet)[:240],
                source=source,
            )
        )

    for line in text.splitlines():
        header = _HEADER_RE.match(line)
        if header:
            if bullet_lines:
                flush(" ".join(bullet_lines))
                bullet_lines = []
            title = header.group(1).lower()
            in_relevant_section = any(k in title for k in _RELEVANT_SECTION_KEYWORDS)
            continue
        if not in_relevant_section:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            if bullet_lines:
                flush(" ".join(bullet_lines))
            bullet_lines = [bullet.group(1)]
        elif bullet_lines and line.strip():
            bullet_lines.append(line.strip())  # continuation of current bullet
    if bullet_lines:
        flush(" ".join(bullet_lines))

    return records


# --------------------------------------------------------------------------- #
# Seed + store
# --------------------------------------------------------------------------- #


def load_seed_records() -> list[DeprecationRecord]:
    data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    return [DeprecationRecord(**row) for row in data]


class DeprecationStore:
    """SQLite-backed store of deprecation records, queryable by symbol."""

    def __init__(self, db_path: str = "app.db"):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self) -> None:
        parent = Path(self.db_path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deprecations (
                    id            INTEGER PRIMARY KEY,
                    symbol        TEXT NOT NULL,
                    last_segment  TEXT NOT NULL,
                    status        TEXT NOT NULL,
                    since_version TEXT,
                    removed_in    TEXT,
                    replacement   TEXT,
                    note          TEXT,
                    source        TEXT,
                    UNIQUE(symbol, status, source)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dep_symbol ON deprecations(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dep_last ON deprecations(last_segment)")

    def upsert_many(self, records: Iterable[DeprecationRecord]) -> int:
        rows = [
            (
                r.symbol,
                r.last_segment,
                r.status,
                r.since_version,
                r.removed_in,
                r.replacement,
                r.note,
                r.source,
            )
            for r in records
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO deprecations
                    (symbol, last_segment, status, since_version, removed_in,
                     replacement, note, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM deprecations").fetchone()[0]

    def lookup(self, symbols: Iterable[str]) -> list[DeprecationRecord]:
        """Return the best deprecation record for each matching symbol.

        Matches on the full symbol or its last segment, then keeps one record per
        symbol, preferring authoritative (seed) and higher-severity (removed/moved)
        entries.
        """
        keys = {k for k in symbols if k}
        if not keys:
            return []
        # Full-symbol keys match exactly; last-segment matching drops generic tokens.
        segment_keys = keys - _GENERIC_SEGMENTS
        full_ph = ",".join("?" for _ in keys)
        seg_ph = ",".join("?" for _ in segment_keys) if segment_keys else "NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT symbol, status, since_version, removed_in, replacement, note, source
                FROM deprecations
                WHERE symbol IN ({full_ph}) OR last_segment IN ({seg_ph})
                """,
                list(keys) + list(segment_keys),
            ).fetchall()

        best: dict[str, DeprecationRecord] = {}
        for row in rows:
            rec = DeprecationRecord(**dict(row))
            # Never report a known-current core API as deprecated.
            if rec.last_segment in _CURRENT_ALLOWLIST or rec.symbol in _CURRENT_ALLOWLIST:
                continue
            current = best.get(rec.symbol)
            if current is None or _score(rec) > _score(current):
                best[rec.symbol] = rec
        return sorted(best.values(), key=_score, reverse=True)


def _score(rec: DeprecationRecord) -> int:
    seed_bonus = 10 if rec.source == "curated-seed" else 0
    return seed_bonus + _STATUS_RANK.get(rec.status, 0)


def build_deprecation_store(docs_dir: str, db_path: str = "app.db") -> int:
    """Build the deprecations table from the curated seed + parsed corpus notes.

    Offline (no network) — reads only local files. Returns the total row count.
    """
    from src.config import get_settings
    from src.ingestion.loader import QiskitMarkdownLoader

    store = DeprecationStore(db_path)
    store.create()

    records: list[DeprecationRecord] = list(load_seed_records())

    loader = QiskitMarkdownLoader(docs_dir, current_version=get_settings().qiskit_target_version)
    for doc in loader.load(doc_types=_RELEVANT_DOC_TYPES):
        records.extend(
            parse_release_note(doc.content, doc.metadata.get("version", ""), doc.metadata["source"])
        )

    inserted = store.upsert_many(records)
    logger.info("Parsed/seeded %s candidate records; store now holds %s.", inserted, store.count())
    return store.count()
