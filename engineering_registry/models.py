from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    PROJECT = "project"
    SYSTEM = "system"
    COMPONENT = "component"
    DOCUMENT = "document"
    DOCUMENT_REVISION = "document_revision"
    EVIDENCE = "evidence"
    REQUIREMENT = "requirement"
    PARAMETER = "parameter"
    ASSUMPTION = "assumption"
    ENGINEERING_ISSUE = "engineering_issue"
    ACTION = "action"
    DECISION = "decision"
    SUBMISSION = "submission"
    LESSON = "lesson"
    FINDING = "finding"
    RESULT = "result"
    TASK = "task"
    RISK = "risk"
    VERIFICATION = "verification"
    ASSESSMENT = "assessment"
    ALIGNMENT_REVIEW = "alignment_review"


class RelationshipType(StrEnum):
    CONTAINS = "contains"
    HAS_REVISION = "has_revision"
    SUPPORTED_BY = "supported_by"
    DERIVED_FROM = "derived_from"
    ADDRESSES = "addresses"
    AFFECTS = "affects"
    ASSIGNED_TO = "assigned_to"
    DECIDED_BY = "decided_by"
    RESOLVED_BY = "resolved_by"
    CLOSED_WITH_EVIDENCE = "closed_with_evidence"
    SUPERSEDES = "supersedes"
    RELATES_TO = "relates_to"
    LEARNED_FROM = "learned_from"


class IssueStatus(StrEnum):
    PROPOSED = "proposed"
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    HELD = "held"
    CLOSED = "closed"


class DecisionLevel(StrEnum):
    """Proposed authority tier; it never replaces authenticated human control."""

    D0 = "D0"  # execution sequencing
    D1 = "D1"  # reversible working assumption
    D2 = "D2"  # material assumption; human review
    D3 = "D3"  # design/interface decision; human acceptance
    D4 = "D4"  # safety/regulatory/contractual matter; formal authority


def _require_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_unique_ids(field_name: str, values: list[str]) -> None:
    for value in values:
        _require_text(field_name, value)
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} contains duplicate IDs")


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    project_id: str
    node_type: NodeType
    title: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("node_id", self.node_id)
        _require_text("project_id", self.project_id)
        _require_text("title", self.title)
        object.__setattr__(self, "node_type", NodeType(self.node_type))
        object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    project_id: str
    source_node_id: str
    relationship: RelationshipType
    target_node_id: str
    provenance_evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text("edge_id", self.edge_id)
        _require_text("project_id", self.project_id)
        _require_text("source_node_id", self.source_node_id)
        _require_text("target_node_id", self.target_node_id)
        if self.source_node_id == self.target_node_id:
            raise ValueError("An edge cannot link a node to itself")
        object.__setattr__(self, "relationship", RelationshipType(self.relationship))
        provenance = list(self.provenance_evidence_ids)
        _validate_unique_ids("provenance_evidence_ids", provenance)
        object.__setattr__(self, "provenance_evidence_ids", provenance)


@dataclass(frozen=True)
class EngineeringIssue:
    issue_id: str
    project_id: str
    title: str
    question: str | None = None
    context: str | None = None
    source_facts: list[str] = field(default_factory=list)
    interpretation: str | None = None
    engineering_judgement: str | None = None
    assumptions: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    finding: str | None = None
    impact: str | None = None
    recommendation: str | None = None
    required_action: str | None = None
    risk_level: str = "unknown"
    risk_dimensions: list[str] = field(default_factory=list)
    confidence: str = "unknown"
    source_document_ids: list[str] = field(default_factory=list)
    responsibility_assignment_ids: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    parameter_ids: list[str] = field(default_factory=list)
    related_entity_ids: list[str] = field(default_factory=list)
    action_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    closure_evidence_ids: list[str] = field(default_factory=list)
    status: IssueStatus = IssueStatus.PROPOSED
    human_review_required: bool = True
    human_review_reason: str | None = None
    source_finding_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("issue_id", self.issue_id)
        _require_text("project_id", self.project_id)
        _require_text("title", self.title)
        object.__setattr__(self, "status", IssueStatus(self.status))
        for field_name in (
            "source_facts",
            "assumptions",
            "unknowns",
            "risk_dimensions",
            "evidence_ids",
            "requirement_ids",
            "parameter_ids",
            "source_document_ids",
            "responsibility_assignment_ids",
            "related_entity_ids",
            "action_ids",
            "decision_ids",
            "closure_evidence_ids",
            "conflicts",
        ):
            values = list(getattr(self, field_name))
            if field_name.endswith("_ids"):
                _validate_unique_ids(field_name, values)
            elif any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"{field_name} cannot contain blank values")
            object.__setattr__(self, field_name, values)
        object.__setattr__(self, "attributes", dict(self.attributes))
