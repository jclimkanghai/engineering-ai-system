from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import NodeType, RelationshipType
from .store import EngineeringGraphStore, GraphIntegrityError


@dataclass(frozen=True)
class ObjectEvidenceResult:
    evidence_id: str
    document_id: str | None
    revision: str | None
    page: int | None
    section: str | None
    locator: str | None
    text: str | None
    issue_status: str | None
    governing_status: str | None
    requested_revision_match: bool
    task_match_count: int


@dataclass(frozen=True)
class ObjectRetrievalResult:
    issue_id: str
    evidence: list[ObjectEvidenceResult]
    warnings: list[str]


class ObjectCentricRetriever:
    """Retrieve issue-linked evidence while preserving revision conflicts."""

    def __init__(self, store: EngineeringGraphStore) -> None:
        self.store = store

    def retrieve_issue(
        self,
        project_id: str,
        issue_id: str,
        *,
        requested_revision: str | None = None,
        task_query: str = "",
    ) -> ObjectRetrievalResult:
        issue = self.store.get_issue(project_id, issue_id)
        if issue is None:
            raise GraphIntegrityError(
                "Engineering Issue does not exist in this project"
            )
        linked = {
            edge.target_node_id
            for edge in self.store.get_edges(project_id, issue_id)
            if edge.relationship == RelationshipType.SUPPORTED_BY
        }
        if linked != set(issue.evidence_ids):
            raise GraphIntegrityError(
                "Issue evidence links do not match the persisted graph"
            )

        terms = {
            token
            for token in re.findall(r"[a-z0-9]+", task_query.lower())
            if len(token) > 2
        }
        result: list[ObjectEvidenceResult] = []
        revisions: set[str] = set()
        for evidence_id in issue.evidence_ids:
            node = self.store.get_node(project_id, evidence_id)
            if node is None or node.node_type != NodeType.EVIDENCE:
                raise GraphIntegrityError(
                    f"Issue evidence is missing or mistyped: {evidence_id}"
                )
            attrs: dict[str, Any] = node.attributes
            revision = attrs.get("revision")
            revision_text = str(revision) if revision is not None else None
            if revision_text:
                revisions.add(revision_text)
            revision_match = (
                requested_revision is None or revision_text == requested_revision
            )
            searchable = " ".join(
                str(value)
                for value in (node.title, attrs.get("text"), attrs.get("section"))
                if value is not None
            ).lower()
            task_match_count = sum(1 for term in terms if term in searchable)
            result.append(
                ObjectEvidenceResult(
                    evidence_id=evidence_id,
                    document_id=attrs.get("document_id"),
                    revision=revision_text,
                    page=attrs.get("page"),
                    section=attrs.get("section"),
                    locator=attrs.get("locator"),
                    text=attrs.get("text") or attrs.get("excerpt"),
                    issue_status=attrs.get("issue_status"),
                    governing_status=attrs.get("governing_status"),
                    requested_revision_match=revision_match,
                    task_match_count=task_match_count,
                )
            )
        authority_order = {"governing": 0, "current": 1}
        result.sort(
            key=lambda item: (
                not item.requested_revision_match,
                authority_order.get((item.governing_status or "").lower(), 3),
                -item.task_match_count,
                item.evidence_id,
            )
        )
        warnings = []
        if not result:
            warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: no source evidence is linked to this issue."
            )
        if len(revisions) > 1:
            warnings.append(
                "Multiple source revisions are linked; compare their authority before concluding."
            )
        if requested_revision is not None and not any(
            item.requested_revision_match for item in result
        ):
            warnings.append(
                f"No linked evidence matches requested revision {requested_revision}."
            )
        if requested_revision is not None and any(
            not item.requested_revision_match for item in result
        ):
            warnings.append(
                "Other linked revisions remain visible for conflict review."
            )
        return ObjectRetrievalResult(
            issue_id=issue_id, evidence=result, warnings=warnings
        )
