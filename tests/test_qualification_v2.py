from pipelines.findings import (
    EvidenceRef,
    FindingClass,
    FindingQualification,
    FindingStatus,
    QualificationBasis,
    RiskLevel,
)
from pipelines.findings.engine import FindingEngine
from pipelines.findings.models import Finding
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import LLMResponse
from pipelines.llm.schemas import FINDING_SCHEMA


def _finding(**overrides):
    return {
        "finding_id": "F1",
        "title": "Pile verification remains open",
        "finding": "The proposed pile capacity is not established in the reviewed package.",
        "status": "unverified",
        "finding_class": "verification_item",
        "qualification": "verification_required",
        "qualification_rationale": "Detailed design substantiation is required before acceptance.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "Same berth pile option and design stage.",
            "chronology": "current",
            "project_stage": "tender",
            "materiality": "material",
            "counter_evidence_ids": ["E2"],
            "unknown_reason": None,
        },
        "source_evidence_ids": ["E1"],
        "requirement_ids": ["REQ1"],
        "interpretation": None,
        "steelman": "The contractor may submit final design checks after award.",
        "critic": "No capacity calculation is included in this review package.",
        "gap": None,
        "impact": None,
        "risk_level": "high",
        "risk_dimensions": [],
        "recommendation": None,
        "human_review_required": True,
        "human_review_reason": "Engineering acceptance is not established.",
        "confidence": "medium",
        "assumptions": [],
        "uncertainties": [],
        "conflicts": [],
        **overrides,
    }


def _engine():
    return FindingEngine([EvidenceRef("E1"), EvidenceRef("E2")])


def test_new_response_schema_requires_qualification_and_basis():
    item = FINDING_SCHEMA["properties"]["findings"]["items"]
    assert "qualification" in item["required"]
    assert "qualification_basis" in item["required"]
    assert set(item["properties"]["qualification"]["enum"]) == {
        "deficiency",
        "verification_required",
        "design_development_item",
        "optimisation_item",
        "observation",
        "unknown_insufficient_information",
        "no_issue",
        "positive_assurance",
    }


def test_deficiency_requires_mandatory_requirement_and_resolved_gate_checks():
    item = _finding(
        finding_class="requirement_noncompliance",
        qualification="deficiency",
        qualification_basis={
            "source_authority": "established",
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
    response = LLMResponse("test", "r1", [item])

    findings, errors, _ = findings_from_response(
        response, [EvidenceRef("E1"), EvidenceRef("E2")],
        allowed_requirement_ids={"REQ1"},
    )

    assert findings == []
    assert any("mandatory requirement type" in error for error in errors)
    assert any("same_object" in error for error in errors)
    assert any("chronology" in error for error in errors)
    assert any("project_stage" in error for error in errors)


def test_matched_current_deficiency_is_valid_and_severity_stays_independent():
    item = _finding(
        finding_class="requirement_noncompliance",
        qualification="deficiency",
        qualification_basis={
            "source_authority": "established",
            "requirement_type": "mandatory_contractual",
            "same_object": "matched",
            "same_object_basis": "Same pile, location and revision.",
            "chronology": "current",
            "project_stage": "detailed_design",
            "materiality": "material",
            "counter_evidence_ids": ["E2"],
            "unknown_reason": None,
        },
    )
    response = LLMResponse("test", "r1", [item])

    findings, errors, _ = findings_from_response(
        response, [EvidenceRef("E1"), EvidenceRef("E2")],
        allowed_requirement_ids={"REQ1"},
    )

    assert errors == []
    assert findings[0].qualification == FindingQualification.DEFICIENCY
    assert findings[0].risk_level == RiskLevel.HIGH


def test_unknown_requires_reason_and_distinguishes_review_package_absence():
    item = _finding(
        qualification="unknown_insufficient_information",
        qualification_basis={
            "source_authority": "unresolved",
            "requirement_type": None,
            "same_object": "not_applicable",
            "same_object_basis": None,
            "chronology": "unknown",
            "project_stage": "unknown",
            "materiality": "unknown",
            "counter_evidence_ids": [],
            "unknown_reason": "not_in_review_package",
        },
    )
    response = LLMResponse("test", "r1", [item])

    findings, errors, _ = findings_from_response(response, [EvidenceRef("E1")])

    assert errors == []
    assert findings[0].qualification == FindingQualification.UNKNOWN
    assert findings[0].qualification_basis.unknown_reason == "not_in_review_package"


def test_no_issue_requires_cited_counter_evidence_and_is_not_formal_finding():
    basis = QualificationBasis(
        source_authority="established",
        requirement_type=None,
        same_object="matched",
        same_object_basis="Same load case, location and revision.",
        chronology="current",
        project_stage="detailed_design",
        materiality="immaterial",
        counter_evidence_ids=["E2"],
    )
    finding = Finding(
        finding_id="F1",
        title="Loading values are location-specific",
        finding="10 kPa and 20 kPa apply to different identified areas.",
        status=FindingStatus.CONFIRMED,
        finding_class=FindingClass.TECHNICAL_INCONSISTENCY,
        qualification=FindingQualification.NO_ISSUE,
        qualification_rationale="The cited load plan maps the values to distinct locations.",
        qualification_basis=basis,
        source_evidence_ids=["E1", "E2"],
        risk_level=RiskLevel.LOW,
    )

    result = _engine().validate(finding)

    assert result.valid
    assert finding.qualification not in {
        FindingQualification.DEFICIENCY,
        FindingQualification.VERIFICATION_REQUIRED,
    }


def test_rejects_unresolved_or_unknown_counter_evidence_ids():
    item = _finding(
        qualification_basis={
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "Same asset.",
            "chronology": "current",
            "project_stage": "tender",
            "materiality": "material",
            "counter_evidence_ids": ["E404"],
            "unknown_reason": None,
        }
    )
    finding = Finding(
        finding_id="F1",
        title="Candidate",
        finding="Candidate claim.",
        status=FindingStatus.UNVERIFIED,
        finding_class=FindingClass.VERIFICATION_ITEM,
        qualification=FindingQualification.VERIFICATION_REQUIRED,
        qualification_rationale="Need verification.",
        qualification_basis=QualificationBasis(**item["qualification_basis"]),
        source_evidence_ids=["E1"],
        requirement_ids=["REQ1"],
    )

    result = _engine().validate(finding)

    assert not result.valid
    assert any("Unknown counter-evidence ID" in error for error in result.errors)


def test_same_object_or_chronology_mismatch_cannot_publish_open_finding():
    for basis_change, expected in (
        ({"same_object": "different_objects"}, "Different objects"),
        ({"same_object": "unknown"}, "same_object comparison"),
        ({"chronology": "superseded"}, "Superseded or later-resolved"),
        ({"chronology": "unknown"}, "chronology/supersession"),
    ):
        item = _finding()
        item["qualification_basis"].update(basis_change)
        finding, errors, _ = findings_from_response(
            LLMResponse("test", "r1", [item]), [EvidenceRef("E1"), EvidenceRef("E2")], {"REQ1"}
        )
        assert finding == []
        assert any(expected in error for error in errors)


def test_unknown_stage_or_materiality_cannot_pass_as_formal_verification():
    for basis_change, expected in (
        ({"project_stage": "unknown"}, "Unknown project stage"),
        ({"materiality": "unknown"}, "Unknown materiality"),
    ):
        item = _finding()
        item["qualification_basis"].update(basis_change)
        _, errors, _ = findings_from_response(
            LLMResponse("test", "r1", [item]), [EvidenceRef("E1"), EvidenceRef("E2")], {"REQ1"}
        )
        assert any(expected in error for error in errors)


def test_late_stage_optimisation_needs_accepted_change_classification():
    item = _finding(
        qualification="optimisation_item",
        qualification_basis={
            "source_authority": "established",
            "requirement_type": "design_guidance",
            "same_object": "matched",
            "same_object_basis": "Same wharf and pile system.",
            "chronology": "current",
            "project_stage": "construction",
            "materiality": "material",
            "counter_evidence_ids": ["E2"],
            "unknown_reason": None,
        },
    )
    _, errors, _ = findings_from_response(
        LLMResponse("test", "r1", [item]), [EvidenceRef("E1"), EvidenceRef("E2")], {"REQ1"}
    )
    assert any("Late-stage scope changes" in error for error in errors)


def test_superseded_issue_can_be_closed_with_later_counter_evidence():
    item = _finding(
        qualification="no_issue",
        qualification_rationale="Later controlled evidence resolves the earlier discrepancy.",
        qualification_basis={
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "Same load case, location and revision.",
            "chronology": "resolved_by_later_evidence",
            "project_stage": "detailed_design",
            "materiality": "immaterial",
            "counter_evidence_ids": ["E2"],
            "unknown_reason": None,
        },
    )
    findings, errors, _ = findings_from_response(
        LLMResponse("test", "r1", [item]), [EvidenceRef("E1"), EvidenceRef("E2")], {"REQ1"}
    )
    assert not errors
    assert findings[0].qualification == FindingQualification.NO_ISSUE


def test_legacy_finding_can_be_loaded_without_new_qualification_fields():
    legacy = Finding(
        finding_id="legacy",
        title="Imported historical finding",
        finding="Historical content.",
        status=FindingStatus.UNVERIFIED,
        source_evidence_ids=["E1"],
    )

    assert legacy.qualification is None
