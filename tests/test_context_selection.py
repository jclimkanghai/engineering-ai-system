from pipelines.retrieval.context.engine import EvidenceContextEngine, SelectionPolicy
from pipelines.retrieval.models import EvidenceChunk, RetrievalHit


def _candidate(evidence_id: str, text: str, score: float = 1.0):
    chunk = EvidenceChunk(
        evidence_id=evidence_id,
        document_id="DOC-1",
        document_number=None,
        title=None,
        revision="A",
        revision_date=None,
        issue_status=None,
        governing_status="governing",
        discipline="civil",
        section="Piling",
        locator=evidence_id,
        text=text,
    )
    return chunk, RetrievalHit(evidence_id, score, score, 0.0)


def test_context_selection_enforces_count_and_records_exclusions():
    engine = EvidenceContextEngine(SelectionPolicy(max_evidence=1, max_chars=20))

    packet = engine.build(
        "pile",
        [
            _candidate("E-1", "Pile requirement."),
            _candidate("E-2", "Second pile requirement."),
        ],
    )

    assert len(packet.evidence) == 1
    assert packet.exclusions
    assert packet.evidence[0]["evidence_id"] == "E-1"


def test_empty_context_is_explicit():
    packet = EvidenceContextEngine().build("pile", [])

    assert packet.evidence == []
    assert packet.warnings
