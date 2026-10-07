from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from .models import EvidenceChunk, RetrievalHit, RetrievalQuery

_TOKEN = re.compile(r"[A-Za-z0-9_./:-]+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class EvidenceIndex:
    """Deterministic local evidence index with BM25-style lexical retrieval.

    The index is intentionally independent of an LLM or hosted vector store.
    A semantic scorer can be layered on later without changing the evidence IDs
    or provenance contract.
    """

    def __init__(self, chunks: Iterable[EvidenceChunk] = ()) -> None:
        self._chunks: dict[str, EvidenceChunk] = {}
        self._term_freq: dict[str, Counter[str]] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)
        self._doc_len: dict[str, int] = {}
        self._add_all(chunks)

    def _add_all(self, chunks: Iterable[EvidenceChunk]) -> None:
        for chunk in chunks:
            self.add(chunk)

    def add(self, chunk: EvidenceChunk) -> None:
        self._chunks[chunk.evidence_id] = chunk
        terms = Counter(_tokens(chunk.text))
        self._term_freq[chunk.evidence_id] = terms
        self._doc_len[chunk.evidence_id] = sum(terms.values()) or 1
        for term in terms:
            self._postings[term].add(chunk.evidence_id)

    def __len__(self) -> int:
        return len(self._chunks)

    def get(self, evidence_id: str) -> EvidenceChunk | None:
        return self._chunks.get(evidence_id)

    def all(self) -> list[EvidenceChunk]:
        return list(self._chunks.values())

    def retrieve(
        self, query: RetrievalQuery, semantic_scores: dict[str, float] | None = None
    ) -> list[RetrievalHit]:
        candidates = self._filtered_candidates(query)
        q_terms = Counter(_tokens(query.query))
        avgdl = sum(self._doc_len[e] for e in candidates) / max(len(candidates), 1)
        n = len(candidates)
        hits: list[RetrievalHit] = []
        for evidence_id in candidates:
            lexical, reasons = self._bm25(evidence_id, q_terms, n, avgdl)
            chunk = self._chunks[evidence_id]
            exact_reasons = list(reasons)
            hay = chunk.text.lower()
            if query.query.strip().lower() and query.query.strip().lower() in hay:
                lexical += 2.0
                exact_reasons.append("exact_phrase")
            for value, label in (
                (chunk.document_id, "document_id"),
                (chunk.document_number, "document_number"),
                (chunk.revision, "revision"),
                (chunk.section, "section"),
            ):
                if value and value.lower() in query.query.lower():
                    lexical += 3.0
                    exact_reasons.append(label)
            semantic = float((semantic_scores or {}).get(evidence_id, 0.0))
            score = lexical + semantic
            if score >= query.min_score:
                hits.append(
                    RetrievalHit(
                        evidence_id,
                        score,
                        lexical,
                        semantic,
                        tuple(sorted(set(exact_reasons))),
                    )
                )
        hits.sort(key=lambda h: (-h.score, h.evidence_id))
        return hits[: max(1, query.top_k)]

    def _filtered_candidates(self, query: RetrievalQuery) -> list[str]:
        out = []
        for eid, c in self._chunks.items():
            if query.document_ids and c.document_id not in query.document_ids:
                continue
            if (
                query.document_numbers
                and (c.document_number or "") not in query.document_numbers
            ):
                continue
            if query.revisions and (c.revision or "") not in query.revisions:
                continue
            if query.disciplines and (c.discipline or "") not in query.disciplines:
                continue
            if query.sections and (c.section or "") not in query.sections:
                continue
            if (
                query.evidence_classes
                and c.evidence_class not in query.evidence_classes
            ):
                continue
            if query.governing_only and c.governing_status not in {
                "governing",
                "current",
                "approved",
            }:
                continue
            if not query.include_context and c.evidence_class == "context":
                continue
            out.append(eid)
        return out

    def _bm25(
        self, evidence_id: str, q_terms: Counter[str], n: int, avgdl: float
    ) -> tuple[float, list[str]]:
        tf = self._term_freq[evidence_id]
        dl = self._doc_len[evidence_id]
        k1, b = 1.2, 0.75
        score = 0.0
        matched: list[str] = []
        for term, qf in q_terms.items():
            if term not in tf:
                continue
            df = len(self._postings.get(term, ()))
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            denom = tf[term] + k1 * (1 - b + b * dl / max(avgdl, 1.0))
            score += idf * (tf[term] * (k1 + 1) / denom) * min(qf, 3)
            matched.append(term)
        return score, matched
