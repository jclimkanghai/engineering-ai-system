from pipelines.retrieval.control.graph import DocumentControlGraph
from pipelines.retrieval.control.intelligence import RevisionIntelligence
from pipelines.retrieval.control.models import (
    ControlRelationType,
    DocumentControlRecord,
    DocumentControlRelation,
)


def test_latest_revision_is_not_governing_without_explicit_status():
    graph = DocumentControlGraph(
        [
            DocumentControlRecord(
                document_id="DOC-A",
                document_number="SPEC-1",
                revision="A",
                governing_status="governing",
            ),
            DocumentControlRecord(
                document_id="DOC-B",
                document_number="SPEC-1",
                revision="B",
                governing_status="unknown",
            ),
        ]
    )

    assessment = RevisionIntelligence(graph).assess("DOC-B")

    assert assessment.governing_status == "unknown"
    assert assessment.active_revision == "B"


def test_explicit_control_relation_is_preserved():
    graph = DocumentControlGraph(
        [
            DocumentControlRecord(document_id="DOC-A", revision="A"),
            DocumentControlRecord(document_id="DOC-B", revision="B"),
        ]
    )
    graph.add_relation(
        DocumentControlRelation(
            source_document_id="DOC-B",
            target_document_id="DOC-A",
            relation_type=ControlRelationType.SUPERSEDES,
        )
    )

    relations = graph.outgoing("DOC-B")

    assert relations[0].relation_type == ControlRelationType.SUPERSEDES
    assert relations[0].target_document_id == "DOC-A"


def test_unknown_document_assessment_is_explicit():
    assessment = RevisionIntelligence(DocumentControlGraph()).assess("MISSING")

    assert assessment.governing_status == "unknown"
    assert assessment.conflict is True
    assert assessment.warnings
