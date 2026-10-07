"""Independent, evidence-bound client-intent review records."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING

from .delegation import string_list
from .models import GraphEdge, GraphNode, NodeType, RelationshipType

if TYPE_CHECKING:
    from .service import RegistryService

CRITERIA = {"plan_alignment", "execution_direction", "outcome_alignment"}
STATUSES = {"aligned", "misaligned", "insufficient_information"}


def validate_materiality(context: dict, review: dict) -> None:
    """Validate formal comments against the exact current project context."""
    from .service import text

    comments = review.get("material_comments", [])
    gaps = review.get("information_gaps", [])
    if (
        not isinstance(comments, list)
        or len(comments) > 20
        or not isinstance(gaps, list)
        or len(gaps) > 20
    ):
        raise ValueError("Reviewer materiality records must be bounded lists")
    records = [
        *context["source_snapshot"]["records"],
        *context["client_project_brief"]["source_basis"]["records"],
    ]
    evidence_ids = {
        item["node_id"] for item in records if item["node_type"] == "evidence"
    }
    requirement_ids = {
        item["node_id"] for item in records if item["node_type"] == "requirement"
    }
    brief_sections = (
        context["client_project_brief"].get("definition", {}).get("sections", {})
    )
    objective_ids = {
        entry["statement_id"]
        for entries in brief_sections.values()
        for entry in entries
    }
    allowed_basis = requirement_ids | objective_ids
    comment_fields = {
        "what",
        "evidence_ids",
        "requirement_or_objective_ids",
        "why_it_matters",
        "impact",
        "required_response",
    }
    for comment in comments:
        if not isinstance(comment, dict) or set(comment) != comment_fields:
            raise ValueError("Invalid material Reviewer comment")
        for field in comment_fields - {"evidence_ids", "requirement_or_objective_ids"}:
            text(comment[field], field)
        string_list(comment["evidence_ids"], "material comment evidence", nonempty=True)
        string_list(
            comment["requirement_or_objective_ids"],
            "material comment basis",
            nonempty=True,
        )
        if not set(comment["evidence_ids"]).issubset(evidence_ids) or not set(
            comment["requirement_or_objective_ids"]
        ).issubset(allowed_basis):
            raise ValueError(
                "Material Reviewer comment cites unknown evidence or requirement"
            )
        if not set(comment["evidence_ids"]).issubset(review.get("evidence_ids", [])):
            raise ValueError(
                "Material Reviewer comment evidence must appear in the report"
            )
    if comments and review.get("status") == "aligned":
        raise ValueError("Material Reviewer concern cannot have an aligned verdict")
    gap_fields = {
        "concern",
        "domain",
        "requirement_or_objective_ids",
        "potential_consequence",
        "required_information",
    }
    for gap in gaps:
        if (
            not isinstance(gap, dict)
            or set(gap) != gap_fields
            or gap["domain"] not in {"safety", "regulatory"}
        ):
            raise ValueError("Invalid safety or regulatory information gap")
        for field in gap_fields - {"domain", "requirement_or_objective_ids"}:
            text(gap[field], field)
        string_list(
            gap["requirement_or_objective_ids"], "information gap basis", nonempty=True
        )
        if not set(gap["requirement_or_objective_ids"]).issubset(allowed_basis):
            raise ValueError(
                "Information gap cites unknown project objective or requirement"
            )
    if gaps and (review.get("status") == "aligned" or not review.get("unknowns")):
        raise ValueError(
            "Safety/regulatory information gap requires insufficient information"
        )


def review_criteria(plan: dict, phase: str) -> set[str]:
    criteria = {"plan_alignment"} if phase == "plan" else set(CRITERIA)
    lessons = (plan.get("analysis") or {}).get("lesson_context", [])
    if plan.get("organizational_lesson_ids") or any(
        isinstance(lesson, dict)
        and (lesson.get("attributes") or {}).get("scope") == "imported_organizational"
        for lesson in lessons
    ):
        criteria.add("knowledge_applicability")
    return criteria


def alignment_context(
    registry: RegistryService, project: str, task_id: str, *, phase: str = "outcome"
) -> dict:
    from .service import digest

    registry.check_access(project)
    task = registry.store.get_record(project, "tasks", task_id)
    if phase not in {"plan", "outcome"}:
        raise ValueError("Unknown alignment review phase")
    if not task:
        raise ValueError("Alignment review requires a retained task")
    project_brief = registry.client_project_brief(project)
    if (
        not project_brief
        or (task.get("brain_plan") or {}).get("client_project_brief") != project_brief
    ):
        raise ValueError(
            "Client project brief changed or is missing; prepare a fresh task"
        )
    if phase == "plan":
        if not task:
            raise ValueError("Plan review requires a retained task")
        plan = task.get("brain_plan") or {}
        mandate = registry.client_mandate(
            project, task["issue_id"], exclude_task_id=task_id
        )
        snapshot = registry.task_context(
            project, task["issue_id"], exclude_task_id=task_id
        )["execution_snapshot"]
        if (
            not mandate
            or plan.get("client_mandate") != mandate
            or digest(snapshot) != task["input_digest"]
        ):
            raise ValueError("Plan or client inputs changed; prepare a fresh task")
        return deepcopy(
            {
                "phase": "plan",
                "project_id": project,
                "client_mandate": mandate,
                "client_project_brief": project_brief,
                "task": {
                    key: task[key]
                    for key in (
                        "task_id",
                        "task_digest",
                        "issue_id",
                        "tool",
                        "tool_version",
                        "parameters",
                        "input_digest",
                    )
                },
                "brain_plan": plan,
                "source_snapshot": snapshot,
            }
        )
    if not task or task["state"] != "succeeded" or not task["result_id"]:
        raise ValueError("Alignment review requires a completed task")
    result = registry.get_record(project, task["result_id"])
    plan = task.get("brain_plan", {})
    mandate = registry.client_mandate(
        project, task["issue_id"], exclude_task_id=task_id
    )
    if not mandate or plan.get("client_mandate") != mandate:
        raise ValueError("Client mandate changed or is missing; prepare a fresh task")
    snapshot = registry.task_context(
        project, task["issue_id"], exclude_task_id=task_id
    )["execution_snapshot"]
    attrs = result["attributes"]
    if (
        digest(snapshot) != task["input_digest"]
        or result["node_type"] != NodeType.RESULT
        or attrs.get("task") != task_id
        or attrs.get("inputs", {}).get("task_digest") != task["task_digest"]
        or attrs.get("inputs", {}).get("brain_plan") != plan
    ):
        raise ValueError("Task or result is stale; review current client inputs")
    assessments = [
        record
        for record in registry.list_records(project, "assessment")
        if record["attributes"].get("result_id") == task["result_id"]
        and record["attributes"].get("result_digest") == digest(result)
        and record["attributes"].get("task_digest") == task["task_digest"]
    ]
    if not assessments:
        raise ValueError(
            "Gate 2 requires the retained Document AI assessment for this exact result"
        )
    assessment = sorted(
        assessments, key=lambda record: record["attributes"].get("produced_at", "")
    )[-1]
    return deepcopy(
        {
            "phase": "outcome",
            "project_id": project,
            "client_mandate": mandate,
            "client_project_brief": project_brief,
            "task": task,
            "brain_plan": plan,
            "source_snapshot": snapshot,
            "execution_result": result,
            "document_ai_assessment": assessment,
        }
    )


def record_alignment_review(
    registry: RegistryService,
    project: str,
    task_id: str,
    result_id: str | None,
    review: dict,
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True)
    if registry.principal.role != "ai_reviewer":
        raise PermissionError("Host-configured AI alignment reviewer route required")
    fields = {
        "status",
        "summary",
        "checks",
        "unknowns",
        "evidence_ids",
        "method",
        "model",
        "response_id",
        "request_digest",
    }
    optional = {
        "provider_request_digest",
        "usage",
        "review_comments",
        "material_comments",
        "information_gaps",
        "finding_disproofs",
    }
    if (
        not isinstance(review, dict)
        or not fields.issubset(review)
        or set(review) - fields - optional
    ):
        raise ValueError("Invalid alignment review contract")
    provider_fields = {"provider_request_digest", "usage"}
    if provider_fields & set(review):
        if not provider_fields.issubset(review) or not isinstance(
            review["usage"], dict
        ):
            raise ValueError("Invalid alignment provider provenance")
        text(review["provider_request_digest"], "provider_request_digest")
    if "review_comments" in review:
        string_list(review["review_comments"], "review_comments")
    if "finding_disproofs" in review:
        if not isinstance(review["finding_disproofs"], list):
            raise ValueError("finding_disproofs must be a list")
        seen_disproofs = set()
        for item in review["finding_disproofs"]:
            expected_keys = {"candidate_finding_id", "review_disposition", "rationale", "proposed_qualification", "counter_evidence_ids"}
            if not isinstance(item, dict) or set(item) != expected_keys:
                raise ValueError("Invalid Reviewer finding disproof record")
            text(item["candidate_finding_id"], "candidate_finding_id")
            if item["candidate_finding_id"] in seen_disproofs:
                raise ValueError("Duplicate Reviewer finding disproof")
            seen_disproofs.add(item["candidate_finding_id"])
            if item["review_disposition"] not in {"upheld", "reclassified", "disproved", "insufficient_information"}:
                raise ValueError("Invalid Reviewer finding disproof disposition")
            qualification = item["proposed_qualification"]
            if qualification is not None and qualification not in {
                "deficiency",
                "verification_required",
                "design_development_item",
                "optimisation_item",
                "observation",
                "unknown_insufficient_information",
                "no_issue",
                "positive_assurance",
            }:
                raise ValueError("Invalid proposed finding qualification")
            if item["review_disposition"] in {"reclassified", "disproved"} and qualification is None:
                raise ValueError("A reclassified or disproved finding requires its proposed qualification")
            text(item["rationale"], "finding disproof rationale")
            string_list(item["counter_evidence_ids"], "finding counter-evidence")
            if qualification in {"no_issue", "positive_assurance"} and not item["counter_evidence_ids"]:
                raise ValueError("A no-issue or positive-assurance disproof requires counter-evidence")
            if not set(item["counter_evidence_ids"]).issubset(set(review["evidence_ids"])):
                raise ValueError("Reviewer disproof cites evidence outside its review record")
    for key in ("summary", "method", "model", "request_digest"):
        text(review[key], key)
    if review["response_id"] is not None:
        text(review["response_id"], "response_id")
    if review["status"] not in STATUSES:
        raise ValueError("Unknown alignment review status")
    string_list(review["unknowns"], "unknowns")
    string_list(review["evidence_ids"], "evidence_ids", nonempty=True)
    task = registry.store.get_record(project, "tasks", task_id)
    criteria = review_criteria(
        (task or {}).get("brain_plan") or {},
        "plan" if result_id is None else "outcome",
    )
    checks = review["checks"]
    if not isinstance(checks, list) or len(checks) != len(criteria):
        raise ValueError(
            "Alignment review must contain exactly the criteria for its review phase"
        )
    for check in checks:
        if (
            not isinstance(check, dict)
            or set(check) != {"criterion", "status", "detail", "evidence_ids"}
            or check["criterion"] not in criteria
            or check["status"] not in STATUSES
        ):
            raise ValueError("Invalid alignment review check")
        text(check["detail"], "detail")
        string_list(check["evidence_ids"], "evidence_ids", nonempty=True)
    if {c["criterion"] for c in checks} != criteria:
        raise ValueError("Missing or duplicate alignment check")
    if (
        "knowledge_applicability" in criteria
        and (task or {}).get("brain_plan", {}).get("knowledge_conflicts")
        and not review.get("review_comments")
    ):
        raise ValueError("Knowledge conflict requires an independent Reviewer comment")
    statuses = {c["status"] for c in checks}
    expected = (
        "misaligned"
        if "misaligned" in statuses
        else "insufficient_information"
        if "insufficient_information" in statuses
        else "aligned"
    )
    if review["status"] != expected:
        raise ValueError("Alignment verdict contradicts its checks")
    if len(str(review)) > 50_000:
        raise ValueError("Alignment review exceeds limit")
    with registry.store.transaction():
        phase = "plan" if result_id is None else "outcome"
        context = alignment_context(registry, project, task_id, phase=phase)
        if phase == "outcome":
            assessment = context.get("document_ai_assessment") or {}
            containers = [assessment] if isinstance(assessment, dict) else []
            for key in ("assessment", "attributes", "data"):
                nested = assessment.get(key) if isinstance(assessment, dict) else None
                if isinstance(nested, dict):
                    containers.append(nested)
                    if isinstance(nested.get("assessment"), dict):
                        containers.append(nested["assessment"])
            assessment_candidates: list[dict] = []
            for container in containers:
                findings = container.get("findings")
                if isinstance(findings, list):
                    assessment_candidates = findings
                    break
            expected_candidate_ids = {
                item["finding_id"]
                for item in assessment_candidates
                if isinstance(item, dict) and item.get("finding_id")
            }
            actual_candidate_ids = {
                item["candidate_finding_id"]
                for item in review.get("finding_disproofs", [])
                if isinstance(item, dict)
            }
            if actual_candidate_ids != expected_candidate_ids:
                raise ValueError(
                    "Outcome Reviewer record must challenge every assessed candidate finding"
                )
        if phase == "plan":
            task = registry.store.get_record(project, "tasks", task_id)
            if not task or task["state"] not in {"proposed", "queued"}:
                raise ValueError(
                    "Plan review cannot be recorded after execution starts"
                )
        result = context.get("execution_result")
        if (result is not None and result["node_id"] != result_id) or digest(
            context
        ) != review["request_digest"]:
            raise ValueError(
                "Alignment review inputs changed or request digest is stale"
            )
        validate_materiality(context, review)
        allowed = (
            set(result["attributes"]["evidence_ids"])
            if result
            else {
                node["node_id"]
                for node in context["source_snapshot"]["records"]
                if node["node_type"] == "evidence"
            }
        )
        allowed.update(context["client_project_brief"]["definition"]["evidence_ids"])
        if not set(review["evidence_ids"]).issubset(allowed) or any(
            not set(c["evidence_ids"]).issubset(set(review["evidence_ids"]))
            for c in checks
        ):
            raise ValueError("Unknown/cross-project evidence in alignment review")
        registry._evidence(project, review["evidence_ids"])
        payload = {
            **deepcopy(review),
            "result_id": result_id,
            "result_digest": digest(result) if result else None,
            **(
                {
                    "document_ai_assessment_id": context["document_ai_assessment"][
                        "node_id"
                    ],
                    "document_ai_assessment_digest": digest(
                        context["document_ai_assessment"]
                    ),
                }
                if result is not None
                else {}
            ),
            "phase": phase,
            "task_id": task_id,
            "task_digest": context["task"]["task_digest"],
            "issue_id": context["task"]["issue_id"],
            "client_mandate_digest": context["client_mandate"]["mandate_digest"],
            "client_project_brief_digest": context["client_project_brief"][
                "brief_digest"
            ],
            "review_authority": "host_configured_ai_reviewer",
        }
        rid = "alignment_review:" + digest(payload)
        previous = registry.store.get_node(project, rid)
        if previous:
            return asdict(previous)
        node = GraphNode(
            rid,
            project,
            NodeType.ALIGNMENT_REVIEW,
            review["summary"],
            {
                **payload,
                "produced_by": registry.principal.actor_id,
                "produced_at": timestamp(),
            },
        )
        registry.store.add_subgraph(
            [node],
            [
                *(
                    [
                        GraphEdge(
                            rid + ":result",
                            project,
                            rid,
                            RelationshipType.RELATES_TO,
                            result_id,
                        )
                    ]
                    if result_id
                    else []
                ),
                GraphEdge(
                    rid + ":issue",
                    project,
                    context["task"]["issue_id"],
                    RelationshipType.RELATES_TO,
                    rid,
                ),
                *[
                    GraphEdge(
                        rid + ":evidence:" + eid,
                        project,
                        rid,
                        RelationshipType.SUPPORTED_BY,
                        eid,
                        [eid],
                    )
                    for eid in review["evidence_ids"]
                ],
            ],
        )
        registry._event(
            project,
            context["task"]["issue_id"],
            "alignment_review_recorded",
            alignment_review_id=rid,
            result_id=result_id,
            status=review["status"],
        )
        return asdict(node)


def current_review(
    registry: RegistryService, project: str, task_id: str, *, phase: str = "outcome"
) -> dict | None:
    from .service import digest

    context = alignment_context(registry, project, task_id, phase=phase)
    candidates = [
        node
        for node in registry.list_records(project, "alignment_review")
        if node["attributes"].get("request_digest") == digest(context)
    ]
    return max(candidates, key=lambda n: n["attributes"]["produced_at"], default=None)
