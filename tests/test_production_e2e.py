from pipelines.review import ReviewApproval, StageStatus
from tests.support.review_harness import (
    make_review_fixture,
    stage_data,
    valid_recording_adapter,
)


def test_synthetic_document_reaches_approved_compliance_output(tmp_path):
    adapter = valid_recording_adapter()
    pending = make_review_fixture(
        tmp_path / "pending",
        adapter=adapter,
        review_mode="compliance",
    )
    assert pending.final_status == StageStatus.BLOCKED
    finding = pending.findings[0]
    evidence_id = finding["source_evidence_ids"][0]
    assert adapter.requests[0].evidence[0]["document_id"] == "SYN-DOC-TEST-001"

    outputs = stage_data(pending, "output_projection")["controlled_outputs"]
    assert any(
        evidence_id in output["source_evidence_ids"]
        for records in outputs.values()
        for output in records
    )
    gate = stage_data(pending, "human_review_gate")
    assert gate["approval_status"] == "pending"

    approval = ReviewApproval(
        "reviewer-1", "approved", "2026-09-24T10:00:00Z", gate["output_digest"]
    )
    approved = make_review_fixture(
        tmp_path / "approved",
        adapter=valid_recording_adapter(),
        review_mode="compliance",
        approval=approval,
    )
    assert approved.final_status == StageStatus.COMPLETE
    assert approved.findings[0]["source_evidence_ids"] == [evidence_id]
