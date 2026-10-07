from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .bundle import build_evidence_bundle
from .chunker import chunk_file
from .hybrid import HybridRetriever
from .index import EvidenceIndex
from .models import EvidenceBundle, EvidenceChunk, RetrievalQuery
from .store import PersistentEvidenceStore


class EvidenceRetrievalEngine:
    """Build and query a provenance-preserving engineering evidence index."""

    def __init__(
        self,
        semantic_scorer: Callable[[str, list[EvidenceChunk]], dict[str, float]]
        | None = None,
        persistent_store: PersistentEvidenceStore | None = None,
    ):
        self.index = EvidenceIndex()
        self.semantic_scorer = semantic_scorer
        self.persistent_store = persistent_store or PersistentEvidenceStore(":memory:")

    def add_chunks(self, chunks: list[EvidenceChunk]) -> None:
        for chunk in chunks:
            self.index.add(chunk)
        self.persistent_store.upsert_many(chunks)

    def add_markdown_file(
        self, path: str | Path, **metadata: Any
    ) -> list[EvidenceChunk]:
        chunks = chunk_file(path, **metadata)
        self.add_chunks(chunks)
        return chunks

    def query(self, request: RetrievalQuery) -> EvidenceBundle:
        if self.persistent_store.count():
            hits = HybridRetriever(
                self.persistent_store,
                semantic_scorer=self.semantic_scorer,
            ).retrieve(request)
            # The persistent store may outlive this engine instance. Resolve hits
            # from the store itself so a reopened database does not return an
            # empty evidence bundle just because the in-memory index is fresh.
            evidence = []
            for hit in hits:
                chunk = self.persistent_store.get(hit.evidence_id)
                if chunk is not None:
                    evidence.append(chunk.to_dict())
            return EvidenceBundle(
                query=request.query,
                evidence=evidence,
                hits=[
                    {
                        "evidence_id": hit.evidence_id,
                        "score": hit.score,
                        "lexical_score": hit.lexical_score,
                        "semantic_score": hit.semantic_score,
                        "match_reasons": list(hit.match_reasons),
                    }
                    for hit in hits
                ],
                warnings=[]
                if evidence
                else [
                    "No evidence matched the retrieval query; analysis must not infer unsupported facts."
                ],
                retrieval_method="hybrid",
            )
        semantic_scores = None
        if self.semantic_scorer and request.query.strip():
            candidates = self.index.all()
            semantic_scores = self.semantic_scorer(request.query, candidates)
        return build_evidence_bundle(
            self.index, request, semantic_scores=semantic_scores
        )

    def query_for_requirement(
        self, requirement: dict[str, Any], **kwargs: Any
    ) -> EvidenceBundle:
        provenance = requirement.get("provenance", {})
        source = requirement.get("source", {})
        query_text = requirement.get("source_text") or requirement.get("text") or ""
        document_id = provenance.get("source_document_id") or source.get("document_id")
        document_number = provenance.get("source_document_number") or source.get(
            "document_number"
        )
        request = RetrievalQuery(
            query=query_text,
            document_ids=[document_id] if document_id else [],
            document_numbers=[document_number] if document_number else [],
            top_k=kwargs.pop("top_k", 8),
            **kwargs,
        )
        return self.query(request)
