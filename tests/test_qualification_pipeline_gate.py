from copy import deepcopy

from pipelines.review.integration import finding_qualification_gate


def candidate(**overrides):
    return {
        "finding_id": "F1",
        "title": "Potential pile discrepancy",
        "finding": "The proposed pile termination remains unverified.",
        "status": "unverified",
        "finding_class": "verification_item",
        "qualification": "verification_required",
        "qualification_rationale": "Final design verification is required.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "Same berth, pile type and design stage.",
            "chronology": "current",
            "project_stage": "tender",
            "materiality": "material",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
        "source_evidence_ids": ["E1"],
        "requirement_ids": ["REQ1"],
        "risk_level": "high",
        "human_review_required": True,
        "confidence": "medium",
        "assumptions": [],
        "uncertainties": [],
        "conflicts": [],
        **overrides,
    }


def context(finding):
    return {
        "findings": [deepcopy(finding)],
        "evidence": [{"evidence_id": "E1", "text": "Pile design requires verification."}],
        "requirements": [
            {"identity": {"requirement_id": "REQ1", "source_text": "Final design shall be verified."}}
        ],
    }


def test_qualification_gate_preserves_valid_candidate_and_trace():
    result = finding_qualification_gate(context(candidate()))

    assert result["finding_qualification_gate"]["status"] == "PASS"
    assert len(result["findings"]) == 1
    assert result["qualification_trace"][0]["qualification"] == "verification_required"


def test_qualification_gate_blocks_unresolved_deficiency_before_registry():
    bad = candidate(
        finding_class="requirement_noncompliance",
        qualification="deficiency",
        qualification_basis={
            "source_authority": "unresolved",
            "requirement_type": "optional_scope",
            "same_object": "unknown",
            "same_object_basis": None,
            "chronology": "unknown",
            "project_stage": "unknown",
            "materiality": "unknown",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
    )

    result = finding_qualification_gate(context(bad))

    assert result["finding_qualification_gate"]["status"] == "BLOCKED"
    assert result["findings"] == []
    assert result["finding_validation_errors"]
    assert result["qualification_trace"][0]["finding_id"] == "F1"


def test_no_issue_is_retained_in_review_trace_but_not_formal_finding_list():
    no_issue = candidate(
        qualification="no_issue",
        qualification_rationale="The values apply to different locations.",
        qualification_basis={
            "source_authority": "established",
            "requirement_type": None,
            "same_object": "different_objects",
            "same_object_basis": "The two locations have separate loading schedules.",
            "chronology": "current",
            "project_stage": "detailed_design",
            "materiality": "immaterial",
            "counter_evidence_ids": ["E1"],
            "unknown_reason": None,
        },
    )

    result = finding_qualification_gate(context(no_issue))

    assert result["finding_qualification_gate"]["status"] == "PASS"
    assert result["findings"] == []
    assert result["qualification_trace"][0]["qualification"] == "no_issue"
