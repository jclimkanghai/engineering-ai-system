"""Evidence-first requirement candidates; humans alone accept them into Registry."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from .models import GraphEdge, GraphNode, NodeType, RelationshipType
from .store import GraphIntegrityError

if TYPE_CHECKING:
    from .service import RegistryService


_FIELDS = {
    "requirement_id",
    "title",
    "source_text",
    "requirement_type",
    "obligation_type",
    "action",
    "source_evidence_id",
    "confidence",
    "uncertainties",
    "extraction_method",
}
_TYPES = {"mandatory", "conditional", "optional", "informational"}
_OBLIGATIONS = {
    "design",
    "deliverable",
    "testing",
    "construction",
    "compliance",
    "other",
}


def _text(value: object, name: str) -> str:
    from .service import text

    return text(value, name)


def _candidate(registry: RegistryService, project: str, value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid requirement candidate contract")
    for field in (
        "requirement_id",
        "title",
        "source_text",
        "source_evidence_id",
        "confidence",
        "extraction_method",
    ):
        _text(value[field], field)
    if (
        value["requirement_type"] not in _TYPES
        or value["obligation_type"] not in _OBLIGATIONS
    ):
        raise ValueError("Invalid requirement classification")
    if value["action"] is not None and not isinstance(value["action"], str):
        raise ValueError("Invalid requirement action")
    if (
        not isinstance(value["uncertainties"], list)
        or len(value["uncertainties"]) > 50
        or any(not isinstance(item, str) for item in value["uncertainties"])
    ):
        raise ValueError("Invalid requirement uncertainties")
    evidence = registry.get_record(project, value["source_evidence_id"])
    if evidence["node_type"] != NodeType.EVIDENCE:
        raise GraphIntegrityError(
            "Requirement candidate requires same-project evidence"
        )
    source = evidence["attributes"].get("text")
    if not isinstance(source, str) or value["source_text"] not in source:
        raise ValueError(
            "Requirement source wording must occur exactly in cited evidence"
        )
    control = registry.source_control(project, [evidence["node_id"]])[0]
    return {
        **value,
        "source_evidence": evidence,
        "source_control": control,
        "human_review_required": True,
    }


def list_requirement_candidates(registry: RegistryService, project: str) -> list[dict]:
    registry.check_access(project)
    return sorted(
        registry.store.list_records(project, "requirement_candidates"),
        key=lambda item: item["candidate_id"],
    )


def propose_requirement_candidate(
    registry: RegistryService, project: str, value: dict
) -> dict:
    from .service import digest, timestamp

    registry.check_access(project, write=True)
    candidate = _candidate(registry, project, value)
    with registry.store.transaction():
        existing = [
            item
            for item in list_requirement_candidates(registry, project)
            if item["candidate"]["requirement_id"] == candidate["requirement_id"]
            and item["candidate"]["source_evidence_id"]
            == candidate["source_evidence_id"]
        ]
        if existing:
            if existing[0]["candidate"]["source_text"] != candidate["source_text"]:
                raise ValueError("Requirement ID has different source wording")
            return existing[0]
        candidate_id = "requirement-candidate:" + digest(candidate)
        record = {
            "candidate_id": candidate_id,
            "project_id": project,
            "candidate": candidate,
            "candidate_digest": digest(candidate),
            "status": "pending_review",
            "proposed_by": registry.principal.actor_id,
            "proposed_at": timestamp(),
        }
        registry.store.put_record(
            project, "requirement_candidates", candidate_id, record
        )
        registry._event(
            project,
            candidate_id,
            "requirement_candidate_proposed",
            source_evidence_id=value["source_evidence_id"],
        )
        return record


def review_requirement_candidate(
    registry: RegistryService,
    project: str,
    candidate_id: str,
    candidate_digest: str,
    accept: bool,
    rationale: str,
) -> dict:
    from .service import text, timestamp

    registry.check_access(project, write=True, human=True)
    rationale = text(rationale, "review rationale")
    if type(accept) is not bool:
        raise ValueError("accept must be boolean")
    with registry.store.transaction():
        record = registry.store.get_record(
            project, "requirement_candidates", candidate_id
        )
        if record is None:
            raise GraphIntegrityError(
                "Requirement candidate does not exist in this project"
            )
        if candidate_digest != record["candidate_digest"]:
            raise ValueError("Stale requirement candidate digest")
        if record["status"] != "pending_review":
            if (
                record.get("accepted") == accept
                and record.get("review_rationale") == rationale
            ):
                return record
            raise ValueError("Requirement candidate already reviewed")
        candidate = record["candidate"]
        current = _candidate(
            registry, project, {key: candidate[key] for key in _FIELDS}
        )
        if current["source_control"] != candidate["source_control"]:
            raise ValueError(
                "Requirement candidate source control changed; extract a fresh candidate"
            )
        reviewed_at = timestamp()
        decision_id = "decision:requirement:" + uuid.uuid4().hex
        requirement_id = "requirement:" + candidate["requirement_id"]
        decision = GraphNode(
            decision_id,
            project,
            NodeType.DECISION,
            rationale,
            {
                "candidate_id": candidate_id,
                "candidate_digest": candidate_digest,
                "candidate_snapshot": candidate,
                "accepted": accept,
                "reviewed_by": registry.principal.actor_id,
                "reviewed_at": reviewed_at,
                "rationale": rationale,
            },
        )
        nodes = [decision]
        edges = [
            GraphEdge(
                decision_id + ":candidate",
                project,
                decision_id,
                RelationshipType.DECIDED_BY,
                candidate["source_evidence_id"],
            ),
        ]
        if accept:
            attrs = {
                "source_text": candidate["source_text"],
                "requirement_type": candidate["requirement_type"],
                "obligation_type": candidate["obligation_type"],
                "action": candidate["action"],
                "source_evidence_ids": [candidate["source_evidence_id"]],
                "provenance": {
                    "document_id": candidate["source_evidence"]["attributes"].get(
                        "document_id"
                    ),
                    "revision": candidate["source_evidence"]["attributes"].get(
                        "revision"
                    ),
                    "page": candidate["source_evidence"]["attributes"].get("page"),
                    "locator": candidate["source_evidence"]["attributes"].get(
                        "locator"
                    ),
                    "extraction_method": candidate["extraction_method"],
                },
                "confidence": candidate["confidence"],
                "uncertainties": candidate["uncertainties"],
                "source_control": candidate["source_control"],
                "authority_status": candidate["source_control"]["status"],
                "review_status": "reviewed",
                "review_decision_id": decision_id,
            }
            node = GraphNode(
                requirement_id, project, NodeType.REQUIREMENT, candidate["title"], attrs
            )
            existing = registry.store.get_node(project, requirement_id)
            if existing is not None and existing != node:
                raise GraphIntegrityError(
                    "Requirement identifier conflicts with existing immutable requirement"
                )
            if existing is None:
                nodes.append(node)
                edges.append(
                    GraphEdge(
                        requirement_id + ":source",
                        project,
                        requirement_id,
                        RelationshipType.SUPPORTED_BY,
                        candidate["source_evidence_id"],
                        [candidate["source_evidence_id"]],
                    )
                )
            edges.append(
                GraphEdge(
                    decision_id + ":requirement",
                    project,
                    decision_id,
                    RelationshipType.DECIDED_BY,
                    requirement_id,
                )
            )
        registry.store.add_subgraph(nodes, edges)
        record.update(
            status="accepted" if accept else "rejected",
            accepted=accept,
            review_rationale=rationale,
            reviewed_by=registry.principal.actor_id,
            reviewed_at=reviewed_at,
            decision_id=decision_id,
            requirement_id=requirement_id if accept else None,
        )
        registry.store.put_record(
            project, "requirement_candidates", candidate_id, record, replace=True
        )
        registry._event(
            project,
            candidate_id,
            "requirement_candidate_reviewed",
            decision_id=decision_id,
            accepted=accept,
        )
        return record
