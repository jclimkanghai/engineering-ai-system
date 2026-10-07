from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from pipelines.findings.models import Finding, FindingStatus, RiskLevel

from .models import ExecutiveSummary, OutputRecord


def _id(output_type: str, finding_id: str | None) -> str:
    raw = f"{output_type}|{finding_id or 'review'}"
    return "out_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _record(
    output_type: str, finding: Finding, data: dict[str, Any], *, status: str = "open"
) -> OutputRecord:
    return OutputRecord(
        output_id=_id(output_type, finding.finding_id),
        output_type=output_type,
        finding_id=finding.finding_id,
        status=status,
        source_evidence_ids=list(finding.source_evidence_ids),
        requirement_ids=list(finding.requirement_ids),
        data=data,
        human_review_required=finding.human_review_required,
    )


def _qualification_data(finding: Finding) -> dict[str, Any]:
    return {
        "qualification": (
            finding.qualification.value if finding.qualification is not None else None
        ),
        "qualification_rationale": finding.qualification_rationale,
        "qualification_basis": asdict(finding.qualification_basis),
    }


def project_review_comments(
    findings: Iterable[Finding], _context: dict[str, Any]
) -> list[OutputRecord]:
    return [
        _record(
            "review_comment",
            finding,
            {
                "reference": finding.metadata.get("reference"),
                "observation": finding.finding,
                "finding_class": finding.finding_class.value,
                **_qualification_data(finding),
                "impact": finding.impact,
                "risk_level": finding.risk_level.value,
                "responsibility_assignment_ids": list(
                    finding.responsibility_assignment_ids
                ),
                "required_action": finding.required_action or finding.recommendation,
            },
            status=finding.output_status,
        )
        for finding in findings
    ]


def project_gap_matrix(
    findings: Iterable[Finding], _context: dict[str, Any]
) -> list[OutputRecord]:
    records = []
    for finding in findings:
        expected = finding.metadata.get("expected_evidence_ids", [])
        if not isinstance(expected, (list, tuple, set)):
            expected = []
        records.append(
            _record(
                "gap_matrix",
                finding,
                {
                    "gap": finding.gap or finding.finding,
                    "finding_class": finding.finding_class.value,
                    **_qualification_data(finding),
                    "expected_evidence": list(expected),
                    "available_evidence": list(finding.source_evidence_ids),
                    "impact": finding.impact,
                    "action": finding.required_action or finding.recommendation,
                    "owner_assignment_ids": list(finding.responsibility_assignment_ids),
                },
            )
        )
    return records


def project_compliance_matrix(
    findings: Iterable[Finding], _context: dict[str, Any]
) -> list[OutputRecord]:
    records = []
    for finding in findings:
        requested = finding.metadata.get("compliance_status")
        if requested in {
            "compliant",
            "partially_compliant",
            "non_compliant",
            "not_applicable",
            "pending_information",
        }:
            status = requested
        elif (
            not finding.source_evidence_ids
            or finding.status == FindingStatus.UNVERIFIED
        ):
            status = "not_demonstrated"
        elif finding.status in {
            FindingStatus.UNVERIFIED,
            FindingStatus.REQUIRES_HUMAN_REVIEW,
        }:
            status = "not_demonstrated"
        else:
            # A confirmed finding establishes that the finding is supported;
            # it does not establish that a requirement is compliant. Keep the
            # legacy controlled-output status for downstream consumers.
            status = "pending_information"
        records.append(
            _record(
                "compliance_matrix",
                finding,
                {
                    "compliance_status": status,
                    "finding_class": finding.finding_class.value,
                    **_qualification_data(finding),
                },
                status=status,
            )
        )
    return records


def project_revision_changes(
    _findings: Iterable[Finding], context: dict[str, Any]
) -> list[OutputRecord]:
    records = []
    for change in context.get("changes", []):
        records.append(
            OutputRecord(
                output_id=_id("revision_change", change.get("head_document_id")),
                output_type="revision_change",
                finding_id=None,
                status=change.get("interpretation_status", "not_assessed"),
                data=dict(change),
                human_review_required=True,
            )
        )
    return records


def project_technical_queries(
    findings: Iterable[Finding], _context: dict[str, Any]
) -> list[OutputRecord]:
    return [
        _record(
            "technical_query",
            finding,
            {
                "question": finding.required_action
                or finding.recommendation
                or finding.finding,
                "background": finding.interpretation,
                "issue": finding.finding,
                "information_required": finding.required_action,
                "responsibility_assignment_ids": list(
                    finding.responsibility_assignment_ids
                ),
            },
            status="open",
        )
        for finding in findings
        if finding.status
        in {FindingStatus.UNVERIFIED, FindingStatus.POTENTIAL, FindingStatus.DISPUTED}
    ]


def project_risk_register(
    findings: Iterable[Finding], _context: dict[str, Any]
) -> list[OutputRecord]:
    return [
        _record(
            "risk_register",
            finding,
            {
                "description": finding.finding,
                "finding_class": finding.finding_class.value,
                **_qualification_data(finding),
                "cause": finding.metadata.get("cause"),
                "consequence": finding.impact,
                "risk_level": finding.risk_level.value,
                "mitigation": finding.required_action or finding.recommendation,
                "action_owner_assignment_ids": list(
                    finding.responsibility_assignment_ids
                ),
            },
        )
        for finding in findings
        if finding.risk_level not in {RiskLevel.LOW, RiskLevel.UNKNOWN}
    ]


def project_decision_log(
    findings: Iterable[Finding], _context: dict[str, Any]
) -> list[OutputRecord]:
    return [
        _record(
            "decision_log",
            finding,
            {
                "decision": finding.metadata.get("decision"),
                "basis": finding.finding,
                "follow_up": finding.required_action or finding.recommendation,
                "decision_maker_assignment_ids": list(
                    finding.responsibility_assignment_ids
                ),
            },
            status="pending" if not finding.metadata.get("decision") else "recorded",
        )
        for finding in findings
        if finding.metadata.get("decision") or finding.human_review_required
    ]


def project_executive_summary(
    outputs: dict[str, list[OutputRecord]],
) -> ExecutiveSummary:
    findings = {
        record.finding_id
        for records in outputs.values()
        for record in records
        if record.finding_id
    }
    risks = len(outputs.get("risk_register", []))
    decisions = len(outputs.get("decision_log", []))
    queries = len(outputs.get("technical_queries", []))
    critical = sum(
        record.data.get("risk_level") == RiskLevel.CRITICAL.value
        for record in outputs.get("review_comments", [])
    )
    return ExecutiveSummary(
        output_id=_id("executive_summary", None),
        output_type="executive_summary",
        finding_id=None,
        status="review_required" if findings else "no_findings",
        critical_issues=critical,
        key_risks=risks,
        required_decisions=decisions,
        outstanding_information=queries,
        overall_review_status="review_required" if findings else "complete",
    )
