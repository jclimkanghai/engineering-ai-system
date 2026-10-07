from __future__ import annotations

from .index import EvidenceIndex
from .models import EvidenceBundle, RetrievalQuery


def build_evidence_bundle(
    index: EvidenceIndex, query: RetrievalQuery, semantic_scores=None
) -> EvidenceBundle:
    hits = index.retrieve(query, semantic_scores=semantic_scores)
    evidence = []
    for hit in hits:
        chunk = index.get(hit.evidence_id)
        if chunk is not None:
            evidence.append(chunk.to_dict())
    warnings = []
    if not evidence:
        warnings.append(
            "No evidence matched the retrieval query; analysis must not infer unsupported facts."
        )
    if evidence and all(
        (e.get("governing_status") in {None, "unknown", "superseded"}) for e in evidence
    ):
        warnings.append(
            "Retrieved evidence does not establish governing status; do not treat it as governing without document-control evidence."
        )
    return EvidenceBundle(
        query=query.query,
        evidence=evidence,
        hits=[
            {
                "evidence_id": h.evidence_id,
                "score": h.score,
                "lexical_score": h.lexical_score,
                "semantic_score": h.semantic_score,
                "match_reasons": list(h.match_reasons),
            }
            for h in hits
        ],
        warnings=warnings,
        retrieval_method="hybrid" if semantic_scores else "lexical",
    )
