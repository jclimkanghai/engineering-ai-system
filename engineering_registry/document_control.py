"""Human source-authority overlay; imported source observations remain immutable."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING

from .models import GraphEdge, GraphNode, NodeType, RelationshipType
from .store import GraphIntegrityError

if TYPE_CHECKING:
    from .service import RegistryService


def document_control(registry: RegistryService, project: str, source_id: str) -> dict:
    from .service import digest

    source = registry.get_record(project, source_id)
    if source["node_type"] not in {"document", "document_revision"}:
        raise ValueError("Source review requires a document or specific revision")
    pointer = registry.store.get_record(project, "document_control", source_id)
    decision = registry.get_record(project, pointer["decision_id"]) if pointer else None
    if decision and (
        decision["node_type"] != "decision"
        or decision["attributes"].get("source_id") != source_id
        or decision["attributes"].get("source_digest") != digest(source)
    ):
        raise GraphIntegrityError("Source authority pointer has invalid provenance")
    view = {
        "source": source,
        "status": decision["attributes"]["authority"] if decision else "unverified",
        "decision_id": decision["node_id"] if decision else None,
        "decision": decision,
    }
    return {**view, "control_digest": digest(view)}


def review_document_control(
    registry: RegistryService,
    project: str,
    source_id: str,
    control_digest: str,
    authority: str,
    evidence_ids: list[str],
    rationale: str,
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True, human=True)
    rationale = text(rationale, "review rationale")
    if authority not in {"governing", "current", "superseded", "unverified"}:
        raise ValueError("Invalid source authority")
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or len(evidence_ids) > 100
        or any(not isinstance(e, str) or not e.strip() for e in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
    ):
        raise ValueError("Source review requires bounded unique supporting evidence")
    with registry.store.transaction():
        view = document_control(registry, project, source_id)
        if view["source"]["node_type"] == "document" and any(
            edge.relationship == RelationshipType.HAS_REVISION
            for edge in registry.store.get_edges(project, source_id)
        ):
            raise ValueError("Review a specific document revision")
        if view["control_digest"] != control_digest:
            raise ValueError(
                "Source control changed since display; refresh and review again"
            )
        registry._evidence(project, evidence_ids)
        basis = [registry.get_record(project, eid) for eid in evidence_ids]
        decision_id = "decision:source:" + uuid.uuid4().hex
        node = GraphNode(
            decision_id,
            project,
            NodeType.DECISION,
            rationale,
            {
                "source_id": source_id,
                "source_digest": digest(view["source"]),
                "source_snapshot": view["source"],
                "authority": authority,
                "previous_decision_id": view["decision_id"],
                "reviewed_control_digest": control_digest,
                "evidence_ids": evidence_ids,
                "evidence_snapshot": basis,
                "reviewed_by": registry.principal.actor_id,
                "reviewed_at": timestamp(),
                "rationale": rationale,
            },
        )
        registry.store.add_subgraph(
            [node],
            [
                GraphEdge(
                    decision_id + ":source",
                    project,
                    source_id,
                    RelationshipType.DECIDED_BY,
                    decision_id,
                ),
                *[
                    GraphEdge(
                        decision_id + ":basis:" + eid,
                        project,
                        decision_id,
                        RelationshipType.SUPPORTED_BY,
                        eid,
                        [eid],
                    )
                    for eid in evidence_ids
                ],
            ],
        )
        registry.store.put_record(
            project,
            "document_control",
            source_id,
            {"decision_id": decision_id},
            replace=True,
        )
        registry._event(
            project,
            source_id,
            "source_authority_reviewed",
            decision_id=decision_id,
            authority=authority,
            evidence_ids=evidence_ids,
            rationale=rationale,
        )
        return asdict(node)


def source_control(
    registry: RegistryService, project: str, evidence_ids: list[str]
) -> list[dict]:
    registry.check_access(project)
    registry._evidence(project, evidence_ids)
    result = []
    for eid in sorted(set(evidence_ids)):
        evidence = registry.get_record(project, eid)
        sources = []
        for edge in registry.store.get_edges(project, eid):
            if edge.relationship == RelationshipType.DERIVED_FROM:
                target = registry.get_record(project, edge.target_node_id)
                if target["node_type"] in {"document", "document_revision"}:
                    sources.append(target)
        revisions = [s for s in sources if s["node_type"] == "document_revision"]
        sources = revisions or sources
        view = (
            document_control(registry, project, sources[0]["node_id"])
            if len(sources) == 1
            else None
        )
        status = view["status"] if view else "unverified"
        result.append(
            {
                "evidence_id": eid,
                "source_id": sources[0]["node_id"] if view else None,
                "status": status,
                "decision_id": view["decision_id"] if view else None,
                "control_digest": view["control_digest"] if view else None,
                "imported_status": evidence["attributes"].get("governing_status"),
                "warning": "UNKNOWN / INSUFFICIENT INFORMATION: source authority is not human-confirmed."
                if status == "unverified"
                else "Source is superseded; retain only as historical/comparison evidence."
                if status == "superseded"
                else "Human-reviewed source status; technical adequacy and hierarchy still require review.",
            }
        )
    return result
