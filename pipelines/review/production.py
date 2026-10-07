from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .analysis import AnalysisAdapter, run_evidence_analysis
from .approval import canonical_review_digest, validate_approval
from .integration import (
    assign_responsibilities,
    build_audit_artifact,
    build_controlled_outputs,
    build_evidence_index,
    compliance_analysis,
    consolidate_findings,
    document_control,
    engineering_qa,
    finding_qualification_gate,
    finding_registry_reconciliation,
    persist_approved_findings,
    requirement_extraction,
    revision_change,
    triage_requirements,
)
from .models import StageStatus
from .orchestrator import ReviewPipeline
from .reporting import render_review_report, write_review_report
from .stages import FunctionStage
from .truth import truth_analysis

_ROOT = Path(__file__).resolve().parents[2]
_STANDARD_STAGES = [
    "document_control",
    "requirement_extraction",
    "requirement_triage",
    "evidence_retrieval",
    "evidence_analysis",
    "finding_qualification",
    "finding_consolidation",
    "finding_registry_reconciliation",
    "responsibility_assignment",
    "output_projection",
    "audit_trail",
    "engineering_qa",
    "human_review_gate",
]
_DEFAULT_MODES = {
    name: list(_STANDARD_STAGES)
    for name in (
        "truth",
        "steelman",
        "gap",
        "critic",
        "scope",
        "compliance",
        "risk",
        "competitor",
        "x10think",
    )
}
_DEFAULT_MODES["truth"] = [
    "document_control",
    "requirement_extraction",
    "requirement_triage",
    "evidence_retrieval",
    "truth_check",
    *_STANDARD_STAGES[5:],
]
_DEFAULT_MODES["compliance"] = [
    "document_control",
    "requirement_extraction",
    "requirement_triage",
    "evidence_retrieval",
    "compliance_analysis",
    *_STANDARD_STAGES[5:],
]
_DEFAULT_MODES["competitor"] = list(_DEFAULT_MODES["compliance"])
_DEFAULT_MODES["change"] = [
    "document_control",
    "requirement_extraction",
    "requirement_triage",
    "revision_change",
    *_STANDARD_STAGES[3:],
]
_DEFAULT_MODES["allin"] = [
    "document_control",
    "requirement_extraction",
    "requirement_triage",
    "revision_change",
    "evidence_retrieval",
    "evidence_analysis",
    "compliance_analysis",
    "truth_check",
    "engineering_conclusion",
    *_STANDARD_STAGES[5:],
]


def _compatibility_analysis(context, kind: str, adapter: AnalysisAdapter | None):
    """Expose legacy mode artifacts without repeating optimized model calls."""
    if context.get("analysis_status") is not None:
        findings = []
        for finding in context.get("findings", []):
            item = dict(finding)
            metadata = dict(item.get("metadata", {}))
            provider_id = metadata.get("provider_finding_id")
            if provider_id:
                item["finding_id"] = provider_id
            item["metadata"] = metadata
            findings.append(item)
        if kind == "compliance":
            return {
                "findings": findings,
                "coverage": list(context.get("analysis_coverage", [])),
                "analysis_status": context.get("analysis_status"),
                "validation_errors": list(context.get("analysis_errors", [])),
                "finding_validation_errors": list(context.get("analysis_errors", [])),
                "adapter_request_count": 0,
            }
        if (
            kind == "truth"
            and context.request.review_mode == "allin"
            and adapter is not None
        ):
            truth = truth_analysis(context, adapter)
            truth["findings"] = [*findings, *truth.get("findings", [])]
            return truth
        return {
            "findings": findings,
            "truth_assessments": list(context.get("truth_assessments", [])),
            "analysis_status": context.get("analysis_status"),
            "validation_errors": list(context.get("analysis_errors", [])),
            "validation_warnings": list(context.get("analysis_warnings", [])),
        }
    if kind == "compliance":
        return compliance_analysis(context, adapter)
    return truth_analysis(context, adapter)


def _review_digest(payload: dict[str, Any]) -> str:
    """Compatibility wrapper; approval.py owns canonical output digesting."""
    return canonical_review_digest(payload)


def _approval_status(approval: Any, digest: str) -> tuple[str, str | None]:
    if is_dataclass(approval):
        approval = asdict(approval)
    if not isinstance(approval, dict):
        if approval is None:
            return validate_approval(None, digest)
        return (
            "invalid",
            "Approval must contain reviewer_id, decision, reviewed_at, and output_digest.",
        )
    required = ("reviewer_id", "decision", "reviewed_at", "output_digest")
    if not all(
        isinstance(approval.get(key), str) and approval[key].strip() for key in required
    ):
        return (
            "invalid",
            "Approval is missing a reviewer, decision, timestamp, or digest.",
        )
    from .models import ReviewApproval

    return validate_approval(
        ReviewApproval(**{key: approval[key] for key in required}), digest
    )


def _render_report(
    payload: dict[str, Any],
    approval_status: str,
    digest: str,
    approval_record: dict[str, Any] | None = None,
):
    """Compatibility wrapper around the reporting projection."""
    return render_review_report(payload, approval_status, digest, approval_record)


def _write_report(paths: dict[str, str] | None, markdown: str, html: str) -> None:
    """Compatibility wrapper around the reporting file writer."""
    write_review_report(paths, markdown, html)


def _gate(context, review_mode: str):
    qa = context.get("qa", {})
    analysis_status = context.get("analysis_status", "UNKNOWN")
    outputs = context.get("controlled_outputs", {})
    evidence_catalog = {
        row.get("evidence_id"): dict(row)
        for row in context.get("evidence", [])
        if row.get("evidence_id")
    }
    evidence_catalog.update(
        {
            row.get("evidence_id"): dict(row)
            for row in context.get("analysis_evidence", [])
            if row.get("evidence_id")
        }
    )
    cited_ids = {
        evidence_id
        for finding in context.get("findings", [])
        for evidence_id in finding.get("source_evidence_ids", [])
    }
    evidence = [
        evidence_catalog[evidence_id]
        for evidence_id in sorted(cited_ids)
        if evidence_id in evidence_catalog
    ]
    digest_payload = {
        "review_mode": review_mode,
        "document_ids": context.request.document_ids,
        "compare_document_ids": context.request.compare_document_ids,
        "findings": context.get("findings", []),
        "finding_qualification_gate": context.get(
            "finding_qualification_gate", {}
        ),
        "qualification_trace": context.get("qualification_trace", []),
        "requirements": context.get("requirements", []),
        "controlled_outputs": outputs,
        "audit_trail": context.get("audit_trail", []),
        "finding_registry_reconciliation": context.get(
            "finding_registry_reconciliation", {}
        ),
        "qa": qa,
        "analysis_status": analysis_status,
        "analysis_coverage": context.get("analysis_coverage", []),
        "cited_evidence": evidence,
    }
    digest = _review_digest(digest_payload)
    if context.request.require_human_gate:
        approval_status, approval_error = _approval_status(
            context.request.approval, digest
        )
    else:
        approval_status, approval_error = "not_required", None

    quality_blockers = []
    if qa.get("status") != "PASS":
        quality_blockers.extend(
            qa.get("blockers", []) or ["Engineering QA did not pass."]
        )
    if analysis_status != "COMPLETE":
        quality_blockers.append(
            f"Evidence analysis is not complete: {analysis_status}."
        )
    blockers = list(quality_blockers)
    if approval_error:
        blockers.append(approval_error)
    blockers = list(dict.fromkeys(blockers))
    gate_passed = not blockers
    registry_write = None
    if gate_passed and approval_status == "approved":
        registry_plan = context.get("finding_registry_reconciliation", {})
        if registry_plan.get("status") == "READY":
            approval_data = (
                asdict(context.request.approval)
                if is_dataclass(context.request.approval)
                else context.request.approval
            )
            try:
                registry_write = persist_approved_findings(
                    project_id=registry_plan["project_id"],
                    registry_path=registry_plan["registry_path"],
                    findings=context.get("findings", []),
                    review_digest=digest,
                    reviewer_id=approval_data["reviewer_id"],
                    document_ids=registry_plan.get(
                        "document_ids", context.request.document_ids
                    ),
                    document_revisions=registry_plan.get("document_revisions", {}),
                    evidence=evidence,
                    registry_database=registry_plan.get("registry_database"),
                    documents=registry_plan.get("documents"),
                    requirements=registry_plan.get("requirements"),
                    reviewed_at=approval_data["reviewed_at"],
                    qualification_trace=context.get("qualification_trace", []),
                )
            except Exception as exc:
                registry_write = {
                    "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                blockers.append(
                    "Approved findings could not be written to the project registry."
                )
                gate_passed = False

    report_payload = {
        "review_mode": review_mode,
        "findings": context.get("findings", []),
        "finding_qualification_gate": context.get(
            "finding_qualification_gate", {}
        ),
        "qualification_trace": context.get("qualification_trace", []),
        "outputs": outputs,
        "qa": qa,
        "coverage": context.get("analysis_coverage", []),
        "evidence": evidence,
        "analysis_status": analysis_status,
        "finding_registry_reconciliation": context.get(
            "finding_registry_reconciliation", {}
        ),
    }
    approval_record = None
    if approval_status == "approved":
        approval_record = (
            asdict(context.request.approval)
            if is_dataclass(context.request.approval)
            else context.request.approval
        )
    report_markdown, report_html = _render_report(
        report_payload, approval_status, digest, approval_record
    )
    report_paths = None
    requested_path = context.request.metadata.get("report_path")
    if requested_path:
        requested = Path(requested_path).expanduser()
        if requested.suffix.lower() in {".md", ".markdown"}:
            markdown_path = requested
            html_path = requested.with_suffix(".html")
        else:
            html_path = requested
            markdown_path = requested.with_suffix(".md")
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(report_html, encoding="utf-8")
        markdown_path.write_text(report_markdown, encoding="utf-8")
        report_paths = {
            "html": str(html_path.resolve()),
            "markdown": str(markdown_path.resolve()),
        }
        _write_report(report_paths, report_markdown, report_html)

    return {
        "_stage_status": StageStatus.COMPLETE if gate_passed else StageStatus.BLOCKED,
        "human_review_ready": gate_passed,
        "human_review_required": context.request.require_human_gate,
        "approval_status": approval_status,
        "gate_passed": gate_passed,
        "gate_blockers": blockers,
        "quality_blockers": quality_blockers,
        "output_digest": digest,
        "review_snapshot": digest_payload,
        "report_payload": report_payload,
        "report_markdown": report_markdown,
        "report_html": report_html,
        "report_paths": report_paths,
        "finding_registry_write": registry_write,
    }


def approve_review(result, approval):
    """Record reviewer approval against the digest of an existing pipeline result.

    This updates the completed review artifact without rerunning retrieval or
    the model, so the reviewer approves the exact report they inspected.
    """
    gate = next(
        (
            stage
            for stage in reversed(result.stages)
            if stage.stage_id == "human_review_gate"
        ),
        None,
    )
    if gate is None:
        raise ValueError("Review result has no human-review gate to approve")
    digest = gate.data.get("output_digest")
    if not digest:
        raise ValueError("Review result has no output digest")
    status, error = _approval_status(approval, digest)
    snapshot = dict(gate.data.get("review_snapshot", {}))
    snapshot["findings"] = result.findings
    snapshot["document_ids"] = result.request.document_ids
    snapshot["compare_document_ids"] = result.request.compare_document_ids
    registry_stage = next(
        (
            item
            for item in result.stages
            if item.stage_id == "finding_registry_reconciliation"
        ),
        None,
    )
    if registry_stage is not None:
        snapshot["finding_registry_reconciliation"] = registry_stage.data.get(
            "finding_registry_reconciliation", {}
        )
    payload = gate.data.get("report_payload", {})
    displayed_snapshot = dict(snapshot)
    for source, destination in (
        ("review_mode", "review_mode"),
        ("findings", "findings"),
        ("outputs", "controlled_outputs"),
        ("evidence", "cited_evidence"),
        ("qa", "qa"),
        ("coverage", "analysis_coverage"),
        ("analysis_status", "analysis_status"),
        ("finding_registry_reconciliation", "finding_registry_reconciliation"),
        ("finding_qualification_gate", "finding_qualification_gate"),
        ("qualification_trace", "qualification_trace"),
    ):
        if source in payload:
            displayed_snapshot[destination] = payload[source]
    if (
        _review_digest(snapshot) != digest
        or _review_digest(displayed_snapshot) != digest
    ):
        status, error = (
            "stale",
            "Review content changed after the displayed snapshot; rerun review before approval.",
        )
    gate.data["approval_status"] = status
    gate.data["approval_error"] = error
    reviewer = asdict(approval) if is_dataclass(approval) else approval
    gate.data["approval_record"] = reviewer if status == "approved" else None
    gate.data["gate_blockers"] = list(gate.data.get("quality_blockers", []))
    if error:
        gate.data["gate_blockers"].append(error)
    gate.data["human_review_ready"] = (
        not gate.data["gate_blockers"] and status == "approved"
    )
    gate.data["gate_passed"] = gate.data["human_review_ready"]
    if gate.data["gate_passed"]:
        try:
            persist_result = _persist_registry_result(result, approval, digest)
            gate.data["finding_registry_write"] = persist_result
        except Exception as exc:
            gate.data["finding_registry_write"] = {
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
            }
            gate.data["gate_blockers"].append(
                "Approved findings could not be written to the project registry."
            )
            gate.data["human_review_ready"] = False
            gate.data["gate_passed"] = False
    gate.status = (
        StageStatus.COMPLETE if gate.data["gate_passed"] else StageStatus.BLOCKED
    )
    payload = gate.data.get("report_payload")
    if payload:
        markdown, html = _render_report(
            payload, status, digest, gate.data.get("approval_record")
        )
        gate.data["report_markdown"] = markdown
        gate.data["report_html"] = html
        _write_report(gate.data.get("report_paths"), markdown, html)
    result.final_status = gate.status
    return result


def _persist_registry_result(result, approval, digest: str) -> dict[str, Any]:
    from .integration import persist_approved_findings

    stage = next(
        (
            item
            for item in result.stages
            if item.stage_id == "finding_registry_reconciliation"
        ),
        None,
    )
    if stage is None:
        return {"status": "NOT_CONFIGURED"}
    registry_plan = stage.data.get("finding_registry_reconciliation", {})
    if registry_plan.get("status") != "READY":
        return {"status": "NOT_CONFIGURED"}
    approval_data = asdict(approval) if is_dataclass(approval) else approval
    report_payload = next(
        (
            item.data.get("report_payload")
            for item in result.stages
            if item.stage_id == "human_review_gate"
        ),
        {},
    )
    return persist_approved_findings(
        project_id=registry_plan["project_id"],
        registry_path=registry_plan["registry_path"],
        findings=result.findings,
        review_digest=digest,
        reviewer_id=approval_data["reviewer_id"],
        document_ids=registry_plan.get("document_ids", result.request.document_ids),
        document_revisions=registry_plan.get("document_revisions", {}),
        evidence=report_payload.get("evidence", []),
        registry_database=registry_plan.get("registry_database"),
        documents=registry_plan.get("documents"),
        requirements=registry_plan.get("requirements"),
        reviewed_at=approval_data["reviewed_at"],
        qualification_trace=report_payload.get("qualification_trace", []),
    )


def _mode_config() -> dict[str, list[str]]:
    path = _ROOT / "config" / "review_modes.yaml"
    if not path.exists():
        return {key: list(value) for key, value in _DEFAULT_MODES.items()}
    try:
        import yaml
    except ImportError:
        # Keep deterministic imports usable in minimal/offline environments.
        return {key: list(value) for key, value in _DEFAULT_MODES.items()}
    return yaml.safe_load(path.read_text(encoding="utf-8"))["modes"]


def build_production_pipeline(
    review_mode: str = "allin",
    *,
    analysis_adapter: AnalysisAdapter | None = None,
) -> ReviewPipeline:
    modes = _mode_config()
    if review_mode not in modes:
        raise ValueError(f"Unknown review mode: {review_mode}")
    configured_stages = list(modes[review_mode])
    if "finding_qualification" not in configured_stages:
        configured_stages.insert(configured_stages.index("finding_consolidation"), "finding_qualification")
    stages: dict[str, FunctionStage] = {
        "document_control": FunctionStage("document_control", document_control),
        "requirement_extraction": FunctionStage(
            "requirement_extraction", requirement_extraction, ("document_records",)
        ),
        "requirement_triage": FunctionStage(
            "requirement_triage",
            lambda context: triage_requirements(context, review_mode),
            ("requirements",),
        ),
        "revision_change": FunctionStage(
            "revision_change", revision_change, ("document_control",)
        ),
        "evidence_retrieval": FunctionStage(
            "evidence_retrieval",
            build_evidence_index,
            ("requirements", "document_records"),
        ),
        "evidence_analysis": FunctionStage(
            "evidence_analysis",
            lambda context: run_evidence_analysis(
                context, analysis_adapter, mode=review_mode
            ),
            ("requirements", "evidence_engine"),
        ),
        "finding_qualification": FunctionStage(
            "finding_qualification",
            finding_qualification_gate,
            ("findings", "evidence", "requirements"),
        ),
        "compliance_analysis": FunctionStage(
            "compliance_analysis",
            lambda context: _compatibility_analysis(
                context, "compliance", analysis_adapter
            ),
            ("requirements", "evidence_engine"),
        ),
        "truth_check": FunctionStage(
            "truth_check",
            lambda context: _compatibility_analysis(context, "truth", analysis_adapter),
            ("requirements", "evidence_engine"),
        ),
        "engineering_conclusion": FunctionStage(
            "engineering_conclusion",
            lambda _context: {
                "analysis_status": "NOT_CONNECTED",
                "human_review_note": "Engineering conclusion requires a configured model adapter.",
            },
        ),
        "responsibility_assignment": FunctionStage(
            "responsibility_assignment", assign_responsibilities, ("findings",)
        ),
        "finding_consolidation": FunctionStage(
            "finding_consolidation", consolidate_findings, ("findings",)
        ),
        "output_projection": FunctionStage(
            "output_projection",
            build_controlled_outputs,
            ("findings", "responsibility_assignments"),
        ),
        "audit_trail": FunctionStage(
            "audit_trail", build_audit_artifact, ("findings", "controlled_outputs")
        ),
        "finding_registry_reconciliation": FunctionStage(
            "finding_registry_reconciliation",
            finding_registry_reconciliation,
            ("findings", "document_records"),
        ),
        "engineering_qa": FunctionStage(
            "engineering_qa", engineering_qa, ("requirements",)
        ),
        "human_review_gate": FunctionStage(
            "human_review_gate",
            lambda context: _gate(context, review_mode),
            ("qa",),
        ),
    }
    return ReviewPipeline([stages[stage_id] for stage_id in configured_stages])
