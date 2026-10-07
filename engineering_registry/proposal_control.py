"""Immutable proposal-readiness submissions and bounded source snapshots."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .models import GraphEdge, GraphNode, NodeType, RelationshipType
from .store import GraphIntegrityError

if TYPE_CHECKING:
    from .service import RegistryService


_DISPOSITIONS = {
    "included",
    "excluded",
    "interface_only",
    "qualification",
    "unresolved",
}
_MATRIX_FIELDS = {
    "matrix_id",
    "requirement_id",
    "disposition",
    "proposal_evidence_ids",
    "qualification",
    "unresolved_question",
    "owner",
}
_PROTECTED_FIELDS = {"template_evidence_id", "candidate_evidence_id", "label"}
_MAX_SOURCE_RECORDS = 200
_MAX_SNAPSHOT_CHARS = 500_000


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{name} must be bounded nonblank text")
    return value.strip()


def _ids(values: object, name: str, *, maximum: int = _MAX_SOURCE_RECORDS) -> list[str]:
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError(f"{name} must be a bounded list")
    result = [_text(value, name) for value in values]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicate IDs")
    return result


def _has_revision(
    registry: RegistryService, project: str, evidence_id: str, revision_id: str
) -> bool:
    return any(
        edge.relationship == RelationshipType.DERIVED_FROM
        and edge.target_node_id == revision_id
        for edge in registry.store.get_edges(project, evidence_id)
    )


def _submission_payload(
    registry: RegistryService,
    project: str,
    submission_id: str,
    title: str,
    issue_id: str,
    candidate_revision_id: str,
    template_revision_id: str,
    source_requirement_ids: list[str],
    protected_sections: list[dict[str, Any]],
    scope_matrix: list[dict[str, Any]],
) -> dict[str, Any]:
    from .service import digest

    submission_id = _text(submission_id, "submission_id")
    title = _text(title, "title")
    issue_id = _text(issue_id, "issue_id")
    candidate_revision_id = _text(candidate_revision_id, "candidate_revision_id")
    template_revision_id = _text(template_revision_id, "template_revision_id")
    if candidate_revision_id == template_revision_id:
        raise ValueError("Candidate and template revisions must be distinct")
    source_requirement_ids = _ids(source_requirement_ids, "source_requirement_ids")
    if not isinstance(scope_matrix, list) or not 1 <= len(scope_matrix) <= 200:
        raise ValueError("scope_matrix must contain between 1 and 200 rows")
    if (
        not isinstance(protected_sections, list)
        or not 1 <= len(protected_sections) <= 20
    ):
        raise ValueError("protected_sections must contain between 1 and 20 pairs")

    issue_record = registry.store.get_node(project, issue_id)
    issue = registry.store.get_issue(project, issue_id)
    if (
        issue_record is None
        or issue_record.node_type != NodeType.ENGINEERING_ISSUE
        or issue is None
        or issue.attributes.get("work_type") != "proposal_readiness"
    ):
        raise GraphIntegrityError(
            "Submission requires a same-project proposal-readiness issue"
        )

    for revision_id in (candidate_revision_id, template_revision_id):
        revision = registry.store.get_node(project, revision_id)
        if revision is None or revision.node_type != NodeType.DOCUMENT_REVISION:
            raise GraphIntegrityError(
                "Submission revision must be a same-project document revision"
            )
    candidate_revision = registry.get_record(project, candidate_revision_id)
    template_revision = registry.get_record(project, template_revision_id)
    if not candidate_revision["attributes"].get("revision") or not template_revision[
        "attributes"
    ].get("revision"):
        raise GraphIntegrityError(
            "Submission revisions require observed revision identifiers"
        )

    issue_context = registry.issue_context(project, issue_id, include_results=False)
    accepted_issue_requirements = set(issue_context["source_context_requirement_ids"])
    if set(source_requirement_ids) != accepted_issue_requirements:
        raise GraphIntegrityError(
            "Submission requirements must exactly cover the linked issue requirements"
        )
    for requirement_id in source_requirement_ids:
        requirement = registry.store.get_node(project, requirement_id)
        if (
            requirement is None
            or requirement.node_type != NodeType.REQUIREMENT
            or requirement.attributes.get("review_status") != "reviewed"
            or not requirement.attributes.get("review_decision_id")
        ):
            raise GraphIntegrityError(
                "Submission requirements must be human-accepted same-project requirements"
            )

    normalized_protected: list[dict[str, str]] = []
    for pair in protected_sections:
        if not isinstance(pair, dict) or set(pair) != _PROTECTED_FIELDS:
            raise ValueError("Invalid protected section contract")
        template_evidence_id = _text(
            pair["template_evidence_id"], "template_evidence_id"
        )
        candidate_evidence_id = _text(
            pair["candidate_evidence_id"], "candidate_evidence_id"
        )
        label = _text(pair["label"], "label")
        for evidence_id, revision_id in (
            (template_evidence_id, template_revision_id),
            (candidate_evidence_id, candidate_revision_id),
        ):
            evidence = registry.store.get_node(project, evidence_id)
            if evidence is None or evidence.node_type != NodeType.EVIDENCE:
                raise GraphIntegrityError(
                    "Protected sections require same-project evidence"
                )
            if not _has_revision(registry, project, evidence_id, revision_id):
                raise GraphIntegrityError(
                    "Protected section evidence must belong to its declared revision"
                )
        normalized_protected.append(
            {
                "template_evidence_id": template_evidence_id,
                "candidate_evidence_id": candidate_evidence_id,
                "label": label,
            }
        )

    normalized_matrix: list[dict[str, Any]] = []
    matrix_ids: set[str] = set()
    matrix_requirement_ids: set[str] = set()
    for row in scope_matrix:
        if not isinstance(row, dict) or set(row) != _MATRIX_FIELDS:
            raise ValueError("Invalid scope matrix row contract")
        matrix_id = _text(row["matrix_id"], "matrix_id")
        requirement_id = _text(row["requirement_id"], "requirement_id")
        disposition = row["disposition"]
        if disposition not in _DISPOSITIONS:
            raise ValueError("Invalid proposal scope disposition")
        evidence_ids = _ids(
            row["proposal_evidence_ids"], "proposal_evidence_ids", maximum=50
        )
        if not evidence_ids:
            raise ValueError("Each matrix row requires candidate proposal evidence")
        qualification = row["qualification"]
        question = row["unresolved_question"]
        owner = row["owner"]
        for optional_value, field in (
            (qualification, "qualification"),
            (question, "unresolved_question"),
            (owner, "owner"),
        ):
            if optional_value is not None:
                if not isinstance(optional_value, str) or len(optional_value) > 2000:
                    raise ValueError(f"Invalid {field}")
                if isinstance(optional_value, str):
                    optional_value = optional_value.strip()
            if field == "qualification":
                qualification = optional_value
            elif field == "unresolved_question":
                question = optional_value
            else:
                owner = optional_value
        if disposition in {"excluded", "interface_only", "qualification"} and (
            not qualification or not owner
        ):
            raise ValueError(
                "Excluded/interface/qualification rows require a qualification and owner"
            )
        if disposition == "unresolved" and (not question or not owner):
            raise ValueError("Unresolved rows require a question and owner")
        if matrix_id in matrix_ids or requirement_id in matrix_requirement_ids:
            raise ValueError("Scope matrix IDs and requirement rows must be unique")
        if requirement_id not in accepted_issue_requirements:
            raise GraphIntegrityError(
                "Scope matrix requirement is not linked to the proposal issue"
            )
        matrix_ids.add(matrix_id)
        matrix_requirement_ids.add(requirement_id)
        for evidence_id in evidence_ids:
            evidence = registry.store.get_node(project, evidence_id)
            if evidence is None or evidence.node_type != NodeType.EVIDENCE:
                raise GraphIntegrityError(
                    "Matrix evidence must be same-project proposal evidence"
                )
            if not _has_revision(registry, project, evidence_id, candidate_revision_id):
                raise GraphIntegrityError(
                    "Matrix evidence must belong to the candidate proposal revision"
                )
        normalized_matrix.append(
            {
                "matrix_id": matrix_id,
                "requirement_id": requirement_id,
                "disposition": disposition,
                "proposal_evidence_ids": sorted(evidence_ids),
                "qualification": qualification,
                "unresolved_question": question,
                "owner": owner,
            }
        )
    if matrix_requirement_ids != accepted_issue_requirements:
        raise GraphIntegrityError(
            "Scope matrix must represent every linked accepted issue requirement exactly once"
        )

    source_evidence_set: set[str] = set(issue.evidence_ids)
    for requirement_id in source_requirement_ids:
        source_evidence_set.update(
            registry.get_record(project, requirement_id)["attributes"].get(
                "source_evidence_ids", []
            )
        )
    source_evidence_set.update(
        pair["template_evidence_id"] for pair in normalized_protected
    )
    source_evidence_set.update(
        pair["candidate_evidence_id"] for pair in normalized_protected
    )
    for row in normalized_matrix:
        source_evidence_set.update(row["proposal_evidence_ids"])
    if len(source_evidence_set) > _MAX_SOURCE_RECORDS:
        raise ValueError("Submission exceeds the 200 source evidence limit")
    for evidence_id in source_evidence_set:
        evidence = registry.store.get_node(project, evidence_id)
        if evidence is None or evidence.node_type != NodeType.EVIDENCE:
            raise GraphIntegrityError("Submission source must be same-project evidence")

    payload = {
        "submission_id": submission_id,
        "project_id": project,
        "title": title,
        "issue_id": issue_id,
        "candidate_revision_id": candidate_revision_id,
        "template_revision_id": template_revision_id,
        "source_requirement_ids": sorted(source_requirement_ids),
        "protected_sections": normalized_protected,
        "scope_matrix": normalized_matrix,
        "source_evidence_ids": sorted(source_evidence_set),
        "human_review_required": True,
    }
    if len(str(payload)) > _MAX_SNAPSHOT_CHARS:
        raise ValueError("Submission exceeds the bounded serialized size")
    payload["content_digest"] = digest(
        {k: v for k, v in payload.items() if k != "content_digest"}
    )
    return payload


def propose_submission(
    registry: RegistryService,
    project_id: str,
    submission_id: str,
    title: str,
    issue_id: str,
    candidate_revision_id: str,
    template_revision_id: str,
    source_requirement_ids: list[str],
    protected_sections: list[dict[str, Any]],
    scope_matrix: list[dict[str, Any]],
) -> dict[str, Any]:
    registry.check_access(project_id, write=True)
    with registry.store.transaction():
        payload = _submission_payload(
            registry,
            project_id,
            submission_id,
            title,
            issue_id,
            candidate_revision_id,
            template_revision_id,
            source_requirement_ids,
            protected_sections,
            scope_matrix,
        )
        existing = registry.store.get_node(project_id, submission_id)
        if existing is not None:
            if (
                existing.node_type != NodeType.SUBMISSION
                or existing.attributes != payload
                or existing.title != payload["title"]
            ):
                raise GraphIntegrityError(
                    "Submission identifier already has different immutable content"
                )
            return asdict(existing)
        edges: list[GraphEdge] = []
        links: list[tuple[RelationshipType, str, list[str]]] = [
            (RelationshipType.RELATES_TO, payload["issue_id"], []),
            (RelationshipType.DERIVED_FROM, payload["candidate_revision_id"], []),
            (RelationshipType.DERIVED_FROM, payload["template_revision_id"], []),
        ]
        links.extend(
            (RelationshipType.ADDRESSES, requirement_id, [])
            for requirement_id in payload["source_requirement_ids"]
        )
        links.extend(
            (RelationshipType.SUPPORTED_BY, evidence_id, [evidence_id])
            for evidence_id in payload["source_evidence_ids"]
        )
        for relationship, target_id, provenance in links:
            from .service import digest

            edge_id = "submission-edge:" + digest(
                [project_id, submission_id, relationship.value, target_id]
            )
            edges.append(
                GraphEdge(
                    edge_id,
                    project_id,
                    submission_id,
                    relationship,
                    target_id,
                    provenance,
                )
            )
        node = GraphNode(
            submission_id, project_id, NodeType.SUBMISSION, payload["title"], payload
        )
        registry.store.add_subgraph([node], edges)
        registry._event(
            project_id,
            submission_id,
            "proposal_submission_registered",
            issue_id=issue_id,
            content_digest=payload["content_digest"],
        )
        return asdict(node)


def submission_context(
    registry: RegistryService, project_id: str, submission_id: str
) -> dict[str, Any]:
    from .service import digest

    registry.check_access(project_id)
    submission = registry.get_record(project_id, submission_id)
    if submission["node_type"] != NodeType.SUBMISSION:
        raise GraphIntegrityError("Record is not a proposal submission")
    attributes = submission["attributes"]
    full_issue_context = registry.issue_context(
        project_id, attributes["issue_id"], include_results=False
    )
    # Task/result lifecycle events do not make source content stale. Bind only
    # the semantic issue and source graph state used to plan this submission.
    issue_context = {
        key: full_issue_context[key]
        for key in ("issue", "linked_records", "source_context_requirement_ids")
    }
    requirements = [
        registry.get_record(project_id, rid)
        for rid in attributes["source_requirement_ids"]
    ]
    evidence_ids = attributes["source_evidence_ids"]
    source_control = registry.source_control(project_id, evidence_ids)
    evidence_review = registry.evidence_review_context(project_id, evidence_ids)
    context_payload = {
        "submission": submission,
        "issue_context": issue_context,
        "requirements": requirements,
        "source_evidence_ids": evidence_ids,
        "source_control": source_control,
        "evidence_review": evidence_review,
    }
    if (
        len(evidence_ids) + len(requirements) > _MAX_SOURCE_RECORDS
        or len(str(context_payload)) > _MAX_SNAPSHOT_CHARS
    ):
        raise ValueError("Submission context exceeds bounded source limits")
    return {**context_payload, "submission_digest": digest(context_payload)}
