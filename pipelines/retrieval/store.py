from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import EvidenceChunk, RetrievalQuery


class PersistentEvidenceStore:
    """SQLite-backed evidence store preserving immutable evidence IDs and provenance."""

    _UPSERT_SQL = """
        INSERT INTO evidence(
            evidence_id, document_id, document_number, title, revision,
            revision_date, issue_status, governing_status, discipline,
            section, locator, text, evidence_class, metadata_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(evidence_id) DO UPDATE SET
            document_id=excluded.document_id,
            document_number=excluded.document_number,
            title=excluded.title,
            revision=excluded.revision,
            revision_date=excluded.revision_date,
            issue_status=excluded.issue_status,
            governing_status=excluded.governing_status,
            discipline=excluded.discipline,
            section=excluded.section,
            locator=excluded.locator,
            text=excluded.text,
            evidence_class=excluded.evidence_class,
            metadata_json=excluded.metadata_json,
            updated_at=CURRENT_TIMESTAMP
    """

    def __init__(self, path: str | Path = "evidence_store.sqlite3") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                document_number TEXT,
                title TEXT,
                revision TEXT,
                revision_date TEXT,
                issue_status TEXT,
                governing_status TEXT,
                discipline TEXT,
                section TEXT,
                locator TEXT NOT NULL,
                text TEXT NOT NULL,
                evidence_class TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_document ON evidence(document_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_docnum ON evidence(document_number);
            CREATE INDEX IF NOT EXISTS idx_evidence_revision ON evidence(revision);
            CREATE INDEX IF NOT EXISTS idx_evidence_discipline ON evidence(discipline);
            CREATE INDEX IF NOT EXISTS idx_evidence_governing ON evidence(governing_status);
            CREATE INDEX IF NOT EXISTS idx_evidence_class ON evidence(evidence_class);
            CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
                evidence_id UNINDEXED,
                text,
                content='evidence',
                content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS evidence_ai AFTER INSERT ON evidence BEGIN
                INSERT INTO evidence_fts(rowid, evidence_id, text) VALUES (new.rowid, new.evidence_id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS evidence_ad AFTER DELETE ON evidence BEGIN
                INSERT INTO evidence_fts(evidence_fts, rowid, evidence_id, text) VALUES ('delete', old.rowid, old.evidence_id, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS evidence_au AFTER UPDATE ON evidence BEGIN
                INSERT INTO evidence_fts(evidence_fts, rowid, evidence_id, text) VALUES ('delete', old.rowid, old.evidence_id, old.text);
                INSERT INTO evidence_fts(rowid, evidence_id, text) VALUES (new.rowid, new.evidence_id, new.text);
            END;
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, chunk: EvidenceChunk) -> None:
        self._conn.execute(self._UPSERT_SQL, self._row_values(chunk))
        self._conn.commit()

    def upsert_many(self, chunks: Iterable[EvidenceChunk]) -> int:
        count = 0

        def rows():
            nonlocal count
            for chunk in chunks:
                count += 1
                yield self._row_values(chunk)

        self._conn.executemany(self._UPSERT_SQL, rows())
        self._conn.commit()
        return count

    @staticmethod
    def _row_values(chunk: EvidenceChunk) -> tuple[object, ...]:
        metadata = dict(chunk.metadata)
        if chunk.page is not None:
            metadata.setdefault("page", chunk.page)
        return (
            chunk.evidence_id,
            chunk.document_id,
            chunk.document_number,
            chunk.title,
            chunk.revision,
            chunk.revision_date,
            chunk.issue_status,
            chunk.governing_status,
            chunk.discipline,
            chunk.section,
            chunk.locator,
            chunk.text,
            chunk.evidence_class,
            json.dumps(metadata, sort_keys=True),
        )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> EvidenceChunk:
        metadata = json.loads(row["metadata_json"] or "{}")
        page = metadata.get("page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        return EvidenceChunk(
            evidence_id=row["evidence_id"],
            document_id=row["document_id"],
            document_number=row["document_number"],
            title=row["title"],
            revision=row["revision"],
            revision_date=row["revision_date"],
            issue_status=row["issue_status"],
            governing_status=row["governing_status"],
            discipline=row["discipline"],
            section=row["section"],
            locator=row["locator"],
            text=row["text"],
            evidence_class=row["evidence_class"],
            metadata=metadata,
            page=page,
        )

    def get(self, evidence_id: str) -> EvidenceChunk | None:
        row = self._conn.execute(
            "SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return self._row_to_chunk(row) if row else None

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])

    def all(self) -> list[EvidenceChunk]:
        return [
            self._row_to_chunk(r)
            for r in self._conn.execute("SELECT * FROM evidence ORDER BY evidence_id")
        ]

    def filtered(
        self,
        query: RetrievalQuery,
        *,
        limit: int | None = None,
        evidence_ids: list[str] | None = None,
    ) -> list[EvidenceChunk]:
        clauses = []
        params: list[str] = []

        def add_in(column: str, values: list[str]) -> None:
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)

        add_in("evidence_id", evidence_ids or [])
        add_in("document_id", query.document_ids)
        add_in("document_number", query.document_numbers)
        add_in("revision", query.revisions)
        add_in("discipline", query.disciplines)
        add_in("section", query.sections)
        add_in("evidence_class", query.evidence_classes)
        if query.governing_only:
            clauses.append("governing_status IN ('governing','current','approved')")
        if not query.include_context:
            clauses.append("evidence_class <> 'context'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = "SELECT * FROM evidence" + where + " ORDER BY evidence_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(str(max(0, int(limit))))
        return [self._row_to_chunk(r) for r in self._conn.execute(sql, params)]

    def lexical_candidates(
        self, query: str, limit: int = 100
    ) -> list[tuple[str, float]]:
        """Use SQLite FTS5 when available; returns BM25-like rank values."""
        if not query.strip():
            return []
        rows = self._conn.execute(
            "SELECT evidence_id, bm25(evidence_fts) AS rank FROM evidence_fts WHERE evidence_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        # SQLite bm25 is lower-is-better; convert to a bounded positive relevance.
        return [
            (r["evidence_id"], 1.0 / (1.0 + max(float(r["rank"]), 0.0))) for r in rows
        ]
