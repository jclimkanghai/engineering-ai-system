from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from .models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    IssueStatus,
    NodeType,
    RelationshipType,
)
from .store import EngineeringGraphStore


class FindingImportError(ValueError):
    """A V1 finding is not safe to project into an Engineering Issue."""


def _dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise FindingImportError("Finding must be a V1 Finding model or mapping")


def _value(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def finding_to_issue(
    finding: Any,
    project_id: str,
    *,
    evidence_by_id: dict[str, Any],
    validation_errors: list[str],
) -> EngineeringIssue:
    """Project a validated V1 finding without inventing absent source details.

    The caller must pass the result of the V1 citation/schema validation step.
    A non-empty validation error list blocks import. Every cited evidence ID
    must also resolve in ``evidence_by_id``.
    """
    if validation_errors:
        raise FindingImportError("Finding has validation errors and cannot be imported")
    data = _dict(finding)
    finding_id = str(data.get("finding_id") or "").strip()
    if not finding_id:
        raise FindingImportError("Validated finding has no finding_id")
    evidence_ids = list(data.get("source_evidence_ids") or [])
    unresolved = [item for item in evidence_ids if item not in evidence_by_id]
    if unresolved:
        raise FindingImportError(
            f"Finding cites unresolved evidence IDs: {', '.join(unresolved)}"
        )

    source_facts: list[str] = []
    document_ids: list[str] = []
    unknowns = list(data.get("uncertainties") or [])
    conflicts = list(data.get("conflicts") or [])
    for evidence_id in evidence_ids:
        evidence = evidence_by_id[evidence_id]
        text = evidence.get("text") or evidence.get("excerpt")
        if isinstance(text, str) and text.strip():
            source_facts.append(text.strip())
        document_id = evidence.get("document_id")
        if (
            isinstance(document_id, str)
            and document_id.strip()
            and document_id not in document_ids
        ):
            document_ids.append(document_id)
    if not evidence_ids:
        unknowns.append("No source evidence link was supplied by the V1 finding.")
    elif not source_facts:
        unknowns.append(
            "Cited evidence has no extracted text or excerpt in the import context."
        )

    return EngineeringIssue(
        issue_id=f"v1-finding:{project_id}:{finding_id}",
        project_id=project_id,
        title=str(data.get("title") or data.get("finding") or finding_id),
        question=data.get("question"),
        source_facts=source_facts,
        interpretation=data.get("interpretation"),
        assumptions=list(data.get("assumptions") or []),
        unknowns=unknowns,
        finding=data.get("finding"),
        impact=data.get("impact"),
        recommendation=data.get("recommendation"),
        required_action=data.get("required_action"),
        risk_level=_value(data.get("risk_level")),
        risk_dimensions=list(data.get("risk_dimensions") or []),
        confidence=_value(data.get("confidence")),
        evidence_ids=evidence_ids,
        requirement_ids=list(data.get("requirement_ids") or []),
        source_document_ids=document_ids,
        responsibility_assignment_ids=list(
            data.get("responsibility_assignment_ids") or []
        ),
        conflicts=conflicts,
        status=IssueStatus.PROPOSED,
        human_review_required=True,
        human_review_reason=(
            data.get("human_review_reason")
            or "V1 finding requires competent human review."
        ),
        source_finding_id=finding_id,
        attributes={
            key: data.get(key)
            for key in (
                "steelman",
                "critic",
                "gap",
                "technical_query_id",
                "risk_record_id",
                "decision_record_id",
                "output_status",
            )
            if data.get(key) is not None
        },
    )


def import_finding(
    store: EngineeringGraphStore,
    finding: Any,
    project_id: str,
    *,
    evidence_by_id: dict[str, Any],
    validation_errors: list[str],
) -> EngineeringIssue:
    """Persist a validated V1 finding and its supplied source provenance atomically."""
    issue = finding_to_issue(
        finding,
        project_id,
        evidence_by_id=evidence_by_id,
        validation_errors=validation_errors,
    )
    supporting_nodes: dict[str, GraphNode] = {}
    supporting_edges: list[GraphEdge] = []

    def add_if_absent(node: GraphNode) -> None:
        existing = store.get_node(project_id, node.node_id)
        if existing is not None:
            if existing.node_type != node.node_type:
                raise FindingImportError(
                    f"Graph node {node.node_id} has an incompatible type"
                )
            if node.node_type != NodeType.REQUIREMENT and existing != node:
                raise FindingImportError(
                    f"Graph node {node.node_id} conflicts with existing source content"
                )
            return
        supporting_nodes[node.node_id] = node

    for evidence_id in issue.evidence_ids:
        source = _dict(evidence_by_id[evidence_id])
        evidence_title = source.get("title") or evidence_id
        add_if_absent(
            GraphNode(
                evidence_id,
                project_id,
                NodeType.EVIDENCE,
                str(evidence_title),
                source,
            )
        )
        document_id = source.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            continue
        document_node_id = f"document:{document_id}"
        add_if_absent(
            GraphNode(
                document_node_id,
                project_id,
                NodeType.DOCUMENT,
                str(
                    source.get("document_title")
                    or source.get("document_number")
                    or document_id
                ),
                {
                    "document_id": document_id,
                    "document_number": source.get("document_number"),
                },
            )
        )
        revision = source.get("revision")
        if revision is None or not str(revision).strip():
            supporting_edges.append(
                GraphEdge(
                    f"{evidence_id}:document:{document_id}",
                    project_id,
                    evidence_id,
                    RelationshipType.DERIVED_FROM,
                    document_node_id,
                    [evidence_id],
                )
            )
            continue
        revision_text = str(revision)
        revision_node_id = f"document-revision:{document_id}:{revision_text}"
        add_if_absent(
            GraphNode(
                revision_node_id,
                project_id,
                NodeType.DOCUMENT_REVISION,
                f"{document_id} revision {revision_text}",
                {
                    "document_id": document_id,
                    "revision": revision_text,
                    "revision_date": source.get("revision_date"),
                    "issue_status": source.get("issue_status"),
                    "governing_status": source.get("governing_status"),
                },
            )
        )
        supporting_edges.extend(
            [
                GraphEdge(
                    f"{document_node_id}:revision:{revision_text}",
                    project_id,
                    document_node_id,
                    RelationshipType.HAS_REVISION,
                    revision_node_id,
                ),
                GraphEdge(
                    f"{evidence_id}:revision:{revision_text}",
                    project_id,
                    evidence_id,
                    RelationshipType.DERIVED_FROM,
                    revision_node_id,
                    [evidence_id],
                ),
            ]
        )

    for requirement_id in issue.requirement_ids:
        add_if_absent(
            GraphNode(
                requirement_id,
                project_id,
                NodeType.REQUIREMENT,
                requirement_id,
                {
                    "resolution_status": "unresolved_reference",
                    "source": "validated_v1_finding",
                },
            )
        )
    store.save_issue(
        issue,
        supporting_nodes=list(supporting_nodes.values()),
        supporting_edges=supporting_edges,
    )
    return issue
