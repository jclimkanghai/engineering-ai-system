from __future__ import annotations

import copy
import difflib
import hashlib
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.requirement_agent.extractor import SourceLocation, extract_requirements
from agents.requirement_agent.requirement_model import (
    RequirementSource,
    requirement_from_extraction,
)
from pipelines.accountability import classify_assignment
from pipelines.findings import EvidenceRef, FindingEngine
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import AnalysisRequest
from pipelines.outputs import build_audit_trail, coerce_findings, project_all
from pipelines.registry.document_manifest import (
    DocumentRegistry,
    RegistryBackedDocumentControl,
)
from pipelines.registry.finding_registry import (
    FindingRegistry,
    default_finding_registry_path,
)
from pipelines.retrieval.control_stage import run_document_control
from pipelines.retrieval.integration import build_engine_from_context
from pipelines.retrieval.models import RetrievalQuery

from .registry_persistence import persist_approved_findings as persist_approved_findings

_PAGE_MARKER = re.compile(r"^\s*\[PAGE:\s*(\d+)\]\s*$", re.IGNORECASE)


def _page_segments(text: str) -> list[tuple[int | None, str]]:
    """Split extractor input at source-emitted page markers only."""
    segments: list[tuple[int | None, str]] = []
    page: int | None = None
    lines: list[str] = []
    for line in text.splitlines():
        match = _PAGE_MARKER.match(line)
        if match:
            if lines:
                segments.append((page, "\n".join(lines)))
                lines = []
            page = int(match.group(1))
            continue
        lines.append(line)
    if lines:
        segments.append((page, "\n".join(lines)))
    return segments or [(None, text)]


def _registry(context) -> DocumentRegistry:
    path = context.request.metadata.get(
        "registry_path", "data/processed/document_registry.json"
    )
    database = context.request.metadata.get("engineering_registry_path")
    project_id = context.request.metadata.get("project_id")
    if database:
        if not project_id:
            raise ValueError("project_id is required with engineering_registry_path")
        return RegistryBackedDocumentControl(database, str(project_id), Path(path))
    return DocumentRegistry(Path(path))


def document_control(context) -> dict[str, Any]:
    registry = _registry(context)
    records = [
        registry.require(document_id) for document_id in context.request.document_ids
    ]
    warnings = []
    for record in records:
        if record.governing_status == "unknown":
            warnings.append(f"Governing status is unknown: {record.document_id}")
    control = run_document_control(context)
    return {
        "document_control": {
            "document_ids": context.request.document_ids,
            "status": "verified_registry_identity",
        },
        "document_records": records,
        "document_control_graph": control["document_control_graph"],
        "control_assessments": control["control_assessments"],
        "warnings": warnings,
    }


def _markdown_path(context, record) -> Path:
    path = record.metadata.get("output_files", {}).get("markdown")
    if not path:
        raise FileNotFoundError(
            f"No extracted Markdown recorded for {record.document_id}"
        )
    candidate = Path(path)
    if candidate.exists():
        return candidate
    registry_path = Path(
        context.request.metadata.get(
            "registry_path", "data/processed/document_registry.json"
        )
    )
    relative = registry_path.parent / candidate
    if relative.exists():
        return relative
    raise FileNotFoundError(f"Extracted Markdown not found: {candidate}")


def requirement_extraction(context) -> dict[str, Any]:
    requirements = []
    evidence = []
    for record in context.get("document_records", []):
        markdown_path = _markdown_path(context, record)
        text = markdown_path.read_text(encoding="utf-8")
        candidates = []
        for page, segment in _page_segments(text):
            candidates.extend(
                extract_requirements(
                    segment,
                    SourceLocation(
                        document_id=record.document_id,
                        document_number=record.document_number,
                        revision=record.revision,
                        page=page,
                    ),
                )
            )
        source = RequirementSource(
            document_id=record.document_id,
            document_number=record.document_number,
            document_title=record.title,
            revision=record.revision,
            revision_date=record.revision_date,
        )
        for candidate in candidates:
            requirement = requirement_from_extraction(candidate, source=source)
            requirements.append(requirement.to_dict())
            evidence.append(
                {
                    "evidence_id": requirement.requirement_id,
                    "requirement_id": requirement.requirement_id,
                    "document_id": record.document_id,
                    "revision": record.revision,
                    "page": candidate.source_location.page,
                    "source_hash": record.source_hash,
                    "source_text": requirement.source_text,
                    "evidence_type": "source_fact",
                }
            )
    return {
        "requirements": requirements,
        "evidence": evidence,
        "extraction_status": "COMPLETE",
    }


def _requirement_text(requirement: dict[str, Any]) -> str:
    identity = requirement.get("identity", {})
    return str(
        identity.get("source_text")
        or identity.get("requirement_text")
        or requirement.get("source_text")
        or requirement.get("requirement_text")
        or ""
    )


def _requirement_key(requirement: dict[str, Any]) -> str:
    """Build a conservative exact-duplicate key without interpreting meaning."""
    text = re.sub(r"\s+", " ", _requirement_text(requirement).strip().casefold())
    classification = requirement.get("classification", {})
    obligation = requirement.get("obligation", {})
    return "|".join(
        (
            text,
            str(classification.get("requirement_type") or ""),
            str(classification.get("requirement_category") or ""),
            str(obligation.get("action") or ""),
        )
    )


def triage_requirements(context, mode: str | None = None) -> dict[str, Any]:
    """Cluster exact duplicate candidates before retrieval and model calls.

    This stage never deletes the extracted registry. It selects one
    representative per exact wording/classification cluster for model work and
    keeps the complete candidate set available for QA and audit.
    """
    requirements = list(context.get("requirements", []))
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for requirement in requirements:
        requirement_id = requirement.get("identity", {}).get("requirement_id")
        if not requirement_id:
            continue
        key = _requirement_key(requirement)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(requirement)

    include_informational = bool(
        context.request.metadata.get("include_informational_requirements", False)
    )
    analysis_requirements: list[dict[str, Any]] = []
    clusters: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for key in order:
        members = groups[key]
        representative = copy.deepcopy(members[0])
        member_ids = [item["identity"]["requirement_id"] for item in members]
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        cluster_id = f"RC-{digest}"
        classification = representative.get("classification", {})
        requirement_type = classification.get("requirement_type", "unknown")
        priority = (
            "high" if requirement_type in {"mandatory", "conditional"} else "normal"
        )
        if requirement_type == "informational" and not include_informational:
            priority = "low"
        representative["triage"] = {
            "cluster_id": cluster_id,
            "cluster_member_ids": member_ids,
            "representative": True,
            "analysis_priority": priority,
        }
        clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "representative_requirement_id": member_ids[0],
            "member_requirement_ids": member_ids,
            "cluster_key": key,
        }
        # If the source contains only informational candidates, retain one
        # representative so the run reports evidence rather than failing
        # solely because a heuristic filtered the queue.
        if priority != "low" or not analysis_requirements:
            analysis_requirements.append(representative)
        else:
            skipped.extend(member_ids)

    mode_lenses = {
        "truth": ["truth"],
        "steelman": ["steelman"],
        "gap": ["gap"],
        "critic": ["critic"],
        "scope": ["scope"],
        "compliance": ["compliance"],
        "change": ["change"],
        "risk": ["risk"],
        "competitor": ["competitor"],
        "x10think": ["truth", "steelman", "gap", "critic", "risk"],
        "allin": ["truth", "steelman", "scope", "compliance", "gap", "critic", "risk"],
    }
    routing_tier = (
        "high_reasoning"
        if mode in {"allin", "risk", "compliance", "change"}
        else "standard"
    )
    mode = mode or context.request.review_mode
    return {
        "analysis_requirements": analysis_requirements,
        "requirement_clusters": clusters,
        "analysis_plan": {
            "mode": mode,
            "lenses": mode_lenses.get(mode, [mode]),
            "routing_tier": routing_tier,
            "selection": "material_requirements_and_one_informational_fallback",
            "merge_policy": "deterministic_duplicate_findings_only",
        },
        "requirement_triage": {
            "status": "COMPLETE",
            "input_count": len(requirements),
            "cluster_count": len(clusters),
            "analysis_count": len(analysis_requirements),
            "skipped_informational_ids": skipped,
            "deduplicated_count": max(0, len(requirements) - len(clusters)),
        },
    }


def build_evidence_index(context) -> dict[str, Any]:
    engine = build_engine_from_context(context)
    return {
        "evidence_engine": engine,
        "evidence_index": engine.index,
        "evidence": [chunk.to_dict() for chunk in engine.index.all()],
    }


def compliance_analysis(context, adapter=None) -> dict[str, Any]:
    """Run the legacy compliance contract against supporting documents only.

    The optimized ``evidence_analysis`` stage handles the batched /allin path.
    This compatibility stage remains available to integrations that consume a
    dedicated ``compliance_analysis`` artifact.
    """
    engine = context.get("evidence_engine")
    requirements = context.get("requirements", [])
    coverage = []
    validation_errors = []
    validation_warnings = []
    findings = []
    response_metadata = []
    request_count = 0
    seen_finding_ids: set[str] = set()

    for requirement in requirements:
        identity = requirement.get("identity", {})
        provenance = requirement.get("provenance", {})
        requirement_id = identity.get("requirement_id")
        source_document_id = provenance.get("source_document_id")
        query_text = identity.get("source_text") or requirement.get("source_text") or ""
        candidate_document_ids = [
            document_id
            for document_id in context.request.document_ids
            if document_id != source_document_id
        ]
        if not engine or not candidate_document_ids or not query_text.strip():
            coverage.append(
                {
                    "requirement_id": requirement_id,
                    "status": "insufficient_evidence",
                    "evidence_ids": [],
                    "reason": "No eligible supporting document or requirement text was available.",
                }
            )
            continue

        bundle = engine.query(
            RetrievalQuery(
                query=query_text,
                document_ids=candidate_document_ids,
                evidence_classes=["source_fact", "context"],
                top_k=8,
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
                    "reason": "No supporting evidence matched in the selected documents.",
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
        records = context.get("document_records", [])
        analysis_request = AnalysisRequest(
            mode="compliance",
            project_id=records[0].project_id if records else None,
            document_ids=context.request.document_ids,
            requirements=[requirement],
            evidence=evidence_rows,
            context={
                "retrieval_method": bundle.retrieval_method,
                "retrieval_warnings": list(bundle.warnings),
                "coverage": coverage_item,
            },
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
        invalid_requirement_findings = set()
        for item in response.findings:
            item_requirement_ids = set(item.get("requirement_ids", []))
            unknown_requirements = item_requirement_ids - {requirement_id}
            if unknown_requirements:
                invalid_requirement_findings.add(item.get("finding_id"))
                validation_errors.append(
                    "Unknown requirement ID(s): "
                    + ", ".join(sorted(unknown_requirements))
                )
            if requirement_id not in item_requirement_ids:
                invalid_requirement_findings.add(item.get("finding_id"))
                validation_errors.append(
                    f"Finding {item.get('finding_id', '<unknown>')} must cite requirement {requirement_id}."
                )

        accepted, errors, warnings = findings_from_response(response, evidence_refs)
        validation_errors.extend(errors)
        validation_warnings.extend(warnings)
        for finding in accepted:
            if finding.finding_id in invalid_requirement_findings:
                continue
            if finding.finding_id in seen_finding_ids:
                validation_errors.append(
                    f"Duplicate finding ID across compliance responses: {finding.finding_id}"
                )
                continue
            seen_finding_ids.add(finding.finding_id)
            findings.append(finding.to_dict())

    return {
        "findings": findings,
        "coverage": coverage,
        "analysis_status": "NOT_CONNECTED" if adapter is None else "COMPLETE",
        "validation_errors": validation_errors,
        "finding_validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
        "adapter_request_count": request_count,
        "response_metadata": response_metadata,
        "human_review_note": "Material compliance conclusions require professional review.",
    }


def assign_responsibilities(context) -> dict[str, Any]:
    assignments = []
    links = {}
    for finding in context.get("findings", []):
        finding_id = (
            finding.get("finding_id")
            if isinstance(finding, dict)
            else finding.finding_id
        )
        metadata = (
            finding.get("metadata", {})
            if isinstance(finding, dict)
            else finding.metadata
        )
        evidence_ids = (
            finding.get("source_evidence_ids", [])
            if isinstance(finding, dict)
            else finding.source_evidence_ids
        )
        party_id = metadata.get("action_owner")
        assignment = classify_assignment(
            party_id=party_id,
            role="action_owner",
            responsibility_type="required action",
            source_evidence_ids=list(evidence_ids),
            source_requirement_ids=(
                finding.get("requirement_ids", [])
                if isinstance(finding, dict)
                else finding.requirement_ids
            ),
            source_class="explicit" if party_id else "unknown",
        )
        assignments.append(assignment.to_dict())
        links[finding_id] = [assignment.assignment_id]
    return {
        "responsibility_assignments": assignments,
        "finding_assignment_links": links,
    }


def consolidate_findings(context) -> dict[str, Any]:
    """Merge only deterministic duplicate findings before projections."""
    findings = list(context.get("findings", []))
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    severity = {"low": 0, "medium": 1, "high": 2, "critical": 3, "unknown": -1}
    confidence = {"low": 0, "medium": 1, "high": 2}

    def normalise(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())

    for raw in findings:
        source = raw.to_dict() if hasattr(raw, "to_dict") else raw
        item = copy.deepcopy(dict(source))
        finding_id = str(item.get("finding_id") or "")
        lens = "truth" if finding_id.startswith("truth:") else ""
        key = "|".join(
            (
                normalise(item.get("title")),
                normalise(item.get("finding")),
                normalise(item.get("required_action") or item.get("recommendation")),
                lens,
            )
        )
        current = by_key.get(key)
        if current is None:
            item.setdefault("metadata", {})
            item["metadata"] = {
                **item["metadata"],
                "merged_finding_ids": [item.get("finding_id")],
                "merged_count": 1,
            }
            by_key[key] = item
            merged.append(item)
            continue
        for field in (
            "source_evidence_ids",
            "requirement_ids",
            "assumptions",
            "uncertainties",
            "conflicts",
            "risk_dimensions",
        ):
            values = list(current.get(field, []) or [])
            for value in list(item.get(field, []) or []):
                if value not in values:
                    values.append(value)
            current[field] = values
        if severity.get(str(item.get("risk_level")), -1) > severity.get(
            str(current.get("risk_level")), -1
        ):
            current["risk_level"] = item.get("risk_level")
        if confidence.get(str(item.get("confidence")), -1) > confidence.get(
            str(current.get("confidence")), -1
        ):
            current["confidence"] = item.get("confidence")
        current["human_review_required"] = bool(
            current.get("human_review_required")
        ) or bool(item.get("human_review_required"))
        metadata = current.setdefault("metadata", {})
        ids = metadata.setdefault("merged_finding_ids", [current.get("finding_id")])
        if item.get("finding_id") not in ids:
            ids.append(item.get("finding_id"))
        metadata["merged_count"] = len(ids)

    return {
        "findings": merged,
        "finding_merge": {
            "status": "COMPLETE",
            "input_count": len(findings),
            "output_count": len(merged),
            "merged_count": max(0, len(findings) - len(merged)),
        },
    }


def build_controlled_outputs(context) -> dict[str, Any]:
    findings = coerce_findings(context.get("findings", []))
    outputs = project_all(findings, context.artifacts)
    serialized = {
        key: [item.to_dict() for item in records] for key, records in outputs.items()
    }
    return {"controlled_outputs": serialized, "output_records": serialized}


def finding_qualification_gate(context) -> dict[str, Any]:
    """Fail closed on unqualified candidates before consolidation or Registry matching."""
    evidence_rows = [
        *context.get("evidence", []),
        *context.get("analysis_evidence", []),
    ]
    evidence_by_id = {
        row.get("evidence_id"): row
        for row in evidence_rows
        if row.get("evidence_id")
    }
    evidence_refs = [
        EvidenceRef(
            evidence_id=str(row["evidence_id"]),
            document_id=row.get("document_id"),
            revision=row.get("revision"),
            locator=row.get("locator"),
            excerpt=row.get("text") or row.get("source_text"),
        )
        for row in evidence_by_id.values()
    ]
    requirement_ids = {
        requirement.get("identity", {}).get("requirement_id")
        or requirement.get("requirement_id")
        for requirement in context.get("requirements", [])
    }
    requirement_ids.discard(None)
    engine = FindingEngine(evidence_refs)
    qualified = []
    trace = []
    errors = []
    input_items = list(context.get("findings", []))
    for item in input_items:
        try:
            finding = coerce_findings([item])[0]
            validation = engine.validate(finding)
            candidate_errors = list(validation.errors)
            unknown_requirements = set(finding.requirement_ids) - requirement_ids
            if requirement_ids and unknown_requirements:
                candidate_errors.append(
                    "Unknown requirement ID(s): " + ", ".join(sorted(unknown_requirements))
                )
            accepted = not candidate_errors
            if accepted and finding.qualification.value not in {
                "no_issue",
                "positive_assurance",
            }:
                qualified.append(finding.to_dict())
            trace.append(
                {
                    "finding_id": finding.finding_id,
                    "qualification": (
                        finding.qualification.value
                        if finding.qualification is not None
                        else None
                    ),
                    "finding_class": str(finding.finding_class),
                    "risk_level": str(finding.risk_level),
                    "qualification_rationale": finding.qualification_rationale,
                    "qualification_basis": asdict(finding.qualification_basis),
                    "qualified": accepted,
                    "registry_finding": accepted
                    and finding.qualification.value
                    not in {"no_issue", "positive_assurance"},
                    "validation_errors": candidate_errors,
                }
            )
            errors.extend(
                f"Finding {finding.finding_id}: {error}" for error in candidate_errors
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"Candidate finding could not be qualified: {exc}")
            trace.append(
                {
                    "finding_id": item.get("finding_id")
                    if isinstance(item, dict)
                    else None,
                    "qualified": False,
                    "registry_finding": False,
                    "validation_errors": [str(exc)],
                }
            )
    result_errors = list(context.get("finding_validation_errors", [])) + errors
    return {
        "findings": qualified,
        "qualification_trace": trace,
        "finding_validation_errors": list(dict.fromkeys(result_errors)),
        "finding_qualification_gate": {
            "status": "PASS" if not errors else "BLOCKED",
            "candidate_count": len(input_items),
            "qualified_count": sum(bool(row.get("qualified")) for row in trace),
            "registry_finding_count": len(qualified),
            "blockers": list(dict.fromkeys(errors)),
        },
    }


def build_audit_artifact(context) -> dict[str, Any]:
    findings = coerce_findings(context.get("findings", []))
    records = build_audit_trail(findings, context.artifacts)
    return {"audit_trail": [record.to_dict() for record in records]}


def finding_registry_reconciliation(context) -> dict[str, Any]:
    """Prepare a conservative project-register match plan without writing."""
    findings = context.get("findings", [])
    if not findings:
        return {
            "finding_registry_reconciliation": {
                "status": "NO_FINDINGS",
                "matches": [],
                "automatic_closure": False,
            }
        }

    records = context.get("document_records", [])
    record_project_ids = {
        record.project_id for record in records if getattr(record, "project_id", None)
    }
    requested_project_id = context.request.metadata.get("project_id")
    if requested_project_id:
        record_project_ids.add(str(requested_project_id))
    if len(record_project_ids) != 1:
        status = (
            "PROJECT_SCOPE_CONFLICT" if record_project_ids else "PROJECT_ID_REQUIRED"
        )
        return {
            "finding_registry_reconciliation": {
                "status": status,
                "matches": [],
                "automatic_closure": False,
                "message": "Set one project_id on the request or source document records before registry reconciliation.",
            }
        }

    project_id = next(iter(record_project_ids))
    document_registry_path = context.request.metadata.get(
        "registry_path", "data/processed/document_registry.json"
    )
    registry_path = context.request.metadata.get("finding_registry_path")
    if registry_path:
        path = Path(registry_path).expanduser()
    else:
        path = default_finding_registry_path(project_id, document_registry_path)
    if context.request.metadata.get("engineering_registry_path"):
        database = Path(
            context.request.metadata["engineering_registry_path"]
        ).expanduser()
        if not database.is_file():
            raise FileNotFoundError(
                "Engineering Registry database is required for finding reconciliation"
            )
        from engineering_registry.service import Principal, RegistryService
        from engineering_registry.store import SQLiteGraphStore

        with SQLiteGraphStore(database) as store:
            service = RegistryService(
                store,
                Principal(
                    "finding-reconciliation-reader", frozenset({project_id}), "reader"
                ),
            )
            plan = service.reconcile_findings(project_id, findings)
        plan["persistence_owner"] = "engineering_registry"
        plan["message"] = (
            "Exact wording matches show existing Registry lifecycle; persistence is atomic after review."
        )
    else:
        registry = FindingRegistry(path, project_id)
        plan = registry.reconcile(findings)
    plan["registry_path"] = str(path)
    plan["registry_database"] = context.request.metadata.get(
        "engineering_registry_path"
    )
    plan["documents"] = [asdict(record) for record in records]
    plan["requirements"] = list(context.get("requirements", []))
    plan["document_ids"] = list(context.request.document_ids)
    plan["document_revisions"] = {
        record.document_id: record.revision for record in records
    }
    return {"finding_registry_reconciliation": plan}


def revision_change(context) -> dict[str, Any]:
    compare_ids = context.request.compare_document_ids
    if not compare_ids:
        return {"changes": [], "change_status": "NOT_REQUESTED"}
    registry = _registry(context)
    records = [registry.require(document_id) for document_id in compare_ids]
    if len(records) < 2:
        return {"changes": [], "change_status": "INSUFFICIENT_DOCUMENTS"}
    base, head = records[-2], records[-1]
    base_text = _markdown_path(context, base).read_text(encoding="utf-8")
    head_text = _markdown_path(context, head).read_text(encoding="utf-8")
    diff = list(
        difflib.unified_diff(
            base_text.splitlines(),
            head_text.splitlines(),
            fromfile=base.document_id,
            tofile=head.document_id,
            lineterm="",
        )
    )
    return {
        "changes": [
            {
                "change_type": "textual_diff",
                "base_document_id": base.document_id,
                "head_document_id": head.document_id,
                "added_lines": sum(
                    line.startswith("+") and not line.startswith("+++") for line in diff
                ),
                "deleted_lines": sum(
                    line.startswith("-") and not line.startswith("---") for line in diff
                ),
                "diff_excerpt": diff[:200],
                "interpretation_status": "not_assessed",
            }
        ],
        "change_status": "deterministic_text_diff",
    }


def engineering_qa(context) -> dict[str, Any]:
    blockers = []
    warnings = []
    analysis_status = context.get("analysis_status")
    if analysis_status is not None and analysis_status != "COMPLETE":
        blockers.append(f"Evidence analysis is not complete: {analysis_status}.")
    qualification_gate = context.get("finding_qualification_gate")
    if qualification_gate and qualification_gate.get("status") != "PASS":
        blockers.extend(
            qualification_gate.get("blockers")
            or ["Finding qualification gate did not pass."]
        )
    registry_plan = context.get("finding_registry_reconciliation", {})
    if context.get("findings") and registry_plan.get("status") not in {
        None,
        "READY",
        "NO_FINDINGS",
        "PROJECT_ID_REQUIRED",
    }:
        blockers.append(
            "Finding registry reconciliation is unavailable: "
            f"{registry_plan.get('status', 'UNKNOWN')}"
        )
    if context.get("findings") and registry_plan.get("status") == "PROJECT_ID_REQUIRED":
        warnings.append(
            "Finding registry reconciliation was skipped because project_id was not supplied."
        )
    evidence_ids = {item.get("evidence_id") for item in context.get("evidence", [])}
    for requirement in context.get("requirements", []):
        identity = requirement.get("identity", {})
        provenance = requirement.get("provenance", {})
        requirement_id = identity.get("requirement_id", "<unknown>")
        if not identity.get("requirement_id") or not identity.get("source_text"):
            blockers.append("Requirement missing stable identity or source text.")
        if not provenance.get("source_document_id"):
            blockers.append(f"Requirement {requirement_id} missing source document ID.")
    for finding in context.get("findings", []):
        for evidence_id in finding.get("source_evidence_ids", []):
            if evidence_id not in evidence_ids:
                blockers.append(f"Finding cites unknown evidence ID: {evidence_id}")
    for error in context.get("finding_validation_errors", []):
        blockers.append(str(error))
    for assignment in context.get("responsibility_assignments", []):
        if assignment.get("human_review_required") and not assignment.get("party_id"):
            warnings.append(
                f"Responsibility assignment has unknown party: {assignment.get('assignment_id', '<unknown>')}"
            )
    for audit in context.get("audit_trail", []):
        for link in audit.get("unresolved_links", []):
            blockers.append(f"Audit trail has unresolved link: {link}")
    for output_group in context.get("controlled_outputs", {}).values():
        for output in output_group:
            for evidence_id in output.get("source_evidence_ids", []):
                if evidence_id not in evidence_ids:
                    blockers.append(f"Output cites unknown evidence ID: {evidence_id}")
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    review_items = [
        {
            "assignment_id": assignment.get("assignment_id", "<unknown>"),
            "reason": "Responsibility owner is unknown and requires human review.",
        }
        for assignment in context.get("responsibility_assignments", [])
        if assignment.get("human_review_required") and not assignment.get("party_id")
    ]
    return {
        "qa": {
            "requirement_count": len(context.get("requirements", [])),
            "analysis_status": analysis_status,
            "blockers": blockers,
            "warnings": warnings,
            "status": "PASS" if not blockers else "BLOCKED",
        },
        "review_items": review_items,
        # This is QA readiness only. The explicit reviewer decision is checked
        # by the final human-review gate.
        "qa_ready": not blockers,
        # Backward-compatible alias retained for existing integrations.
        "human_review_ready": not blockers,
        "human_review_required": True,
    }
