from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceClass(StrEnum):
    SOURCE_FACT = "source_fact"
    INTERPRETATION = "interpretation"
    ENGINEERING_JUDGEMENT = "engineering_judgement"
    ASSUMPTION = "assumption"
    RECOMMENDATION = "recommendation"
    UNKNOWN = "unknown"


class FindingStatus(StrEnum):
    CONFIRMED = "confirmed"
    POTENTIAL = "potential"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class FindingClass(StrEnum):
    """Qualification of the issue being reported, separate from risk severity."""

    REQUIREMENT_NONCOMPLIANCE = "requirement_noncompliance"
    TECHNICAL_INCONSISTENCY = "technical_inconsistency"
    VERIFICATION_ITEM = "verification_item"
    EVIDENCE_LIMITATION = "evidence_limitation"
    TENDER_OBSERVATION = "tender_observation"
    RECOMMENDATION = "recommendation"
    UNCLASSIFIED = "unclassified"


class FindingQualification(StrEnum):
    """Disposition of a reviewed candidate, separate from issue class and risk."""

    DEFICIENCY = "deficiency"
    VERIFICATION_REQUIRED = "verification_required"
    DESIGN_DEVELOPMENT_ITEM = "design_development_item"
    OPTIMISATION_ITEM = "optimisation_item"
    OBSERVATION = "observation"
    UNKNOWN = "unknown_insufficient_information"
    NO_ISSUE = "no_issue"
    POSITIVE_ASSURANCE = "positive_assurance"


class UnknownReason(StrEnum):
    ENGINEERING_NOT_VERIFIED = "engineering_not_verified"
    NOT_IN_REVIEW_PACKAGE = "not_in_review_package"
    CONTROLLING_SOURCE_UNAVAILABLE = "controlling_source_unavailable"
    RESOLVED_BY_LATER_EVIDENCE = "resolved_by_later_evidence"
    OTHER = "other"


class RequirementType(StrEnum):
    MANDATORY_CONTRACTUAL = "mandatory_contractual"
    MANDATORY_REGULATORY = "mandatory_regulatory"
    CURRENT_PROJECT_REQUIREMENT = "current_project_requirement"
    ACCEPTED_PROJECT_DECISION = "accepted_project_decision"
    OPTIONAL_SCOPE = "optional_scope"
    TENDER_SCORING_CRITERION = "tender_scoring_criterion"
    DESIGN_GUIDANCE = "design_guidance"
    INDUSTRY_PRACTICE = "industry_practice"
    CONSULTANT_PREFERENCE = "consultant_preference"


class ProjectStage(StrEnum):
    CONCEPT = "concept"
    TENDER = "tender"
    FEED = "feed"
    PRELIMINARY_DESIGN = "preliminary_design"
    DETAILED_DESIGN = "detailed_design"
    FOR_SUBMISSION = "for_submission"
    IFC = "ifc"
    CONSTRUCTION = "construction"
    AS_BUILT = "as_built"
    ASSESSMENT_CERTIFICATION = "assessment_certification"
    UNKNOWN = "unknown"


@dataclass
class QualificationBasis:
    """Evidence-backed checks supporting a finding's qualification."""

    source_authority: str = "unresolved"
    requirement_type: str | None = None
    same_object: str = "unknown"
    same_object_basis: str | None = None
    chronology: str = "unknown"
    project_stage: str = ProjectStage.UNKNOWN
    materiality: str = "unknown"
    counter_evidence_ids: list[str] = field(default_factory=list)
    unknown_reason: str | None = None


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    document_id: str | None = None
    revision: str | None = None
    locator: str | None = None
    excerpt: str | None = None
    evidence_class: EvidenceClass = EvidenceClass.SOURCE_FACT


@dataclass
class Finding:
    finding_id: str
    title: str
    finding: str
    status: FindingStatus
    source_evidence_ids: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    interpretation: str | None = None
    steelman: str | None = None
    critic: str | None = None
    gap: str | None = None
    impact: str | None = None
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    risk_dimensions: list[str] = field(default_factory=list)
    recommendation: str | None = None
    required_action: str | None = None
    responsibility_assignment_ids: list[str] = field(default_factory=list)
    technical_query_id: str | None = None
    risk_record_id: str | None = None
    decision_record_id: str | None = None
    output_status: str = "open"
    human_review_required: bool = True
    human_review_reason: str | None = None
    confidence: str = "medium"
    assumptions: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    finding_class: str = FindingClass.UNCLASSIFIED
    qualification: FindingQualification | None = None
    qualification_rationale: str | None = None
    qualification_basis: QualificationBasis = field(default_factory=QualificationBasis)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
