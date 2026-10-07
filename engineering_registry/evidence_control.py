"""Human evidence-review overlay; imported evidence remains immutable."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING

from .models import GraphEdge, GraphNode, NodeType, RelationshipType
from .store import GraphIntegrityError

if TYPE_CHECKING:
    from .service import RegistryService


_STATUSES = {"visually_reviewed", "insufficient", "not_reviewable"}


def evidence_review(registry: RegistryService, project: str, evidence_id: str) -> dict:
    """Return human review state without treating imported text as human confirmation."""
    from .service import digest

    evidence = registry.get_record(project, evidence_id)
    if evidence["node_type"] != NodeType.EVIDENCE:
        raise ValueError("Evidence review requires an evidence record")
    pointer = registry.store.get_record(project, "evidence_control", evidence_id)
    decision = registry.get_record(project, pointer["decision_id"]) if pointer else None
    if decision and (
        decision["node_type"] != NodeType.DECISION
        or decision["attributes"].get("evidence_id") != evidence_id
        or decision["attributes"].get("evidence_digest") != digest(evidence)
    ):
        raise GraphIntegrityError("Evidence review pointer has invalid provenance")
    status = (
        decision["attributes"]["status"]
        if decision
        else "unreviewed_text_available"
        if isinstance(evidence["attributes"].get("text"), str)
        and evidence["attributes"]["text"].strip()
        else "unreviewed_text_missing"
    )
    view = {
        "evidence_id": evidence_id,
        "evidence": evidence,
        "status": status,
        "human_reviewed": decision is not None,
        "decision_id": decision["node_id"] if decision else None,
        "decision": decision,
    }
    return {**view, "review_digest": digest(view)}


def evidence_review_context(
    registry: RegistryService, project: str, evidence_ids: list[str]
) -> list[dict]:
    registry.check_access(project)
    registry._evidence(project, evidence_ids)
    return [
        evidence_review(registry, project, evidence_id)
        for evidence_id in sorted(set(evidence_ids))
    ]


def review_evidence(
    registry: RegistryService,
    project: str,
    evidence_id: str,
    review_digest: str,
    status: str,
    rationale: str,
) -> dict:
    """Record one attributed human evidence-reading result and retain its basis."""
    from .service import digest, text, timestamp

    registry.check_access(project, write=True, human=True)
    rationale = text(rationale, "review rationale")
    if status not in _STATUSES:
        raise ValueError("Invalid evidence review status")
    with registry.store.transaction():
        view = evidence_review(registry, project, evidence_id)
        if view["review_digest"] != review_digest:
            raise ValueError(
                "Evidence review changed since display; refresh and review again"
            )
        decision_id = "decision:evidence:" + uuid.uuid4().hex
        node = GraphNode(
            decision_id,
            project,
            NodeType.DECISION,
            rationale,
            {
                "evidence_id": evidence_id,
                "evidence_digest": digest(view["evidence"]),
                "evidence_snapshot": view["evidence"],
                "status": status,
                "previous_decision_id": view["decision_id"],
                "reviewed_review_digest": review_digest,
                "reviewed_by": registry.principal.actor_id,
                "reviewed_at": timestamp(),
                "rationale": rationale,
            },
        )
        registry.store.add_subgraph(
            [node],
            [
                GraphEdge(
                    decision_id + ":evidence",
                    project,
                    evidence_id,
                    RelationshipType.DECIDED_BY,
                    decision_id,
                )
            ],
        )
        registry.store.put_record(
            project,
            "evidence_control",
            evidence_id,
            {"decision_id": decision_id},
            replace=True,
        )
        registry._event(
            project,
            evidence_id,
            "evidence_reviewed",
            decision_id=decision_id,
            status=status,
            rationale=rationale,
        )
        return asdict(node)
