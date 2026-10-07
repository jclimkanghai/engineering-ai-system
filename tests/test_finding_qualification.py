import pytest

from pipelines.findings import EvidenceRef, FindingClass
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import LLMResponse
from pipelines.outputs.engine import coerce_findings
from pipelines.outputs.projections import project_review_comments


def finding(**overrides):
    return {
        "finding_id": "F1",
        "title": "Possible discrepancy",
        "finding": "The calculation requires verification against the governing basis.",
        "status": "unverified",
        "finding_class": "verification_item",
        "qualification": "verification_required",
        "qualification_rationale": "Further substantiation is required.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "Same asset and design stage.",
            "chronology": "current",
            "project_stage": "tender",
            "materiality": "material",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
        "source_evidence_ids": ["E1"],
        "requirement_ids": [],
        "interpretation": "The supplied evidence does not resolve the input basis.",
        "steelman": "A separate controlled calculation may resolve it.",
        "critic": "No failure is demonstrated by the current evidence.",
        "gap": "Verification remains open.",
        "impact": "The acceptance basis is not established.",
        "risk_level": "unknown",
        "risk_dimensions": [],
        "recommendation": "Check the controlled calculation.",
        "human_review_required": True,
        "human_review_reason": "Engineering basis remains unverified.",
        "confidence": "medium",
        "assumptions": [],
        "uncertainties": ["The reviewed package may not contain the full calculation."],
        "conflicts": [],
        **overrides,
    }


def test_requirement_noncompliance_requires_a_cited_requirement_id():
    response = LLMResponse(
        "test", "r1", [finding(finding_class="requirement_noncompliance")]
    )

    accepted, errors, _ = findings_from_response(response, [EvidenceRef("E1")])

    assert accepted == []
    assert any("must cite at least one requirement ID" in error for error in errors)


def test_open_verification_item_is_preserved_as_distinct_from_noncompliance():
    response = LLMResponse("test", "r1", [finding()])

    accepted, errors, _ = findings_from_response(response, [EvidenceRef("E1")])

    assert errors == []
    assert len(accepted) == 1
    assert accepted[0].finding_class == FindingClass.VERIFICATION_ITEM


def test_finding_class_survives_output_projection():
    response = LLMResponse("test", "r1", [finding()])
    accepted, errors, _ = findings_from_response(response, [EvidenceRef("E1")])

    assert errors == []
    output = project_review_comments(coerce_findings(accepted), {})[0]
    assert output.data["finding_class"] == "verification_item"


@pytest.mark.parametrize(
    "qualification",
    ["unknown_insufficient_information", "no_issue", "positive_assurance"],
)
def test_resolved_by_later_evidence_requires_closed_qualification(qualification):
    record = finding(qualification=qualification)
    record["qualification_basis"].update(
        unknown_reason="resolved_by_later_evidence", counter_evidence_ids=["E1"]
    )
    accepted, errors, _ = findings_from_response(
        LLMResponse("test", "r1", [record]), [EvidenceRef("E1")]
    )
    if qualification == "unknown_insufficient_information":
        assert accepted == []
        assert any("Resolved by later evidence" in error for error in errors)
    else:
        assert errors == []
        assert len(accepted) == 1
