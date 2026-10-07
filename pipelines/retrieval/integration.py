from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import EvidenceRetrievalEngine
from .models import RetrievalQuery
from .store import PersistentEvidenceStore


def build_engine_from_context(context) -> EvidenceRetrievalEngine:
    store_path = context.request.metadata.get("evidence_store_path", ":memory:")
    engine = EvidenceRetrievalEngine(
        persistent_store=PersistentEvidenceStore(store_path)
    )
    for record in context.get("document_records", []):
        output_files = record.metadata.get("output_files", {})
        markdown = output_files.get("markdown")
        if not markdown or not Path(markdown).exists():
            continue
        engine.add_markdown_file(
            markdown,
            document_id=record.document_id,
            document_number=record.document_number,
            title=record.title,
            revision=record.revision,
            revision_date=record.revision_date,
            issue_status=record.issue_status,
            governing_status=record.governing_status,
            discipline=record.discipline,
            metadata={
                "source_file": record.source_file,
                "source_hash": record.source_hash,
            },
        )
    # Requirement-level evidence is added separately because it may contain
    # structured source text that should remain directly addressable.
    for req in context.get("requirements", []):
        identity = req.get("identity", {})
        provenance = req.get("provenance", {})
        source_text = identity.get("source_text") or req.get("source_text")
        if not source_text:
            continue
        from .models import EvidenceChunk

        eid = identity.get("requirement_id")
        if not eid:
            continue
        if engine.index.get(eid) is None:
            engine.add_chunks(
                [
                    EvidenceChunk(
                        evidence_id=eid,
                        document_id=provenance.get("source_document_id", ""),
                        document_number=provenance.get("source_document_number"),
                        title=provenance.get("source_document_title"),
                        revision=provenance.get("source_revision"),
                        revision_date=provenance.get("source_revision_date"),
                        issue_status=None,
                        governing_status=None,
                        discipline=None,
                        section=provenance.get("source_location"),
                        locator=provenance.get("source_location")
                        or f"requirement={eid}",
                        text=source_text,
                        evidence_class="requirement",
                        metadata={"requirement_id": eid},
                        page=provenance.get("page"),
                    )
                ]
            )
    return engine


def retrieve_for_analysis(context, query: str, **filters: Any) -> dict[str, Any]:
    engine = build_engine_from_context(context)
    request = RetrievalQuery(query=query, **filters)
    bundle = engine.query(request)
    return bundle.to_dict()
