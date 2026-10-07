from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .index import EvidenceIndex
from .models import EvidenceChunk, RetrievalHit, RetrievalQuery
from .store import PersistentEvidenceStore

SemanticScorer = Callable[[str, list[EvidenceChunk]], dict[str, float]]


@dataclass(frozen=True)
class HybridConfig:
    lexical_weight: float = 0.55
    semantic_weight: float = 0.35
    metadata_weight: float = 0.10
    candidate_multiplier: int = 5
    rerank: str = "weighted"


class HybridRetriever:
    """Hybrid retrieval with lexical + semantic + metadata reranking.

    Semantic scoring is optional and injected. The retriever never creates
    embeddings itself, keeping provider/API concerns outside the retrieval core.
    """

    def __init__(
        self,
        store: PersistentEvidenceStore,
        *,
        semantic_scorer: SemanticScorer | None = None,
        config: HybridConfig | None = None,
    ) -> None:
        self.store = store
        self.semantic_scorer = semantic_scorer
        self.config = config or HybridConfig()

    def retrieve(self, query: RetrievalQuery) -> list[RetrievalHit]:
        candidates = self._bounded_candidates(query)
        if not candidates:
            return []
        index = EvidenceIndex(candidates)
        lexical_hits = index.retrieve(
            RetrievalQuery(
                query=query.query,
                top_k=max(query.top_k * self.config.candidate_multiplier, query.top_k),
                min_score=0.0,
                include_context=query.include_context,
            )
        )
        lexical_map = {h.evidence_id: h for h in lexical_hits}
        candidate_ids = set(lexical_map)
        # Metadata-only filters can produce useful candidates even when lexical
        # matching is weak, so include all filtered candidates if the query is short.
        if len(candidate_ids) < query.top_k:
            candidate_ids.update(c.evidence_id for c in candidates[: query.top_k])
        candidate_chunks = [c for c in candidates if c.evidence_id in candidate_ids]
        semantic_scores = (
            self.semantic_scorer(query.query, candidate_chunks)
            if self.semantic_scorer and query.query.strip()
            else {}
        )

        max_lex = max((h.lexical_score for h in lexical_hits), default=1.0) or 1.0
        max_sem = max(semantic_scores.values(), default=1.0) or 1.0
        results: list[RetrievalHit] = []
        for chunk in candidate_chunks:
            lh = lexical_map.get(chunk.evidence_id)
            lex = (lh.lexical_score if lh else 0.0) / max_lex
            sem = float(semantic_scores.get(chunk.evidence_id, 0.0)) / max_sem
            meta, reasons = self._metadata_score(chunk, query)
            score = (
                self.config.lexical_weight * lex
                + self.config.semantic_weight * sem
                + self.config.metadata_weight * meta
            )
            if score >= query.min_score:
                match_reasons = set(lh.match_reasons if lh else ())
                match_reasons.update(reasons)
                results.append(
                    RetrievalHit(
                        chunk.evidence_id, score, lex, sem, tuple(sorted(match_reasons))
                    )
                )
        results.sort(key=lambda h: (-h.score, h.evidence_id))
        return results[: max(1, query.top_k)]

    def _bounded_candidates(self, query: RetrievalQuery) -> list[EvidenceChunk]:
        """Load a bounded retrieval window instead of the whole SQLite corpus."""
        if not query.query.strip():
            return self.store.filtered(query, limit=max(1, query.top_k))
        limit = max(query.top_k * self.config.candidate_multiplier, query.top_k)
        fts_failed = False
        try:
            lexical_ids = [
                evidence_id
                for evidence_id, _score in self.store.lexical_candidates(
                    query.query, limit=limit
                )
            ]
        except Exception:
            lexical_ids = []
            fts_failed = True
        if fts_failed:
            # Preserve correctness for malformed FTS syntax; this is an
            # exceptional path and is preferable to silently missing support.
            return self.store.filtered(query)
        candidates = self.store.filtered(query, evidence_ids=lexical_ids)
        if len(candidates) < query.top_k:
            existing = {chunk.evidence_id for chunk in candidates}
            for chunk in self.store.filtered(query, limit=query.top_k):
                if chunk.evidence_id not in existing:
                    candidates.append(chunk)
                    existing.add(chunk.evidence_id)
                if len(candidates) >= limit:
                    break
        return candidates[:limit]

    @staticmethod
    def _metadata_score(
        chunk: EvidenceChunk, query: RetrievalQuery
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        q = query.query.lower()
        for value, label in (
            (chunk.document_id, "document_id_match"),
            (chunk.document_number, "document_number_match"),
            (chunk.revision, "revision_match"),
            (chunk.section, "section_match"),
        ):
            if value and value.lower() in q:
                score += 0.25
                reasons.append(label)
        if chunk.governing_status in {"governing", "current", "approved"}:
            score += 0.25
            reasons.append("governing_document")
        return min(score, 1.0), reasons
