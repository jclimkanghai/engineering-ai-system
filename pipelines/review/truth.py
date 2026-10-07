from __future__ import annotations

from pathlib import Path
from typing import Any

from pipelines.findings import EvidenceRef
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import AnalysisRequest
from pipelines.llm.stage import AnalysisAdapter
from pipelines.retrieval.models import RetrievalQuery

_TRUTH_PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "truth.md"


def truth_analysis(context, adapter: AnalysisAdapter | None) -> dict[str, Any]:
    """Produce source-linked Truth assessments for each retrievable requirement."""
    engine = context.get("evidence_engine")
    requirements = context.get("requirements", [])
    records = context.get("document_records", [])
    prompt = _TRUTH_PROMPT.read_text(encoding="utf-8")
    coverage = []
    assessments = []
    findings = []
    validation_errors = []
    validation_warnings = []
    response_metadata = []
    request_count = 0
    for requirement in requirements:
        identity = requirement.get("identity", {})
        requirement_id = identity.get("requirement_id")
        query_text = identity.get("source_text") or requirement.get("source_text") or ""
        if not engine or not query_text.strip():
            coverage.append(
                {
                    "requirement_id": requirement_id,
                    "status": "insufficient_evidence",
                    "evidence_ids": [],
                    "reason": "No evidence engine or requirement text was available.",
                }
            )
            assessments.append(
                {
                    "requirement_id": requirement_id,
                    "status": "unassessed",
                    "source_evidence_ids": [],
                    "reason": "UNKNOWN / INSUFFICIENT INFORMATION: no evidence was available.",
                }
            )
            continue

        bundle = engine.query(
            RetrievalQuery(
                query=query_text,
                document_ids=context.request.document_ids,
                evidence_classes=["source_fact", "context"],
                top_k=12,
                min_score=0.01,
            )
        )
        evidence_rows = bundle.evidence
        if not evidence_rows:
            coverage.append(
                {
                    "requirement_id": requirement_id,
                    "status": "insufficient_evidence",
                    "evidence_ids": [],
                    "reason": "No selected document evidence matched the requirement.",
                }
            )
            assessments.append(
                {
                    "requirement_id": requirement_id,
                    "status": "unassessed",
                    "source_evidence_ids": [],
                    "reason": "UNKNOWN / INSUFFICIENT INFORMATION: no supporting source was retrieved.",
                }
            )
            continue

        coverage_item = {
            "requirement_id": requirement_id,
            "status": "evidence_retrieved",
            "evidence_ids": bundle.evidence_ids(),
            "retrieval_method": bundle.retrieval_method,
            "warnings": list(bundle.warnings),
        }
        coverage.append(coverage_item)
        if adapter is None:
            assessments.append(
                {
                    "requirement_id": requirement_id,
                    "status": "unassessed",
                    "source_evidence_ids": bundle.evidence_ids(),
                    "reason": "Truth analysis requires a configured analysis adapter.",
                }
            )
            continue

        evidence_refs = [
            EvidenceRef(
                evidence_id=row["evidence_id"],
                document_id=row.get("document_id"),
                revision=row.get("revision"),
                locator=row.get("locator"),
                excerpt=row.get("text"),
            )
            for row in evidence_rows
        ]
        analysis_request = AnalysisRequest(
            mode="truth",
            project_id=records[0].project_id if records else None,
            document_ids=context.request.document_ids,
            requirements=[requirement],
            evidence=evidence_rows,
            context={
                "retrieval_method": bundle.retrieval_method,
                "retrieval_warnings": list(bundle.warnings),
                "coverage": coverage_item,
            },
            instructions=prompt,
        )
        request_count += 1
        response = adapter.analyse(analysis_request)
        response_metadata.append(
            {
                "model": response.model,
                "response_id": response.response_id,
                "usage": response.usage,
            }
        )
        if not response.findings:
            validation_errors.append(
                f"Truth analysis returned no findings for requirement {requirement_id}."
            )
            assessments.append(
                {
                    "requirement_id": requirement_id,
                    "status": "unassessed",
                    "source_evidence_ids": bundle.evidence_ids(),
                    "reason": "The analysis adapter returned no assessment.",
                }
            )
            continue

        seen_provider_finding_ids: set[str] = set()
        invalid_findings = set()
        for item in response.findings:
            item_requirement_ids = set(item.get("requirement_ids", []))
            unknown_requirements = item_requirement_ids - {requirement_id}
            if unknown_requirements:
                invalid_findings.add(item.get("finding_id"))
                validation_errors.append(
                    "Unknown requirement ID(s): "
                    + ", ".join(sorted(unknown_requirements))
                )
            if requirement_id not in item_requirement_ids:
                invalid_findings.add(item.get("finding_id"))
                validation_errors.append(
                    f"Truth finding {item.get('finding_id', '<unknown>')} must cite requirement {requirement_id}."
                )

        accepted, errors, warnings = findings_from_response(response, evidence_refs)
        validation_errors.extend(errors)
        validation_warnings.extend(warnings)
        evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
        for finding in accepted:
            if finding.finding_id in invalid_findings:
                continue
            if finding.finding_id in seen_provider_finding_ids:
                validation_errors.append(
                    f"Duplicate truth finding ID for requirement {requirement_id}: {finding.finding_id}"
                )
                continue
            seen_provider_finding_ids.add(finding.finding_id)
            provider_finding_id = finding.finding_id
            finding_data = finding.to_dict()
            namespaced_id = f"truth:{requirement_id}:{provider_finding_id}"
            finding_data["finding_id"] = namespaced_id
            finding_data["metadata"]["provider_finding_id"] = provider_finding_id
            findings.append(finding_data)
            source_rows = [
                evidence_by_id[evidence_id]
                for evidence_id in finding.source_evidence_ids
                if evidence_id in evidence_by_id
            ]
            assessments.append(
                {
                    "requirement_id": requirement_id,
                    "finding_id": namespaced_id,
                    "status": finding.status.value,
                    "fact": [
                        {
                            "evidence_id": row["evidence_id"],
                            "text": row.get("text"),
                        }
                        for row in source_rows
                    ],
                    "model_assessment": finding.finding,
                    "source": [
                        {
                            "evidence_id": row["evidence_id"],
                            "document_id": row.get("document_id"),
                            "revision": row.get("revision"),
                            "locator": row.get("locator"),
                        }
                        for row in source_rows
                    ],
                    "source_evidence_ids": list(finding.source_evidence_ids),
                    "interpretation": finding.interpretation,
                    "engineering_judgement": None,
                    "impact": finding.impact,
                    "assumptions": list(finding.assumptions),
                    "unknown": list(finding.uncertainties),
                    "confidence": finding.confidence,
                    "recommendation": finding.recommendation,
                }
            )

    if adapter is None:
        analysis_status = "NOT_CONNECTED"
    elif not validation_errors and all(
        item["status"] == "evidence_retrieved" for item in coverage
    ):
        analysis_status = "COMPLETE"
    else:
        analysis_status = "PARTIAL"

    return {
        "truth_assessments": assessments,
        "truth_coverage": coverage,
        "findings": findings,
        "analysis_status": analysis_status,
        "validation_errors": validation_errors,
        "finding_validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
        "adapter_request_count": request_count,
        "response_metadata": response_metadata,
        "human_review_note": "Truth assessments require review against source documents and governing revisions.",
    }
