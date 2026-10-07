from __future__ import annotations

from typing import Any

from .control.graph import DocumentControlGraph
from .control.intelligence import RevisionIntelligence
from .control.models import DocumentControlRecord


def run_document_control(context) -> dict[str, Any]:
    records = [
        DocumentControlRecord(
            document_id=record.document_id,
            document_number=record.document_number,
            title=record.title,
            document_type=record.document_type,
            discipline=record.discipline,
            revision=record.revision,
            issue_status=record.issue_status,
            governing_status=record.governing_status,
            metadata=record.metadata,
        )
        for record in context.get("document_records", [])
    ]
    graph = DocumentControlGraph(records)
    assessments = RevisionIntelligence(graph).assess_many(
        [record.document_id for record in records]
    )
    return {
        "document_control_graph": graph,
        "control_assessments": {
            key: value.to_dict() for key, value in assessments.items()
        },
    }
