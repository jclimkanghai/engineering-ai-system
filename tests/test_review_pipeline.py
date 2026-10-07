import pytest

from pipelines.review import ReviewApproval, ReviewRequest, StageStatus
from pipelines.review.approval import review_output_digest
from pipelines.review.production import build_production_pipeline
from tests.support.review_harness import (
    RecordingAdapter,
    make_review_fixture,
    response_for_request,
    stage_data,
    valid_recording_adapter,
)


def test_unknown_review_mode_is_rejected():
    with pytest.raises(ValueError):
        build_production_pipeline("unknown")


def test_missing_registry_document_blocks():
    result = build_production_pipeline("truth").run(
        ReviewRequest(document_ids=["MISSING"], require_human_gate=False)
    )

    assert result.final_status in {StageStatus.BLOCKED, StageStatus.FAILED}


def test_allin_contains_qa_and_human_gate():
    pipeline = build_production_pipeline("allin")

    assert pipeline.stage_ids[-5:] == [
        "responsibility_assignment",
        "output_projection",
        "audit_trail",
        "engineering_qa",
        "human_review_gate",
    ]


def test_document_control_intelligence_stage_publishes_assessments():
    from types import SimpleNamespace

    from pipelines.retrieval.control_stage import run_document_control
    from pipelines.review.context import ReviewContext

    context = ReviewContext(
        ReviewRequest(document_ids=["DOC-1"], require_human_gate=False)
    )
    context.put(
        "document_records",
        [
            SimpleNamespace(
                document_id="DOC-1",
                document_number="D-1",
                title="Spec",
                document_type="spec",
                discipline="process",
                revision="A",
                issue_status="issued",
                governing_status="governing",
                metadata={},
            )
        ],
    )

    result = run_document_control(context)

    assert result["control_assessments"]["DOC-1"]["governing_status"] == "governing"


def test_required_gate_accepts_only_matching_approval(tmp_path):
    pending = make_review_fixture(tmp_path / "pending", valid_recording_adapter())
    gate = stage_data(pending, "human_review_gate")
    assert pending.final_status == StageStatus.BLOCKED
    assert gate["approval_status"] == "pending"
    assert gate["output_digest"]

    approved = make_review_fixture(
        tmp_path / "approved",
        valid_recording_adapter(),
        approval=ReviewApproval(
            "reviewer-1", "approved", "2026-09-24T10:00:00Z", gate["output_digest"]
        ),
    )
    assert approved.final_status == StageStatus.COMPLETE


def test_stale_or_rejected_approval_remains_blocked(tmp_path):
    for name, decision, digest in (
        ("stale", "approved", "stale-digest"),
        ("rejected", "rejected", "placeholder"),
    ):
        pending = make_review_fixture(
            tmp_path / f"{name}-pending", valid_recording_adapter()
        )
        output_digest = stage_data(pending, "human_review_gate")["output_digest"]
        approval_digest = digest if name == "stale" else output_digest
        result = make_review_fixture(
            tmp_path / name,
            valid_recording_adapter(),
            approval=ReviewApproval(
                "reviewer-1", decision, "2026-09-24T10:00:00Z", approval_digest
            ),
        )
        assert result.final_status == StageStatus.BLOCKED


def test_malformed_approval_remains_blocked(tmp_path):
    for index, approval in enumerate(
        (
            ReviewApproval(" ", "approved", "2026-09-24T10:00:00Z", "digest"),
            ReviewApproval("reviewer-1", "approved", "not-a-date", "digest"),
        )
    ):
        result = make_review_fixture(
            tmp_path / str(index), valid_recording_adapter(), approval=approval
        )
        assert result.final_status == StageStatus.BLOCKED
        assert stage_data(result, "human_review_gate")["approval_status"] == "invalid"


def test_disabling_human_gate_discloses_not_required(tmp_path):
    result = make_review_fixture(
        tmp_path,
        valid_recording_adapter(),
        require_human_gate=False,
    )
    assert result.final_status == StageStatus.COMPLETE
    assert stage_data(result, "human_review_gate")["approval_status"] == "not_required"


def test_output_digest_is_canonical_and_tracks_changes():
    left = {
        "findings": [{"id": "F-1", "status": "confirmed"}],
        "controlled_outputs": {"a": 1},
    }
    reordered = {
        "controlled_outputs": {"a": 1},
        "findings": [{"status": "confirmed", "id": "F-1"}],
    }
    changed = {
        "findings": [{"id": "F-1", "status": "contradicted"}],
        "controlled_outputs": {"a": 1},
    }
    assert review_output_digest(left) == review_output_digest(reordered)
    assert review_output_digest(left) != review_output_digest(changed)


def test_output_digest_binds_truth_assessments_and_coverage():
    baseline = {
        "truth_assessments": [{"fact": "synthetic fact"}],
        "truth_coverage": [{"status": "evidence_retrieved"}],
    }
    changed_assessment = {
        "truth_assessments": [{"fact": "changed synthetic fact"}],
        "truth_coverage": [{"status": "evidence_retrieved"}],
    }
    changed_coverage = {
        "truth_assessments": [{"fact": "synthetic fact"}],
        "truth_coverage": [{"status": "insufficient_evidence"}],
    }

    assert review_output_digest(baseline) != review_output_digest(changed_assessment)
    assert review_output_digest(baseline) != review_output_digest(changed_coverage)


def test_qa_failure_blocks_even_when_human_gate_is_disabled(tmp_path):
    def invalid_response(request):
        response = response_for_request(request)
        response.findings[0]["requirement_ids"] = ["SYN-UNKNOWN-REQ"]
        return response

    result = make_review_fixture(
        tmp_path,
        RecordingAdapter(invalid_response),
        require_human_gate=False,
    )

    assert stage_data(result, "engineering_qa")["qa"]["status"] == "BLOCKED"
    assert result.final_status == StageStatus.BLOCKED


def test_changed_unassessed_requirement_invalidates_prior_approval(tmp_path):
    pending = make_review_fixture(
        tmp_path / "original",
        valid_recording_adapter(),
        include_evidence=False,
    )
    digest = stage_data(pending, "human_review_gate")["output_digest"]
    approved = make_review_fixture(
        tmp_path / "changed",
        valid_recording_adapter(),
        include_evidence=False,
        requirement_text=(
            "Supplier shall perform a pressure test on the synthetic pressure line."
        ),
        approval=ReviewApproval(
            "reviewer-1", "approved", "2026-09-24T10:00:00Z", digest
        ),
    )

    assert approved.final_status == StageStatus.BLOCKED
    assert stage_data(approved, "human_review_gate")["approval_status"] == "stale"
