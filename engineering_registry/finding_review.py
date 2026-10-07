"""Attributed human interpretation overlay; original findings remain immutable."""

import uuid

from .models import GraphEdge, GraphNode, NodeType, RelationshipType
from .service import text, timestamp

CLASSIFICATIONS = (
    "DEFICIENCY",
    "VERIFICATION REQUIRED",
    "DESIGN-DEVELOPMENT ITEM",
    "OPTIMISATION ITEM",
    "OBSERVATION",
    "UNKNOWN / INSUFFICIENT INFORMATION",
    "NO ISSUE",
    "PASS / POSITIVE ASSURANCE",
)


def review_finding(
    registry,
    project,
    issue_id,
    snapshot,
    statement,
    classification,
    severity,
    rationale,
):
    registry.check_access(project, write=True, human=True)
    statement, rationale = text(statement, "statement"), text(rationale, "rationale")
    if len(statement) > 6000 or len(rationale) > 2000:
        raise ValueError("Finding review exceeds text limit")
    if classification not in CLASSIFICATIONS or severity not in {
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    }:
        raise ValueError("Invalid finding classification or severity")
    with registry.store.transaction():
        context = registry.issue_context(project, issue_id)
        if snapshot != context["output_digest"]:
            raise ValueError("Finding changed; refresh before recording your amendment")
        decision_id = "decision:finding-review:" + uuid.uuid4().hex
        attributes = {
            "decision_type": "human_finding_review",
            "issue_id": issue_id,
            "statement": statement,
            "classification": classification,
            "severity": severity,
            "rationale": rationale,
            "reviewed_by": registry.principal.actor_id,
            "reasoning_evidence_status": "verified_human_reasoning",
            "reviewed_at": timestamp(),
            "reviewed_context_digest": snapshot,
            "source_issue_snapshot": context["issue"],
            "evidence_ids": context["issue"]["evidence_ids"],
            "authority_limit": "Human interpretation; does not bypass run gates or certify engineering correctness",
        }
        node = GraphNode(
            decision_id, project, NodeType.DECISION, "Human finding review", attributes
        )
        edges = [
            GraphEdge(
                "edge:" + uuid.uuid4().hex,
                project,
                issue_id,
                RelationshipType.DECIDED_BY,
                decision_id,
            )
        ]
        edges.extend(
            GraphEdge(
                "edge:" + uuid.uuid4().hex,
                project,
                decision_id,
                RelationshipType.SUPPORTED_BY,
                evidence_id,
            )
            for evidence_id in attributes["evidence_ids"]
        )
        registry.store.add_subgraph([node], edges)
        registry._event(
            project,
            issue_id,
            "human_finding_reviewed",
            decision_id=decision_id,
            rationale=rationale,
            classification=classification,
            severity=severity,
        )
        return registry.get_record(project, decision_id)
