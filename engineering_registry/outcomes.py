"""Controlled output assessments and human dispositions; no brain dependency."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .models import GraphEdge, GraphNode, NodeType, RelationshipType


def require_outcome_review(
    registry, project: str, result: dict, *, require_aligned: bool
) -> dict | None:
    plan = result.get("attributes", {}).get("inputs", {}).get("brain_plan") or {}
    if not plan.get("alignment_review_required"):
        return None
    from .alignment import current_review

    task_id = result["attributes"].get("task")
    review = current_review(registry, project, task_id, phase="outcome")
    if not review:
        raise ValueError(
            "Gate 2 project-assurance review is required before human decision"
        )
    if require_aligned and (
        review["attributes"]["status"] != "aligned" or review["attributes"]["unknowns"]
    ):
        raise ValueError(
            "Gate 2 must be aligned with no unknowns before acceptance/verification"
        )
    return review


if TYPE_CHECKING:
    from .service import RegistryService


def validated_assessment(
    registry: RegistryService,
    project: str,
    result: dict,
    assessment_id: str | None,
    assessment_digest: str | None,
) -> dict:
    from .service import digest

    if not assessment_id or not assessment_digest:
        raise ValueError(
            "Document AI assessment and reviewed assessment digest are required"
        )
    assessment = registry.get_record(project, assessment_id)
    attrs = assessment["attributes"]
    if (
        assessment["node_type"] != NodeType.ASSESSMENT
        or digest(assessment) != assessment_digest
        or attrs.get("result_id") != result["node_id"]
        or attrs.get("result_digest") != digest(result)
    ):
        raise ValueError("Stale assessment or assessment belongs to another result")
    return assessment


def needs_assessment(registry: RegistryService, project: str, result: dict) -> bool:
    attrs = result["attributes"]
    task = registry.store.get_record(project, "tasks", attrs.get("task", "")) or {}
    return bool(attrs.get("inputs", {}).get("brain_plan") or task.get("brain_plan"))


def record_assessment(
    registry: RegistryService,
    project: str,
    result_id: str,
    result_digest: str,
    assessment: dict[str, Any],
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True)
    required = {
        "task_digest",
        "summary",
        "checks",
        "unknowns",
        "recommendation",
        "evidence_ids",
        "method",
    }
    if (
        not isinstance(assessment, dict)
        or not required.issubset(assessment)
        or set(assessment) - required - {"analysis"}
    ):
        raise ValueError("Invalid assessment contract")
    for key in ("task_digest", "summary", "method"):
        text(assessment[key], key)
    if assessment["recommendation"] not in {"human_review", "rework", "hold"}:
        raise ValueError("Assessment cannot grant human approval")
    checks = assessment["checks"]
    if not isinstance(checks, list) or not checks or len(checks) > 20:
        raise ValueError("Assessment requires bounded acceptance checks")
    for check in checks:
        if (
            not isinstance(check, dict)
            or set(check) != {"criterion", "passed", "detail"}
            or type(check["passed"]) is not bool
        ):
            raise ValueError("Invalid assessment check")
        text(check["criterion"], "criterion")
        text(check["detail"], "detail")
    for field in ("unknowns", "evidence_ids"):
        values = assessment[field]
        if not isinstance(values, list) or len(values) > 200:
            raise ValueError("Invalid assessment references/unknowns")
        for value in values:
            text(value, field)
    with registry.store.transaction():
        result = registry.get_record(project, result_id)
        attrs = result["attributes"]
        if result["node_type"] != NodeType.RESULT or digest(result) != result_digest:
            raise ValueError("Stale result digest or wrong record type")
        inputs = attrs.get("inputs", {})
        if inputs.get("task_digest") != assessment["task_digest"]:
            raise ValueError("Assessment task does not match execution result")
        plan = inputs.get("brain_plan")
        if plan and "analysis" in plan and "analysis" not in assessment:
            if any(
                c["criterion"] == "engineering_review" and c["passed"] for c in checks
            ):
                raise ValueError(
                    "Engineering review cannot pass without reasoning analysis provenance"
                )
        if "analysis" in assessment:
            from .analysis import validate_analysis

            validate_analysis(
                assessment["analysis"],
                "execution_assessment",
                attrs["evidence_ids"],
                (plan or {}).get("requirement_ids", []),
            )
            unresolved = any(
                finding["status"] in {"potential", "unverified", "disputed"}
                or finding["risk_level"] in {"high", "critical"}
                for finding in assessment["analysis"]["findings"]
            )
            if unresolved and any(
                c["criterion"] == "engineering_review" and c["passed"] for c in checks
            ):
                raise ValueError(
                    "Unresolved model findings cannot pass engineering review"
                )
            if not plan or "analysis" not in plan:
                raise ValueError(
                    "Model output assessment requires a reasoning task contract"
                )
            if (
                assessment["analysis"]["lesson_context"]
                != plan["analysis"]["lesson_context"]
            ):
                raise ValueError("Assessment must retain the planning lesson context")
        if plan and sorted(c["criterion"] for c in checks) != sorted(
            plan["acceptance_criteria"]
        ):
            raise ValueError(
                "Assessment must cover the task acceptance criteria exactly"
            )
        if not set(assessment["evidence_ids"]).issubset(attrs["evidence_ids"]):
            raise ValueError("Assessment evidence must belong to the result")
        registry._evidence(project, assessment["evidence_ids"])
        aid = "assessment:" + digest(
            {
                "result": result_id,
                "result_digest": result_digest,
                "assessment": assessment,
            }
        )
        previous = registry.store.get_node(project, aid)
        if previous:
            return asdict(previous)
        node = GraphNode(
            aid,
            project,
            NodeType.ASSESSMENT,
            assessment["summary"],
            {
                **assessment,
                "result_id": result_id,
                "result_digest": result_digest,
                "issue_id": attrs["issue_id"],
                "produced_by": registry.principal.actor_id,
                "produced_at": timestamp(),
                "human_review_required": True,
            },
        )
        registry.store.add_subgraph(
            [node],
            [
                GraphEdge(
                    aid + ":result",
                    project,
                    aid,
                    RelationshipType.RELATES_TO,
                    result_id,
                ),
                GraphEdge(
                    aid + ":issue",
                    project,
                    attrs["issue_id"],
                    RelationshipType.RELATES_TO,
                    aid,
                ),
            ],
        )
        registry._event(
            project,
            attrs["issue_id"],
            "output_assessed",
            assessment_id=aid,
            result_id=result_id,
        )
        return asdict(node)


def decide_result(
    registry: RegistryService,
    project: str,
    result_id: str,
    result_digest: str,
    assessment_id: str,
    assessment_digest: str,
    disposition: str,
    evidence_ids: list[str],
    rationale: str,
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True, human=True)
    rationale = text(rationale, "rationale")
    if disposition not in {"accept", "reject", "rework", "hold"}:
        raise ValueError("Invalid human disposition")
    if not evidence_ids:
        raise ValueError("Human disposition requires verification evidence")
    with registry.store.transaction():
        result = registry.get_record(project, result_id)
        if result["node_type"] != NodeType.RESULT or digest(result) != result_digest:
            raise ValueError("Stale result digest")
        assessment = validated_assessment(
            registry, project, result, assessment_id, assessment_digest
        )
        require_outcome_review(
            registry, project, result, require_aligned=disposition == "accept"
        )
        registry._evidence(project, evidence_ids)
        payload = {
            "result_id": result_id,
            "result_digest": result_digest,
            "assessment_id": assessment_id,
            "assessment_digest": assessment_digest,
            "disposition": disposition,
            "evidence_ids": evidence_ids,
            "rationale": rationale,
            "reviewed_by": registry.principal.actor_id,
            "issue_id": result["attributes"]["issue_id"],
            "reasoning_evidence_status": "verified_human_reasoning",
            "reasoning_evidence_ids": evidence_ids,
        }
        did = "decision:output:" + digest(payload)
        previous = registry.store.get_node(project, did)
        if previous:
            return asdict(previous)
        if disposition == "accept":
            if not all(c["passed"] for c in assessment["attributes"]["checks"]):
                raise ValueError(
                    "Failed assessment checks require rework or hold before acceptance"
                )
            registry.verify_result(
                project,
                result_id,
                result_digest,
                evidence_ids,
                rationale,
                assessment_id=assessment_id,
                assessment_digest=assessment_digest,
            )
        node = GraphNode(
            did,
            project,
            NodeType.DECISION,
            rationale,
            {**payload, "reviewed_at": timestamp()},
        )
        registry.store.add_subgraph(
            [node],
            [
                GraphEdge(
                    did + ":issue",
                    project,
                    payload["issue_id"],
                    RelationshipType.DECIDED_BY,
                    did,
                ),
                GraphEdge(
                    did + ":result",
                    project,
                    did,
                    RelationshipType.RELATES_TO,
                    result_id,
                ),
                GraphEdge(
                    did + ":assessment",
                    project,
                    did,
                    RelationshipType.RELATES_TO,
                    assessment_id,
                ),
                *[
                    GraphEdge(
                        did + ":evidence:" + evidence_id,
                        project,
                        did,
                        RelationshipType.SUPPORTED_BY,
                        evidence_id,
                        [evidence_id],
                    )
                    for evidence_id in evidence_ids
                ],
            ],
        )
        registry._event(
            project,
            payload["issue_id"],
            "output_decided",
            decision_id=did,
            result_id=result_id,
            disposition=disposition,
        )
        return asdict(node)
