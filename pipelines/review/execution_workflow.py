"""Document AI's execution feedback entry point, shared with the local desk/MCP.

The lightweight package keeps Registry core independent of review/provider code.
"""

import json
from copy import deepcopy
from dataclasses import asdict

from engineering_ai_system.review_provider import LazyOpenAIAnalysisAdapter
from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_document_ai_brain.errors import AnalysisUnavailable
from engineering_execution import SolverCatalog
from engineering_registry.service import RegistryService, digest
from engineering_registry.store import SQLiteGraphStore
from pipelines.findings import EvidenceRef
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import AnalysisRequest
from pipelines.llm.stage import AnalysisAdapter

from .reviewer_adapter import ReviewerAnalysisAdapter as _ReviewerAnalysisAdapter

__all__ = [
    "DocumentAIWorkflow",
    "_ReviewerAnalysisAdapter",
    "build_automated_workflow",
    "build_reasoning_workflow",
]


def build_reasoning_workflow(
    registry: RegistryService,
    *,
    analysis_adapter: AnalysisAdapter | None,
    alignment_adapter: AnalysisAdapter | None = None,
    decision_registry: RegistryService | None = None,
    delegation_policy_id: str | None = None,
    solver_catalog: SolverCatalog | None = None,
) -> DocumentAIWorkflow:
    """Explicit adapter only: constructing this workflow never selects a paid provider."""
    if analysis_adapter is None:
        raise ValueError("An explicit analysis adapter is required for reasoning")

    def analyse(phase: str, context: dict) -> dict:
        records = context["source_snapshot"]["records"]
        evidence = [
            EvidenceRef(
                evidence_id=n["node_id"],
                document_id=n["attributes"].get("document_id"),
                revision=n["attributes"].get("revision"),
                locator=n["attributes"].get("locator")
                or n["attributes"].get("section")
                or (
                    f"page={n['attributes']['page']}"
                    if n["attributes"].get("page") is not None
                    else None
                ),
                excerpt=n["attributes"].get("text"),
            )
            for n in records
            if n["node_type"] == "evidence"
        ]
        requirement_ids = [
            n["node_id"] for n in records if n["node_type"] == "requirement"
        ]
        if registry.store.path == ":memory:":
            requirement_edges = {
                requirement_id: registry.store.get_edges(
                    context["project_id"], requirement_id
                )
                for requirement_id in requirement_ids
            }
        else:
            # ProjectRun workers own their SQLite connection. Reopen a scoped,
            # thread-local connection here instead of closing over the host's
            # connection from workflow construction. This is a read-only lookup
            # by the already-authorized project ID; do not bind an existing DB
            # that may contain both project and organisation scopes.
            edge_store = SQLiteGraphStore(registry.store.path)
            try:
                requirement_edges = {
                    requirement_id: edge_store.get_edges(
                        context["project_id"], requirement_id
                    )
                    for requirement_id in requirement_ids
                }
            finally:
                edge_store.close()
        requirements = [
            {
                **deepcopy(n["attributes"]),
                "requirement_id": n["node_id"],
                "legacy_source_evidence_ids": deepcopy(
                    n["attributes"].get("source_evidence_ids", [])
                ),
                "source_evidence_ids": sorted(
                    e.target_node_id
                    for e in requirement_edges[n["node_id"]]
                    if e.relationship == "supported_by"
                ),
            }
            for n in records
            if n["node_type"] == "requirement"
        ]
        request = AnalysisRequest(
            mode=phase,
            project_id=context["project_id"],
            document_ids=[
                n["node_id"] for n in records if n["node_type"] == "document"
            ],
            requirements=requirements,
            evidence=[asdict(e) for e in evidence],
            context=deepcopy(context),
            instructions=(
                "Use the retained client_project_brief when present to understand overall project purpose and constraints. "
                "Keep the task scope bounded; inferred client needs are not approved requirements. "
                + (
                    "Analyse the requested task and its bounded source basis."
                    if phase == "execution_planning"
                    else "Assess the supplied V2 output against the exact task, source evidence and requirements."
                )
            )
            + " Separate source facts, interpretation, judgement, assumptions and unknowns. Apply authority in order: mandatory/governing statutory, contractual/client and incorporated code/standard requirements; current project requirements; accepted human/client project decisions; current project evidence; validated organisational knowledge; external/general engineering knowledge. Establish actual applicability, revision and statutory/contractual precedence from supplied records; do not assume a lower tier changes a higher one. Use the retained brain_plan source_control for human-reviewed authority; imported labels alone are observations, and a later revision is not automatically governing. Prefer applicable, validated organisational lessons when developing a solution, but current project requirements, accepted decisions and evidence govern conflicts. For every reused project or imported organisational lesson, return exactly one lesson_reviews entry. First assess relevance: not_relevant means ignore. If relevant, assess applicability: not_applicable means ignore, applicable means use only as a proposed basis, partial means use cautiously and state each limitation. Then check against CURRENT PROJECT REQUIREMENTS and accepted decisions. Set requirements_check to complies only with cited current requirement IDs and project evidence IDs; set conflict with both citations when a conflict is established; use unknown and escalate when the check cannot be established. The review status is one of not_relevant, not_applicable, applicable, partial, conflict or uncertain; requirements_check is respectively not_applicable, not_applicable, complies, complies, conflict or unknown. For a conflict, set resolution to compliant_alternative, deviation_proposed or unresolved and provide a concrete lead_action; set both fields to null for other statuses. Highlight the knowledge and current requirement IDs and explain why the knowledge cannot be directly adopted. Prefer a compliant alternative; require a specific human/client deviation decision only if deviation_proposed. Do not equate conflict discovery with a deviation proposal. Describe any conflict in a finding's conflicts too. Return an empty lesson_reviews list when there are no lessons. Lessons are context, not current source authority. Return evidence-linked proposed findings; no human approval or execution authorization."
            + " Organisational knowledge serves as both a solution accelerator and an early-warning system. A conflict does not establish that the knowledge is wrong. If the lesson is not applicable, use not_applicable. For each true conflict, set conflict_interpretation to project_requirement_controls or possible_requirement_problem; set it to null for other statuses. For a possible_requirement_problem, state a specific requirement_concern and Lead check; otherwise set requirement_concern to null. A possible concern is an early warning, not proof that the project requirement is defective. The current requirement remains governing unless formally changed by the appropriate authority.",
        )
        if (
            len(json.dumps(asdict(request), ensure_ascii=False, allow_nan=False))
            > 500_000
        ):
            raise ValueError("Analysis request exceeds bounded context limit")
        assert analysis_adapter is not None
        try:
            response = analysis_adapter.analyse(deepcopy(request))
        except Exception as exc:
            raise AnalysisUnavailable(
                "Document AI analysis is unavailable; retry assessment. Any completed V2 output remains unverified."
            ) from exc
        if not isinstance(response.findings, list) or len(response.findings) > 100:
            raise ValueError(
                "Document AI finding validation failed: invalid findings list"
            )
        for item in response.findings:
            if not isinstance(item, dict):
                raise ValueError(
                    "Document AI finding validation failed: invalid finding object"
                )
            for key in (
                "source_evidence_ids",
                "requirement_ids",
                "assumptions",
                "uncertainties",
                "conflicts",
            ):
                values = item.get(key, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values
                ):
                    raise ValueError(
                        "Document AI finding validation failed: invalid citation/uncertainty list"
                    )
        findings, errors, _warnings = findings_from_response(
            response, evidence, {r["requirement_id"] for r in requirements}
        )
        if errors:
            raise ValueError(
                "Document AI finding validation failed: " + "; ".join(errors)
            )
        normalized = []
        for finding in findings:
            finding.human_review_required = True
            finding.human_review_reason = (
                finding.human_review_reason
                or "Proposed analysis requires final human verification."
            )
            normalized.append(finding.to_dict())
        task_decisions = (
            deepcopy(getattr(response, "task_decisions", []))
            if phase == "execution_planning"
            else []
        )
        return {
            "producer": "engineering_document_ai",
            "phase": phase,
            "model": response.model,
            "response_id": response.response_id,
            "request_digest": digest(asdict(request)),
            "findings": normalized,
            "task_decisions": task_decisions,
            "usage": deepcopy(response.usage),
            "lesson_context": deepcopy(context["approved_lessons"]),
            "lesson_reviews": deepcopy(response.lesson_reviews),
            "human_review_required": True,
        }

    from .alignment_workflow import build_alignment_reviewer

    analyse.supports_concurrent_calls = (
        registry.store.path != ":memory:"
        and getattr(analysis_adapter, "supports_concurrent_calls", False) is True
    )
    analyse.deterministic_test_adapter = getattr(
        analysis_adapter, "deterministic_test_adapter", False
    )
    reviewer = (
        build_alignment_reviewer(alignment_adapter) if alignment_adapter else None
    )

    return DocumentAIWorkflow(
        registry,
        analysis=analyse,
        alignment_reviewer=reviewer,
        decision_registry=decision_registry,
        delegation_policy_id=delegation_policy_id,
        solver_catalog=solver_catalog,
    )


def build_automated_workflow(
    registry: RegistryService,
    *,
    decision_registry: RegistryService | None = None,
    delegation_policy_id: str | None = None,
    solver_catalog: SolverCatalog | None = None,
) -> DocumentAIWorkflow:
    """Standard application workflow: Document AI plus both automated reviewer gates.

    Provider clients are lazy; constructing the desk/MCP server makes no model call.
    """
    return build_reasoning_workflow(
        registry,
        analysis_adapter=LazyOpenAIAnalysisAdapter(),
        alignment_adapter=LazyOpenAIAnalysisAdapter(),
        decision_registry=decision_registry,
        delegation_policy_id=delegation_policy_id,
        solver_catalog=solver_catalog,
    )


__all__ = [
    "DocumentAIWorkflow",
    "build_automated_workflow",
    "build_reasoning_workflow",
]
