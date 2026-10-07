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


def test_finding_accepts_known_evidence():
    engine = FindingEngine([EvidenceRef("E-1", document_id="DOC-1")])
    finding = Finding(
        finding_id="F-1",
        title="Test finding",
        finding="The requirement is not demonstrated.",
        status=FindingStatus.POTENTIAL,
        finding_class=FindingClass.VERIFICATION_ITEM,
        qualification=FindingQualification.VERIFICATION_REQUIRED,
        qualification_rationale="Verification remains open.",
        qualification_basis=QualificationBasis(
            source_authority="established",
            requirement_type="current_project_requirement",
            same_object="matched",
            same_object_basis="Same element.",
            chronology="current",
            project_stage="detailed_design",
            materiality="material",
        ),
        source_evidence_ids=["E-1"],
        risk_level=RiskLevel.MEDIUM,
    )

    result = engine.validate(finding)

    assert result.valid is True


def test_finding_rejects_unknown_evidence():
    engine = FindingEngine([EvidenceRef("E-1")])
    finding = Finding(
        finding_id="F-2",
        title="Unsupported",
        finding="Unsupported claim.",
        status=FindingStatus.UNVERIFIED,
        finding_class=FindingClass.EVIDENCE_LIMITATION,
        qualification=FindingQualification.UNKNOWN,
        qualification_rationale="The source does not identify the supported claim.",
        qualification_basis=QualificationBasis(
            unknown_reason="not_in_review_package"
        ),
        source_evidence_ids=["E-999"],
    )

    result = engine.validate(finding)

    assert result.valid is False
    assert any("Unknown evidence_id" in error for error in result.errors)
