from __future__ import annotations

from collections.abc import Iterable

from pipelines.findings.models import (
    Finding,
    FindingClass,
    FindingQualification,
    FindingStatus,
    QualificationBasis,
    RiskLevel,
)

from .models import AuditTrailRecord, OutputRecord
from .projections import (
    project_compliance_matrix,
    project_decision_log,
    project_executive_summary,
    project_gap_matrix,
    project_review_comments,
    project_revision_changes,
    project_risk_register,
    project_technical_queries,
)


def project_all(findings: Iterable[Finding], context: dict) -> dict:
    finding_list = list(findings)
    outputs: dict[str, list[OutputRecord]] = {
        "review_comments": project_review_comments(finding_list, context),
        "gap_matrix": project_gap_matrix(finding_list, context),
        "compliance_matrix": project_compliance_matrix(finding_list, context),
        "revision_change_log": project_revision_changes(finding_list, context),
        "technical_queries": project_technical_queries(finding_list, context),
        "risk_register": project_risk_register(finding_list, context),
        "decision_log": project_decision_log(finding_list, context),
    }
    outputs["executive_summary"] = [project_executive_summary(outputs)]
    return outputs


def coerce_findings(items: Iterable[Finding | dict]) -> list[Finding]:
    result = []
    for item in items:
        if isinstance(item, Finding):
            result.append(item)
            continue
        values = dict(item)
        values["status"] = FindingStatus(values.get("status", FindingStatus.UNVERIFIED))
        values["risk_level"] = RiskLevel(values.get("risk_level", RiskLevel.UNKNOWN))
        values["finding_class"] = FindingClass(
            values.get("finding_class", FindingClass.UNCLASSIFIED)
        )
        qualification = values.get("qualification")
        values["qualification"] = (
            FindingQualification(qualification) if qualification else None
        )
        basis = values.get("qualification_basis")
        values["qualification_basis"] = (
            QualificationBasis(**basis)
            if isinstance(basis, dict)
            else QualificationBasis()
        )
        result.append(
            Finding(
                **{
                    key: value
                    for key, value in values.items()
                    if key in Finding.__dataclass_fields__
                }
            )
        )
    return result


def build_audit_trail(
    findings: Iterable[Finding], context: dict
) -> list[AuditTrailRecord]:
    evidence = {item.get("evidence_id"): item for item in context.get("evidence", [])}
    requirements = {
        item.get("identity", {}).get("requirement_id"): item
        for item in context.get("requirements", [])
    }
    records = []
    for finding in findings:
        unresolved: list[str] = []
        evidence_rows = []
        for evidence_id in finding.source_evidence_ids:
            row = evidence.get(evidence_id)
            if row is None:
                unresolved.append(f"evidence:{evidence_id}")
            else:
                evidence_rows.append(row)
        for requirement_id in finding.requirement_ids:
            if requirement_id not in requirements:
                unresolved.append(f"requirement:{requirement_id}")
        document_ids = sorted(
            {row.get("document_id") for row in evidence_rows if row.get("document_id")}
        )
        revisions = sorted(
            {row.get("revision") for row in evidence_rows if row.get("revision")}
        )
        locations = sorted(
            {row.get("locator") for row in evidence_rows if row.get("locator")}
        )
        records.append(
            AuditTrailRecord(
                audit_id=f"audit_{finding.finding_id}",
                finding_id=finding.finding_id,
                evidence_ids=list(finding.source_evidence_ids),
                requirement_ids=list(finding.requirement_ids),
                document_ids=document_ids,
                revisions=revisions,
                source_locations=locations,
                final_disposition=finding.output_status,
                unresolved_links=unresolved,
                human_review_required=finding.human_review_required or bool(unresolved),
            )
        )
    return records
