from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .models import (
    EvidenceRef,
    Finding,
    FindingClass,
    FindingQualification,
    FindingStatus,
    ProjectStage,
    RequirementType,
    RiskLevel,
    UnknownReason,
)


@dataclass(frozen=True)
class FindingValidation:
    valid: bool
    errors: list[str]
    warnings: list[str]


class FindingEngine:
    """Validate and normalize LLM findings against supplied evidence."""

    def __init__(self, evidence: Iterable[EvidenceRef]) -> None:
        self.evidence = {item.evidence_id: item for item in evidence}

    def validate(self, finding: Finding) -> FindingValidation:
        errors: list[str] = []
        warnings: list[str] = []

        if not finding.finding_id:
            errors.append("finding_id is required")
        if not finding.title:
            errors.append("title is required")
        if not finding.finding:
            errors.append("finding is required")
        if not finding.source_evidence_ids:
            errors.append("at least one source_evidence_id is required")
        if finding.finding_class not in set(FindingClass):
            errors.append(f"Invalid finding_class: {finding.finding_class}")
        if finding.qualification is None:
            errors.append("qualification is required for a new finding")
        elif finding.qualification not in set(FindingQualification):
            errors.append(f"Invalid qualification: {finding.qualification}")
        basis = finding.qualification_basis
        if basis.source_authority not in {
            "established",
            "unresolved",
            "not_applicable",
        }:
            errors.append(f"Invalid qualification source_authority: {basis.source_authority}")
        if basis.requirement_type is not None and basis.requirement_type not in set(
            RequirementType
        ):
            errors.append(f"Invalid requirement_type: {basis.requirement_type}")
        if basis.same_object not in {
            "matched",
            "different_objects",
            "not_applicable",
            "unknown",
        }:
            errors.append(f"Invalid same_object qualification: {basis.same_object}")
        if basis.chronology not in {
            "current",
            "superseded",
            "resolved_by_later_evidence",
            "not_applicable",
            "unknown",
        }:
            errors.append(f"Invalid qualification chronology: {basis.chronology}")
        if basis.project_stage not in set(ProjectStage):
            errors.append(f"Invalid project_stage: {basis.project_stage}")
        if basis.materiality not in {"material", "immaterial", "unknown"}:
            errors.append(f"Invalid qualification materiality: {basis.materiality}")
        if finding.qualification == FindingQualification.DEFICIENCY:
            mandatory_types = {
                RequirementType.MANDATORY_CONTRACTUAL,
                RequirementType.MANDATORY_REGULATORY,
                RequirementType.CURRENT_PROJECT_REQUIREMENT,
                RequirementType.ACCEPTED_PROJECT_DECISION,
            }
            if basis.requirement_type not in mandatory_types:
                errors.append(
                    "A deficiency must establish a mandatory requirement type"
                )
            if basis.source_authority != "established":
                errors.append("A deficiency must establish source authority")
            if basis.same_object not in {"matched", "not_applicable"}:
                errors.append("A deficiency requires a resolved same_object check")
            if basis.same_object == "matched" and not basis.same_object_basis:
                errors.append("A matched same_object check requires its basis")
            if basis.chronology not in {"current", "not_applicable"}:
                errors.append("A deficiency requires a current chronology check")
            if basis.project_stage == "unknown":
                errors.append("A deficiency requires a known project_stage")
            if basis.materiality != "material":
                errors.append("A deficiency must be established as material")
            if finding.finding_class != FindingClass.REQUIREMENT_NONCOMPLIANCE:
                errors.append(
                    "A deficiency must be classified as requirement_noncompliance"
                )
        if finding.qualification is not None:
            closed_outcomes = {
                FindingQualification.NO_ISSUE,
                FindingQualification.POSITIVE_ASSURANCE,
            }
            issue_outcomes = {
                FindingQualification.DEFICIENCY,
                FindingQualification.VERIFICATION_REQUIRED,
                FindingQualification.DESIGN_DEVELOPMENT_ITEM,
                FindingQualification.OPTIMISATION_ITEM,
            }
            if basis.same_object == "different_objects" and finding.qualification in issue_outcomes:
                errors.append(
                    "Different objects cannot be published as a deficiency or unresolved issue"
                )
            if basis.same_object == "unknown" and finding.qualification in issue_outcomes:
                errors.append(
                    "Unresolved same_object comparison must remain UNKNOWN until reconciled"
                )
            if basis.chronology in {"superseded", "resolved_by_later_evidence"} and finding.qualification not in closed_outcomes:
                errors.append(
                    "Superseded or later-resolved evidence cannot support an open formal finding"
                )
            if basis.chronology == "unknown" and finding.qualification in issue_outcomes:
                errors.append(
                    "Unresolved chronology/supersession must remain UNKNOWN before formal publication"
                )
            if basis.project_stage == ProjectStage.UNKNOWN and finding.qualification in issue_outcomes:
                errors.append(
                    "Unknown project stage must remain UNKNOWN before formal publication"
                )
            if basis.materiality == "unknown" and finding.qualification in issue_outcomes:
                errors.append(
                    "Unknown materiality must remain UNKNOWN before formal publication"
                )
            if finding.qualification == FindingQualification.OPTIMISATION_ITEM and basis.project_stage in {
                ProjectStage.IFC,
                ProjectStage.CONSTRUCTION,
                ProjectStage.AS_BUILT,
                ProjectStage.ASSESSMENT_CERTIFICATION,
            }:
                errors.append(
                    "Late-stage scope changes cannot be labelled tender/design optimisation without an accepted change basis"
                )
        if finding.qualification == FindingQualification.UNKNOWN:
            if basis.unknown_reason not in set(UnknownReason):
                errors.append("UNKNOWN qualification requires an unknown_reason")
        if (
            basis.unknown_reason == UnknownReason.RESOLVED_BY_LATER_EVIDENCE
            and finding.qualification not in {
                FindingQualification.NO_ISSUE,
                FindingQualification.POSITIVE_ASSURANCE,
            }
        ):
            errors.append(
                "Resolved by later evidence must use NO_ISSUE or POSITIVE_ASSURANCE, not UNKNOWN"
            )
        if finding.qualification in {
            FindingQualification.NO_ISSUE,
            FindingQualification.POSITIVE_ASSURANCE,
        } and not basis.counter_evidence_ids:
            errors.append(
                "NO_ISSUE and POSITIVE_ASSURANCE require cited counter/confirming evidence"
            )
        if (
            finding.finding_class == FindingClass.TECHNICAL_INCONSISTENCY
            and finding.qualification == FindingQualification.DEFICIENCY
            and basis.same_object == "different_objects"
        ):
            errors.append("A conflict cannot qualify when same_object is different")
        if (
            finding.finding_class == FindingClass.REQUIREMENT_NONCOMPLIANCE
            and not finding.requirement_ids
        ):
            errors.append(
                "Requirement non-compliance must cite at least one requirement ID"
            )

        if any(not item for item in finding.responsibility_assignment_ids):
            errors.append("responsibility_assignment_ids must contain non-empty IDs")
        if finding.output_status not in {
            "open",
            "in_progress",
            "closed",
            "accepted",
            "rejected",
            "unknown",
        }:
            errors.append(f"Invalid output_status: {finding.output_status}")

        for evidence_id in finding.source_evidence_ids:
            if evidence_id not in self.evidence:
                errors.append(f"Unknown evidence_id: {evidence_id}")
        for evidence_id in basis.counter_evidence_ids:
            if evidence_id not in self.evidence:
                errors.append(f"Unknown counter-evidence ID: {evidence_id}")

        if finding.confidence not in {"low", "medium", "high"}:
            errors.append(f"Invalid confidence: {finding.confidence}")

        if finding.risk_level not in set(RiskLevel):
            errors.append(f"Invalid risk_level: {finding.risk_level}")

        safety_terms = re.compile(
            r"\b(safety|structural|stability|fire|explosion|regulatory|permit|life safety|fatal|collapse)\b",
            re.IGNORECASE,
        )
        if safety_terms.search(finding.finding) and not finding.human_review_required:
            errors.append(
                "Safety/regulatory-sensitive finding must require human review"
            )

        if finding.status == FindingStatus.CONFIRMED and finding.confidence == "low":
            warnings.append("Confirmed finding has low confidence")

        if not finding.interpretation:
            warnings.append(
                "No interpretation field supplied; verify that the finding is source-grounded"
            )

        return FindingValidation(not errors, errors, warnings)

    def validate_all(
        self, findings: Iterable[Finding]
    ) -> tuple[list[Finding], list[FindingValidation]]:
        accepted: list[Finding] = []
        validations: list[FindingValidation] = []
        for finding in findings:
            validation = self.validate(finding)
            validations.append(validation)
            if validation.valid:
                accepted.append(finding)
        return accepted, validations

    def normalize(self, finding: Finding) -> Finding:
        """Apply deterministic safety defaults; never invent evidence."""
        if finding.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            finding.human_review_required = True
            if not finding.human_review_reason:
                finding.human_review_reason = (
                    "Material risk requires professional review."
                )
        return finding
