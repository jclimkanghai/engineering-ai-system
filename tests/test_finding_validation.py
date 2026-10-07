from pipelines.findings.engine import FindingEngine
from pipelines.findings.models import (
    EvidenceRef,
    Finding,
    FindingClass,
    FindingQualification,
    FindingStatus,
    QualificationBasis,
    RiskLevel,
)


def test_critical_risk_forces_human_review():
    finding = Finding(
        finding_id="F-3",
        title="Critical issue",
        finding="Potential structural collapse.",
        status=FindingStatus.POTENTIAL,
        source_evidence_ids=["E-1"],
        risk_level=RiskLevel.CRITICAL,
        human_review_required=False,
    )

    normalized = FindingEngine([EvidenceRef("E-1")]).normalize(finding)

    assert normalized.human_review_required is True
    assert normalized.human_review_reason


def test_finding_preserves_accountability_and_output_links():
    finding = Finding(
        finding_id="F-4",
        title="Missing calculation",
        finding="The calculation is not demonstrated.",
        status=FindingStatus.UNVERIFIED,
        finding_class=FindingClass.EVIDENCE_LIMITATION,
        qualification=FindingQualification.UNKNOWN,
        qualification_rationale="The calculation is absent from this review package.",
        qualification_basis=QualificationBasis(
            unknown_reason="not_in_review_package"
        ),
        source_evidence_ids=["E-1"],
        responsibility_assignment_ids=["RA-1"],
        required_action="Submit the calculation.",
        output_status="open",
    )

    result = FindingEngine([EvidenceRef("E-1")]).validate(finding)

    assert result.valid is True
    assert finding.responsibility_assignment_ids == ["RA-1"]
    assert finding.required_action == "Submit the calculation."
