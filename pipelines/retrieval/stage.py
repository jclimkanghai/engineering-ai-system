from __future__ import annotations

from typing import Any

from .integration import build_engine_from_context
from .models import RetrievalQuery


def run_evidence_retrieval(
    context,
    *,
    query: str | None = None,
    top_k: int = 12,
    governing_only: bool = False,
    document_ids: list[str] | None = None,
    disciplines: list[str] | None = None,
) -> dict[str, Any]:
    """Pipeline-safe retrieval stage.

    Query text should normally be supplied by the review-mode adapter. The stage
    deliberately returns an explicit no-query result rather than guessing a
    search question from the document content.
    """
    if not query:
        return {
            "evidence_bundle": {
                "query": "",
                "evidence": [],
                "hits": [],
                "warnings": [
                    "No retrieval query supplied; evidence retrieval not performed."
                ],
                "retrieval_method": "not_requested",
            },
            "evidence_retrieval_status": "NOT_REQUESTED",
        }

    engine = build_engine_from_context(context)
    bundle = engine.query(
        RetrievalQuery(
            query=query,
            document_ids=document_ids or list(context.request.document_ids),
            disciplines=disciplines or [],
            governing_only=governing_only,
            top_k=top_k,
        )
    )
    return {
        "evidence_bundle": bundle.to_dict(),
        "evidence_retrieval_status": "COMPLETE" if bundle.evidence else "NO_MATCH",
    }
