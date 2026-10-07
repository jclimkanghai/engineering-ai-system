from pipelines.findings.models import Finding, FindingStatus
from pipelines.outputs import build_audit_trail


def _finding():
    return Finding(
        finding_id="F-1",
        title="Missing calculation",
        finding="The calculation is not demonstrated.",
        status=FindingStatus.UNVERIFIED,
        source_evidence_ids=["E-1"],
        requirement_ids=["REQ-1"],
    )


def test_audit_trail_resolves_source_chain():
    context = {
        "evidence": [
            {
                "evidence_id": "E-1",
                "document_id": "DOC-1",
                "revision": "B",
                "locator": "§4",
            }
        ],
        "requirements": [
            {
                "identity": {"requirement_id": "REQ-1"},
                "provenance": {"source_document_id": "DOC-1"},
            }
        ],
        "document_records": [],
    }

    record = build_audit_trail([_finding()], context)[0]

    assert record.finding_id == "F-1"
    assert record.evidence_ids == ["E-1"]
    assert record.unresolved_links == []


def test_audit_trail_marks_unknown_links_for_review():
    record = build_audit_trail(
        [_finding()], {"evidence": [], "requirements": [], "document_records": []}
    )[0]

    assert "evidence:E-1" in record.unresolved_links
    assert "requirement:REQ-1" in record.unresolved_links
    assert record.human_review_required is True
