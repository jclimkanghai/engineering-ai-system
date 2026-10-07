"""Persist an approved document-review artifact into project Registry."""

from __future__ import annotations

from typing import Any

from engineering_registry.importers import import_bundle
from engineering_registry.service import Principal, RegistryService, timestamp
from engineering_registry.store import SQLiteGraphStore
from pipelines.findings import EvidenceRef, FindingEngine, FindingQualification
from pipelines.outputs.engine import coerce_findings


def persist_approved_findings(
    *,
    project_id: str,
    registry_path: str,
    findings: list[dict[str, Any]],
    review_digest: str,
    reviewer_id: str,
    document_ids: list[str],
    document_revisions: dict[str, str | None],
    evidence: list[dict[str, Any]],
    registry_database: str | None = None,
    documents: list[dict[str, Any]] | None = None,
    requirements: list[dict[str, Any]] | None = None,
    reviewed_at: str | None = None,
    qualification_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not registry_database:
        raise ValueError(
            "Approved findings require an Engineering Registry database; legacy JSON is migration input only"
        )
    parsed_findings = coerce_findings(findings)
    evidence_refs = [
        EvidenceRef(
            evidence_id=row["evidence_id"],
            document_id=row.get("document_id"),
            revision=row.get("revision"),
            locator=row.get("locator"),
            excerpt=row.get("text") or row.get("source_text") or row.get("excerpt"),
            evidence_class=row.get("evidence_class", "source_fact"),
        )
        for row in evidence
        if row.get("evidence_id")
    ]
    engine = FindingEngine(evidence_refs)
    invalid = [
        f"{finding.finding_id}: {'; '.join(engine.validate(finding).errors)}"
        for finding in parsed_findings
        if not engine.validate(finding).valid
    ]
    if invalid:
        raise ValueError("Registry persistence blocked by qualification validation: " + " | ".join(invalid))
    if any(
        finding.qualification in {FindingQualification.NO_ISSUE, FindingQualification.POSITIVE_ASSURANCE}
        for finding in parsed_findings
    ):
        raise ValueError("No-issue and positive-assurance outcomes cannot be imported as formal findings")
    package = {
        "schema_version": 1,
        "project_id": project_id,
        "run_id": review_digest,
        "documents": [],
        "requirements": requirements or [],
        "evidence": evidence,
        "findings": findings,
    }
    with SQLiteGraphStore(registry_database) as store:
        service = RegistryService(
            store, Principal(reviewer_id, frozenset({project_id}), "reviewer")
        )
        with store.transaction():
            report = import_bundle(service, package, dry_run=False)
            store.put_record(
                project_id,
                "review_approvals",
                review_digest,
                {
                    "reviewer_id": reviewer_id,
                    "output_digest": review_digest,
                    "reviewed_at": reviewed_at or timestamp(),
                    "import_id": report["import_id"],
                    "authority_scope": "document_review_approval_only",
                    "project_acceptance": False,
                    "human_outcome_decision": False,
                    "qualification_trace": qualification_trace or [],
                },
            )
        return report
