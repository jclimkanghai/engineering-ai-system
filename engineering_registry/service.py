"""Controlled local memory. Reviewer principals come from the trusted host."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import (
    DecisionLevel,
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    IssueStatus,
    NodeType,
    RelationshipType,
)
from .store import GraphIntegrityError, SQLiteGraphStore


def digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def finding_signature(finding: dict[str, Any]) -> str:
    """Conservative exact identity for a finding's title and wording."""
    identity = "\n".join(
        " ".join(
            re.sub(r"[^a-z0-9]+", " ", str(finding.get(field, "")).lower()).split()
        )
        for field in ("title", "finding")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


IMPACT_DOMAINS = frozenset(
    {
        "scope",
        "requirement",
        "assumption",
        "design_basis",
        "technical_solution",
        "interface",
        "risk",
        "cost_programme",
        "future_task",
        "client_objective",
        "accepted_rejected_alternative",
    }
)


@dataclass(frozen=True)
class Principal:
    actor_id: str
    project_ids: frozenset[str]
    role: str = "agent"
    organization_ids: frozenset[str] = frozenset()
    authentication_method: str = "host-configured"
    authority_basis: str = "explicit host Principal grants"

    def __post_init__(self) -> None:
        text(self.actor_id, "actor_id")
        if self.role not in {"reader", "agent", "reviewer", "delegate", "ai_reviewer"}:
            raise ValueError("Unknown principal role")
        if not self.project_ids or any(
            not isinstance(p, str) or not p.strip() for p in self.project_ids
        ):
            raise ValueError("At least one explicit project grant is required")
        object.__setattr__(self, "project_ids", frozenset(self.project_ids))
        if any(not isinstance(o, str) or not o.strip() for o in self.organization_ids):
            raise ValueError("Organization grants must be non-blank IDs")
        object.__setattr__(self, "organization_ids", frozenset(self.organization_ids))
        text(self.authentication_method, "authentication_method")
        text(self.authority_basis, "authority_basis")


class RegistryService:
    """Project-scoped records with human decisions bound to immutable snapshots."""

    def __init__(
        self,
        store: SQLiteGraphStore,
        principal: Principal,
        *,
        execution_validator: Callable[[dict], None] | None = None,
        organizational_registry: RegistryService | None = None,
    ) -> None:
        self.store = store
        self.principal = principal
        self.execution_validator = execution_validator
        self.organizational_registry = organizational_registry

    def check_access(
        self, project_id: str, *, write: bool = False, human: bool = False
    ) -> None:
        organization_id = (
            project_id.removeprefix("ORG:") if project_id.startswith("ORG:") else None
        )
        if (
            project_id not in self.principal.project_ids
            and organization_id not in self.principal.organization_ids
        ):
            raise PermissionError("Project access denied")
        if (
            organization_id is not None
            and write
            and (self.principal.role != "reviewer" or not human)
        ):
            raise PermissionError(
                "Organizational knowledge requires the human reviewer route"
            )
        if write and self.principal.role == "reader":
            raise PermissionError("Read-only principal")
        if human and self.principal.role != "reviewer":
            raise PermissionError("Human reviewer route required")

    def organizational_lesson_status(
        self, organization_id: str, lesson_id: str
    ) -> dict[str, Any] | None:
        """Read current organisation lifecycle from its separate store when configured."""
        scope = "ORG:" + text(organization_id, "organization_id")
        if self.organizational_registry is None:
            # Backward-compatible lookup for imported snapshots in a legacy shared DB.
            return self.store.get_record(
                scope, "organizational_lesson_status", text(lesson_id, "lesson_id")
            )
        authority = self.organizational_registry
        authority.check_access(scope)
        return authority.store.get_record(
            scope, "organizational_lesson_status", text(lesson_id, "lesson_id")
        )

    def _event(
        self, project_id: str, record_id: str, kind: str, **details: Any
    ) -> None:
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "record_id": record_id,
            "kind": kind,
            "actor_id": self.principal.actor_id,
            "actor_role": self.principal.role,
            "authentication_method": self.principal.authentication_method,
            "authority_basis": self.principal.authority_basis,
            "occurred_at": timestamp(),
            **details,
        }
        self.store.put_record(project_id, "events", event_id, event)

    def list_records(
        self, project_id: str, node_type: str | None = None
    ) -> list[dict[str, Any]]:
        self.check_access(project_id)
        return [asdict(n) for n in self.store.list_nodes(project_id, node_type)]

    def get_control_record(
        self, project_id: str, namespace: str, record_id: str
    ) -> dict[str, Any] | None:
        """Read one service-owned control record within the granted project."""
        self.check_access(project_id)
        return self.store.get_record(project_id, namespace, record_id)

    def list_control_records(
        self, project_id: str, namespace: str
    ) -> list[dict[str, Any]]:
        """Read control records within the granted project."""
        self.check_access(project_id)
        return self.store.list_records(project_id, namespace)

    def get_node(self, project_id: str, node_id: str) -> GraphNode | None:
        """Read a project graph node through the public Registry boundary."""
        self.check_access(project_id)
        return self.store.get_node(project_id, node_id)

    def reconcile_findings(
        self, project_id: str, findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Show conservative exact matches against canonical project findings."""
        self.check_access(project_id)
        existing_nodes = self.store.list_nodes(project_id, NodeType.FINDING)
        issues = self.store.list_nodes(project_id, NodeType.ENGINEERING_ISSUE)
        by_signature: dict[str, list[GraphNode]] = {}
        for node in existing_nodes:
            signature = node.attributes.get("finding_signature") or finding_signature(
                node.attributes
            )
            by_signature.setdefault(signature, []).append(node)
        matches = []
        for finding in findings:
            candidates = by_signature.get(finding_signature(finding), [])
            existing = []
            for node in candidates:
                lifecycle = "proposed"
                for issue in issues:
                    linked = any(
                        edge.relationship == RelationshipType.RELATES_TO
                        and edge.target_node_id == node.node_id
                        for edge in self.store.get_edges(project_id, issue.node_id)
                    )
                    if linked:
                        persisted_issue = self.store.get_issue(
                            project_id, issue.node_id
                        )
                        lifecycle = (
                            persisted_issue.status.value
                            if persisted_issue
                            else lifecycle
                        )
                        break
                existing.append(
                    {
                        "finding_id": node.attributes.get("legacy_id", node.node_id),
                        "title": node.title,
                        "lifecycle_status": lifecycle.upper(),
                    }
                )
            matches.append(
                {
                    "finding_id": finding.get("finding_id"),
                    "match_type": "possible_existing_match"
                    if existing
                    else "new_candidate",
                    "existing_finding_ids": [item["finding_id"] for item in existing],
                    "existing_findings": existing,
                    "automatic_action": "append_observation_only"
                    if existing
                    else "create_open_entry_after_approval",
                    "human_lifecycle_decision_required": bool(existing),
                }
            )
        return {
            "project_id": project_id,
            "status": "READY",
            "matches": matches,
            "automatic_closure": False,
        }

    def controlled_document(
        self, project_id: str, document_id: str
    ) -> dict[str, Any] | None:
        """Return Registry-owned document authority and revision provenance.

        Ingestion manifests may supply extraction metadata, but only these
        Registry graph records determine revision and governing status.
        """
        self.check_access(project_id)
        alias = self.store.get_record(project_id, "aliases", "document:" + document_id)
        node_id = (
            alias["canonical_id"]
            if alias
            else "document:" + quote(document_id, safe="")
        )
        document = self.store.get_node(project_id, node_id)
        if document is None or document.node_type != NodeType.DOCUMENT:
            return None
        revisions = []
        for edge in self.store.get_edges(project_id, node_id):
            if edge.relationship == RelationshipType.HAS_REVISION:
                revision = self.store.get_node(project_id, edge.target_node_id)
                if (
                    revision is not None
                    and revision.node_type == NodeType.DOCUMENT_REVISION
                ):
                    revisions.append(asdict(revision))
        supersedes = []
        superseded_by = []
        for candidate in self.store.list_nodes(project_id, NodeType.DOCUMENT):
            for edge in self.store.get_edges(project_id, candidate.node_id):
                if edge.relationship != RelationshipType.SUPERSEDES:
                    continue
                if edge.source_node_id == node_id:
                    supersedes.append(edge.target_node_id)
                if edge.target_node_id == node_id:
                    superseded_by.append(edge.source_node_id)
        revisions.sort(key=lambda item: item["node_id"])
        preferred_revision = document.attributes.get("revision")
        selected = next(
            (
                item
                for item in revisions
                if item["attributes"].get("revision") == preferred_revision
            ),
            revisions[-1] if revisions else None,
        )
        source_id = selected["node_id"] if selected else node_id
        authority_control = self.document_control(project_id, source_id)
        return {
            "document": asdict(document),
            "revisions": revisions,
            "selected_revision_id": source_id if selected else None,
            "authority_control": authority_control,
            "supersedes": sorted(supersedes),
            "superseded_by": sorted(superseded_by),
        }

    def records_page(
        self,
        project_id: str,
        node_type: str,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return one bounded typed page for desk and other overview readers."""
        self.check_access(project_id)
        if (
            not isinstance(node_type, str)
            or not node_type
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise ValueError("Invalid bounded record query")
        records, total = self.store.list_nodes_page(
            project_id, node_type, limit=limit, offset=offset
        )
        next_offset = offset + len(records)
        return {
            "records": [asdict(record) for record in records],
            "total": total,
            "next_offset": next_offset if next_offset < total else None,
        }

    def evidence_page(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        revision: str | None = None,
        authority: str | None = None,
        query_text: str = "",
        missing_text: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a bounded evidence projection for desk and MCP readers."""
        self.check_access(project_id)
        if (
            not isinstance(limit, int)
            or not 1 <= limit <= 100
            or not isinstance(offset, int)
            or offset < 0
            or not isinstance(query_text, str)
            or not isinstance(missing_text, bool)
            or authority not in {None, "governing", "current"}
            or any(
                value is not None and (not isinstance(value, str) or not value.strip())
                for value in (document_id, revision)
            )
        ):
            raise ValueError("Invalid bounded evidence query")
        records, total = self.store.list_evidence_page(
            project_id,
            document_id=document_id,
            revision=revision,
            authority=authority,
            query_text=query_text,
            missing_text=missing_text,
            limit=limit,
            offset=offset,
        )
        return {
            "records": [asdict(record) for record in records],
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def human_confirmed_evidence_page(
        self,
        project_id: str,
        *,
        authority: str | None = None,
        revision: str | None = None,
        query_text: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a bounded page with a current reviewer authority decision."""
        self.check_access(project_id)
        if (
            authority not in {None, "governing", "current"}
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or not isinstance(offset, int)
            or offset < 0
            or not isinstance(query_text, str)
            or revision is not None
            and (not isinstance(revision, str) or not revision.strip())
        ):
            raise ValueError("Invalid bounded human-confirmed evidence query")
        records, total = self.store.list_human_confirmed_evidence_page(
            project_id,
            authority=authority,
            revision=revision,
            query_text=query_text,
            limit=limit,
            offset=offset,
        )
        return {
            "records": [asdict(record) for record in records],
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def get_record(self, project_id: str, record_id: str) -> dict[str, Any]:
        self.check_access(project_id)
        node = self.store.get_node(project_id, record_id)
        if node is None:
            raise GraphIntegrityError("Record does not exist in this project")
        return asdict(node)

    def review_task_decision(
        self,
        project_id: str,
        decision_id: str,
        decision_digest: str,
        accept: bool,
        evidence_ids: list[str],
        rationale: str,
        *,
        formal_authority_reference: str | None = None,
    ) -> dict[str, Any]:
        """Add a human review decision to an immutable AI task-decision proposal."""
        self.check_access(project_id, write=True, human=True)
        if type(accept) is not bool:
            raise ValueError("accept must be boolean")
        rationale = text(rationale, "rationale")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("Task decision review requires evidence")
        with self.store.transaction():
            proposal = self.get_record(project_id, decision_id)
            if (
                proposal["node_type"] != NodeType.DECISION
                or proposal["attributes"].get("decision_type") != "task"
                or digest(proposal) != decision_digest
            ):
                raise ValueError("Stale task decision digest or wrong record type")
            issue_id = proposal["attributes"]["issue_id"]
            self.issue_context(project_id, issue_id)
            self._evidence(project_id, evidence_ids)
            source = proposal["attributes"]
            if accept and source["formal_authority_required"]:
                if not formal_authority_reference:
                    raise ValueError(
                        "D4 acceptance requires a formal authority reference"
                    )
                self.get_record(
                    project_id,
                    text(formal_authority_reference, "formal_authority_reference"),
                )
            current_reviews = [
                record
                for record in self.list_records(project_id, NodeType.DECISION)
                if record["attributes"].get("decision_type") == "human_task_review"
                and record["attributes"].get("source_decision_id") == decision_id
            ]
            if current_reviews:
                prior = current_reviews[0]
                attrs = prior["attributes"]
                if (
                    attrs["disposition"] == ("accept" if accept else "reject")
                    and attrs["reviewed_by"] == self.principal.actor_id
                    and attrs["evidence_ids"] == evidence_ids
                    and attrs["rationale"] == rationale
                    and attrs.get("formal_authority_reference")
                    == formal_authority_reference
                ):
                    return prior
                raise ValueError(
                    "Task decision was already reviewed; create a new proposal"
                )
            payload = {
                "decision_type": "human_task_review",
                "source_decision_id": decision_id,
                "issue_id": issue_id,
                "decision_level": source["decision_level"],
                **({"task_id": source["task_id"]} if source.get("task_id") else {}),
                "disposition": "accept" if accept else "reject",
                "authority": "human_engineer",
                "evidence_ids": evidence_ids,
                "rationale": rationale,
                "reviewed_by": self.principal.actor_id,
                "reviewed_at": timestamp(),
                "reasoning_evidence_status": "verified_human_reasoning",
                "reasoning_evidence_ids": evidence_ids,
                **(
                    {"formal_authority_reference": formal_authority_reference}
                    if formal_authority_reference
                    else {}
                ),
            }
            review_id = "decision:task-review:" + digest(
                {"decision_id": decision_id, "review": payload}
            )
            node = GraphNode(
                review_id,
                project_id,
                NodeType.DECISION,
                rationale,
                payload,
            )
            self.store.add_subgraph(
                [node],
                [
                    GraphEdge(
                        review_id + ":issue",
                        project_id,
                        issue_id,
                        RelationshipType.RELATES_TO,
                        review_id,
                    ),
                    GraphEdge(
                        review_id + ":proposal",
                        project_id,
                        decision_id,
                        RelationshipType.RESOLVED_BY,
                        review_id,
                    ),
                    *[
                        GraphEdge(
                            review_id + ":evidence:" + evidence_id,
                            project_id,
                            review_id,
                            RelationshipType.SUPPORTED_BY,
                            evidence_id,
                            [evidence_id],
                        )
                        for evidence_id in evidence_ids
                    ],
                    *(
                        [
                            GraphEdge(
                                review_id + ":formal-authority",
                                project_id,
                                review_id,
                                RelationshipType.RELATES_TO,
                                formal_authority_reference,
                            )
                        ]
                        if formal_authority_reference
                        else []
                    ),
                ],
            )
            self._event(
                project_id,
                issue_id,
                "task_decision_reviewed",
                source_decision_id=decision_id,
                review_id=review_id,
                disposition=payload["disposition"],
                decision_level=source["decision_level"],
            )
            return asdict(node)

    def register(
        self, node: GraphNode, *, links: list[GraphEdge] | None = None
    ) -> None:
        self.check_access(node.project_id, write=True)
        controlled = {
            NodeType.ENGINEERING_ISSUE,
            NodeType.SUBMISSION,
            NodeType.DECISION,
            NodeType.RESULT,
            NodeType.VERIFICATION,
            NodeType.LESSON,
            NodeType.TASK,
            NodeType.ASSESSMENT,
            NodeType.ALIGNMENT_REVIEW,
        }
        if node.node_type in controlled:
            raise ValueError(
                "Use a controlled proposal, execution or human review route"
            )
        with self.store.transaction():
            existed = self.store.get_node(node.project_id, node.node_id)
            self.store.add_subgraph([node], links or [])
            if existed is None:
                self._event(node.project_id, node.node_id, "registered")

    def propose_issue(
        self,
        project_id: str,
        issue_id: str,
        title: str,
        evidence_ids: list[str],
        requirement_ids: list[str] | None = None,
        finding_ids: list[str] | None = None,
        *,
        work_type: str | None = None,
    ) -> dict[str, Any]:
        self.check_access(project_id, write=True)
        if work_type not in {None, "proposal_readiness"}:
            raise ValueError("Unknown issue work type")
        issue = EngineeringIssue(
            issue_id,
            project_id,
            title,
            evidence_ids=evidence_ids,
            requirement_ids=requirement_ids or [],
            related_entity_ids=finding_ids or [],
            attributes={"work_type": work_type} if work_type else {},
        )
        for fid in finding_ids or []:
            node = self.store.get_node(project_id, fid)
            if node is None or node.node_type != NodeType.FINDING:
                raise GraphIntegrityError("Issue finding must exist in this project")
        with self.store.transaction():
            existed = self.store.get_issue(project_id, issue_id)
            self.store.save_issue(issue)
            if existed is None:
                self._event(project_id, issue_id, "issue_proposed")
        return self.issue_context(project_id, issue_id)

    def review_finding(
        self,
        project_id: str,
        issue_id: str,
        snapshot: str,
        statement: str,
        classification: str,
        severity: str,
        rationale: str,
    ) -> dict:
        from .finding_review import review_finding

        return review_finding(
            self,
            project_id,
            issue_id,
            snapshot,
            statement,
            classification,
            severity,
            rationale,
        )

    def history(self, project_id: str, record_id: str) -> list[dict[str, Any]]:
        self.check_access(project_id)
        events = [
            e
            for e in self.store.list_records(project_id, "events")
            if e["record_id"] == record_id
        ]
        return sorted(events, key=lambda e: (e["occurred_at"], e["event_id"]))

    def document_control(self, project_id: str, source_id: str) -> dict:
        from .document_control import document_control

        return document_control(self, project_id, source_id)

    def review_document_control(
        self,
        project_id: str,
        source_id: str,
        control_digest: str,
        authority: str,
        evidence_ids: list[str],
        rationale: str,
    ) -> dict:
        from .document_control import review_document_control

        return review_document_control(
            self,
            project_id,
            source_id,
            control_digest,
            authority,
            evidence_ids,
            rationale,
        )

    def source_control(self, project_id: str, evidence_ids: list[str]) -> list[dict]:
        from .document_control import source_control

        return source_control(self, project_id, evidence_ids)

    def evidence_review(self, project_id: str, evidence_id: str) -> dict:
        from .evidence_control import evidence_review

        return evidence_review(self, project_id, evidence_id)

    def evidence_review_context(
        self, project_id: str, evidence_ids: list[str]
    ) -> list[dict]:
        from .evidence_control import evidence_review_context

        return evidence_review_context(self, project_id, evidence_ids)

    def review_evidence(
        self,
        project_id: str,
        evidence_id: str,
        review_digest: str,
        status: str,
        rationale: str,
    ) -> dict:
        from .evidence_control import review_evidence

        return review_evidence(
            self, project_id, evidence_id, review_digest, status, rationale
        )

    def list_requirement_candidates(self, project_id: str) -> list[dict]:
        from .requirement_control import list_requirement_candidates

        return list_requirement_candidates(self, project_id)

    def propose_requirement_candidate(self, project_id: str, candidate: dict) -> dict:
        from .requirement_control import propose_requirement_candidate

        return propose_requirement_candidate(self, project_id, candidate)

    def review_requirement_candidate(
        self,
        project_id: str,
        candidate_id: str,
        candidate_digest: str,
        accept: bool,
        rationale: str,
    ) -> dict:
        from .requirement_control import review_requirement_candidate

        return review_requirement_candidate(
            self, project_id, candidate_id, candidate_digest, accept, rationale
        )

    def propose_submission(
        self,
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
        from .proposal_control import propose_submission

        return propose_submission(
            self,
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

    def submission_context(self, project_id: str, submission_id: str) -> dict[str, Any]:
        from .proposal_control import submission_context

        return submission_context(self, project_id, submission_id)

    def requirements_for_evidence(
        self, project_id: str, evidence_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Return stored requirements explicitly supported by these sources."""
        self.check_access(project_id)
        self._evidence(project_id, evidence_ids)
        source_ids = set(evidence_ids)
        requirements = self.store.list_requirements_for_evidence(
            project_id, sorted(source_ids)
        )
        return [asdict(requirement) for requirement in requirements]

    def issue_context(
        self, project_id: str, issue_id: str, *, include_results: bool = True
    ) -> dict[str, Any]:
        self.check_access(project_id)
        issue = self.store.get_issue(project_id, issue_id)
        if issue is None:
            raise GraphIntegrityError("Issue does not exist in this project")
        ids = {e.target_node_id for e in self.store.get_edges(project_id, issue_id)}
        if not include_results:
            # Reviewer citations provide project context, not new execution inputs.
            ids = {
                node_id
                for node_id in ids
                if (linked := self.store.get_node(project_id, node_id)) is not None
                and linked.node_type
                not in {
                    NodeType.ALIGNMENT_REVIEW,
                    NodeType.RESULT,
                    NodeType.TASK,
                    NodeType.VERIFICATION,
                    NodeType.LESSON,
                    NodeType.ASSESSMENT,
                }
                and (
                    linked.node_type != NodeType.DECISION
                    or linked.attributes.get("decision_type")
                    in {"task", "human_task_review"}
                    or linked.attributes.get("reasoning_evidence_status")
                    in {"verified_human_reasoning", "no_human_reasoning"}
                )
            }
            decisions, decision_count = self.store.list_decisions_for_issue(
                project_id, issue_id, limit=100
            )
            if decision_count > 100:
                raise ValueError("Issue decision history exceeds bounded context limit")
        pending = list(ids)
        source_links = {
            RelationshipType.DERIVED_FROM,
            RelationshipType.SUPPORTED_BY,
            RelationshipType.HAS_REVISION,
            RelationshipType.SUPERSEDES,
        }
        while pending:
            source = pending.pop()
            for edge in self.store.get_edges(project_id, source):
                if edge.relationship in source_links and edge.target_node_id not in ids:
                    ids.add(edge.target_node_id)
                    pending.append(edge.target_node_id)
                    if len(ids) > 1000:
                        raise ValueError(
                            "Issue source graph exceeds bounded context limit"
                        )
        # Submissions point to their issue, so include the incoming proposal link
        # and its immutable source records in the shared task context.
        submission_targets: set[str] = set()
        for submission in self.store.list_nodes(project_id, NodeType.SUBMISSION):
            if submission.attributes.get("issue_id") != issue_id:
                continue
            ids.add(submission.node_id)
            targets = {
                edge.target_node_id
                for edge in self.store.get_edges(project_id, submission.node_id)
            }
            ids.update(targets)
            submission_targets.update(targets)
            if len(ids) > 1000:
                raise ValueError("Issue source graph exceeds bounded context limit")
        pending.extend(submission_targets)
        while pending:
            source = pending.pop()
            for edge in self.store.get_edges(project_id, source):
                if edge.relationship in source_links and edge.target_node_id not in ids:
                    ids.add(edge.target_node_id)
                    pending.append(edge.target_node_id)
                    if len(ids) > 1000:
                        raise ValueError(
                            "Issue source graph exceeds bounded context limit"
                        )
        source_requirements = self.requirements_for_evidence(
            project_id, issue.evidence_ids
        )
        source_requirement_ids = sorted(
            set(issue.requirement_ids)
            | {requirement["node_id"] for requirement in source_requirements}
        )
        if not include_results:
            ids.update(node.node_id for node in decisions)
            prior_decisions, prior_count = self.store.list_decisions_for_records(
                project_id,
                sorted(set(issue.evidence_ids) | set(source_requirement_ids)),
                limit=100,
            )
            if prior_count > 100:
                raise ValueError(
                    "Relevant project decision history exceeds bounded context limit"
                )
            ids.update(node.node_id for node in prior_decisions)
        linked_records = []
        for record_id in sorted(ids | set(source_requirement_ids)):
            record = self.get_record(project_id, record_id)
            if record is not None:
                linked_records.append(record)
        payload: dict[str, Any] = {
            "issue": asdict(issue),
            "linked_records": linked_records,
            "source_context_requirement_ids": source_requirement_ids,
            "history": self.history(project_id, issue_id),
            "lifecycle_events": self.store.list_lifecycle_events(project_id, issue_id),
        }
        if include_results:
            payload["results"] = [
                n
                for n in self.list_records(project_id, "result")
                if n["attributes"].get("issue_id") == issue_id
            ]
        controls = self.source_control(project_id, issue.evidence_ids)
        if any(c["decision_id"] for c in controls):
            payload["source_control"] = controls
        return {
            **payload,
            "output_digest": digest(payload),
            "pending_proposals": [
                p
                for p in self.list_proposals(project_id)
                if p["issue_id"] == issue_id and p["status"] == "pending"
            ],
        }

    def task_context(
        self, project_id: str, issue_id: str, *, exclude_task_id: str | None = None
    ) -> dict[str, Any]:
        """Build the one bounded Registry context shared by brain and execution."""
        context = self.issue_context(project_id, issue_id, include_results=False)
        excluded = {
            "result",
            "task",
            "verification",
            "lesson",
            "assessment",
            "alignment_review",
        }
        records = sorted(
            (
                record
                for record in context["linked_records"]
                if record["node_type"] not in excluded
                and (
                    record["node_type"] != NodeType.DECISION
                    or (
                        (
                            record["attributes"].get("decision_type")
                            in {"task", "human_task_review"}
                            or record["attributes"].get("reasoning_evidence_status")
                            in {"verified_human_reasoning", "no_human_reasoning"}
                        )
                        and not (
                            exclude_task_id is not None
                            and record["attributes"].get("task_id") == exclude_task_id
                        )
                    )
                )
            ),
            key=lambda record: record["node_id"],
        )
        task_reviews = {
            record["attributes"]["source_decision_id"]: record
            for record in records
            if record["node_type"] == NodeType.DECISION
            and record["attributes"].get("decision_type") == "human_task_review"
        }
        records = [
            {
                **record,
                "attributes": {
                    **record["attributes"],
                    **(
                        {"human_review": task_reviews[record["node_id"]]}
                        if record["node_type"] == NodeType.DECISION
                        and record["attributes"].get("decision_type") == "task"
                        and record["node_id"] in task_reviews
                        else {}
                    ),
                },
            }
            for record in records
        ]
        current_issue_id = context["issue"]["issue_id"]
        current_anchors = set(context["issue"]["evidence_ids"]) | set(
            context["source_context_requirement_ids"]
        )
        for record in records:
            attrs = record["attributes"]
            if (
                record["node_type"] == NodeType.DECISION
                and attrs.get("issue_id") != current_issue_id
            ):
                cited = set(attrs.get("evidence_ids", []))
                cited.update(attrs.get("reasoning_evidence_ids", []))
                cited.update(attrs.get("requirement_ids", []))
                cited.update(attrs.get("related_record_ids", []))
                if isinstance(attrs.get("proposal"), dict):
                    cited.update(attrs["proposal"].get("evidence_ids", []))
                shared = sorted(cited & current_anchors)
                if shared:
                    record["context_match"] = {"shared_source_ids": shared}
        evidence_ids = sorted(
            record["node_id"]
            for record in records
            if record["node_type"] == NodeType.EVIDENCE
        )
        controls = self.source_control(project_id, evidence_ids)
        reviews = self.evidence_review_context(project_id, evidence_ids)
        lesson_ids = []
        for lesson in self.store.list_approved_lessons(project_id, limit=100):
            attrs = lesson.attributes
            if attrs.get("scope") == "imported_organizational":
                organization_scope = "ORG:" + str(
                    attrs.get("source_organization_id", "")
                )
                pointer = self.organizational_lesson_status(
                    organization_scope.removeprefix("ORG:"),
                    str(attrs.get("source_organizational_lesson_id", "")),
                )
                try:
                    due = date.fromisoformat(attrs["review_due"])
                except (KeyError, ValueError):
                    continue
                if (
                    not pointer
                    or pointer.get("status") != "active"
                    or due <= datetime.now(UTC).date()
                ):
                    continue
            lesson_ids.append(lesson.node_id)
        snapshot = {
            "issue": context["issue"],
            "records": records,
            "source_context_requirement_ids": context["source_context_requirement_ids"],
        }
        submission_digests = {
            record["node_id"]: self.submission_context(project_id, record["node_id"])[
                "submission_digest"
            ]
            for record in records
            if record["node_type"] == NodeType.SUBMISSION
        }
        if submission_digests:
            snapshot["submission_context_digests"] = submission_digests
        if any(control["decision_id"] for control in controls):
            snapshot["source_control"] = controls
        if any(review["human_reviewed"] for review in reviews):
            snapshot["evidence_review"] = reviews
        payload = {
            "schema_version": 1,
            "issue": context["issue"],
            "records": records,
            "evidence_ids": evidence_ids,
            "requirement_ids": context["source_context_requirement_ids"],
            "source_control": controls,
            "evidence_review": reviews,
            "lesson_ids": lesson_ids,
            "execution_snapshot": snapshot,
        }
        return {**payload, "context_digest": digest(payload)}

    def record_task_decision(
        self,
        project_id: str,
        issue_id: str,
        decision_id: str,
        statement: str,
        rationale: str,
        evidence_ids: list[str],
        *,
        alternatives: list[str] | None = None,
        assumptions: list[str] | None = None,
        requirement_ids: list[str] | None = None,
        related_record_ids: list[str] | None = None,
        task_id: str | None = None,
        impact: str | None = None,
        authority: str = "ai_lead",
        decision_level: str = "D2",
        human_acceptance_required: bool | None = None,
        impact_flags: dict[str, str] | None = None,
        source_log_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a Document AI task decision as a proposed, non-authoritative record."""
        self.check_access(project_id, write=True)
        if self.principal.role != "agent":
            raise PermissionError("AI lead route required to record a task decision")
        if authority != "ai_lead":
            raise ValueError("Task decision authority must remain the AI lead")
        statement = text(statement, "statement")
        rationale = text(rationale, "rationale")
        level = DecisionLevel(decision_level)
        controls = {
            DecisionLevel.D0: (False, False, False),
            DecisionLevel.D1: (False, False, False),
            DecisionLevel.D2: (True, False, False),
            DecisionLevel.D3: (True, True, False),
            DecisionLevel.D4: (True, True, True),
        }[level]
        human_review_required, derived_acceptance, formal_authority_required = controls
        if (
            human_acceptance_required is not None
            and human_acceptance_required != derived_acceptance
        ):
            raise ValueError(
                "Human-control requirements are derived from decision level"
            )
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("decision_id must not be blank")
        if any(
            values is not None and not isinstance(values, list)
            for values in (
                evidence_ids,
                alternatives,
                assumptions,
                requirement_ids,
                related_record_ids,
            )
        ):
            raise ValueError("Task decision references and lists must be lists")
        evidence_ids = list(evidence_ids)
        alternatives = list(alternatives or [])
        assumptions = list(assumptions or [])
        requirement_ids = list(requirement_ids or [])
        related_record_ids = list(related_record_ids or [])
        for field_name, values in (
            ("evidence_ids", evidence_ids),
            ("alternatives", alternatives),
            ("assumptions", assumptions),
            ("requirement_ids", requirement_ids),
            ("related_record_ids", related_record_ids),
        ):
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field_name} must contain non-blank text")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} contains duplicates")
        if not evidence_ids and not related_record_ids:
            raise ValueError(
                "Task decision requires linked evidence or Registry records"
            )
        impact = text(impact, "impact") if impact is not None else None
        if impact_flags is not None and (
            not isinstance(impact_flags, dict)
            or set(impact_flags) != IMPACT_DOMAINS
            or any(
                value not in {"yes", "no", "unknown"} for value in impact_flags.values()
            )
        ):
            raise ValueError("Invalid impact classification")
        if source_log_id is not None:
            text(source_log_id, "source_log_id")
            source_log = self.store.get_record(
                project_id, "execution_logs", source_log_id
            )
            if not source_log or source_log["issue_id"] != issue_id:
                raise ValueError("Unknown or cross-issue source execution log")

        with self.store.transaction():
            issue_context = self.issue_context(project_id, issue_id)
            self._evidence(project_id, evidence_ids)
            if not set(requirement_ids).issubset(
                issue_context["source_context_requirement_ids"]
            ):
                raise GraphIntegrityError(
                    "Task decision requirement must be linked to its issue sources"
                )
            for record_id in related_record_ids:
                self.get_record(project_id, record_id)
            if task_id is not None:
                task = self.store.get_record(project_id, "tasks", task_id)
                if task is None or task.get("issue_id") != issue_id:
                    raise GraphIntegrityError(
                        "Task decision must reference a task for this issue"
                    )
            payload = {
                "decision_type": "task",
                "authority": "ai_lead",
                "status": "proposed",
                "statement": statement,
                "rationale": rationale,
                "issue_id": issue_id,
                "evidence_ids": evidence_ids,
                "requirement_ids": requirement_ids,
                "alternatives": [text(value, "alternative") for value in alternatives],
                "assumptions": [text(value, "assumption") for value in assumptions],
                "related_record_ids": related_record_ids,
                "decision_level": level.value,
                "human_review_required": human_review_required,
                "human_acceptance_required": derived_acceptance,
                "formal_authority_required": formal_authority_required,
                "proposed_by": self.principal.actor_id,
                "proposed_at": timestamp(),
                **({"task_id": task_id} if task_id else {}),
                **({"impact": impact} if impact else {}),
                **({"impact_flags": impact_flags} if impact_flags is not None else {}),
                **({"source_log_id": source_log_id} if source_log_id else {}),
            }
            node = GraphNode(
                decision_id, project_id, NodeType.DECISION, statement, payload
            )
            self.store.add_subgraph(
                [node],
                [
                    GraphEdge(
                        f"{decision_id}:issue",
                        project_id,
                        issue_id,
                        RelationshipType.RELATES_TO,
                        decision_id,
                    ),
                    *[
                        GraphEdge(
                            f"{decision_id}:evidence:{evidence_id}",
                            project_id,
                            decision_id,
                            RelationshipType.SUPPORTED_BY,
                            evidence_id,
                            [evidence_id],
                        )
                        for evidence_id in evidence_ids
                    ],
                    *[
                        GraphEdge(
                            f"{decision_id}:related:{record_id}",
                            project_id,
                            decision_id,
                            RelationshipType.RELATES_TO,
                            record_id,
                        )
                        for record_id in related_record_ids
                    ],
                    *[
                        GraphEdge(
                            f"{decision_id}:requirement:{requirement_id}",
                            project_id,
                            decision_id,
                            RelationshipType.ADDRESSES,
                            requirement_id,
                        )
                        for requirement_id in requirement_ids
                    ],
                    *(
                        [
                            GraphEdge(
                                f"{decision_id}:task",
                                project_id,
                                decision_id,
                                RelationshipType.RELATES_TO,
                                "task:" + task_id,
                            )
                        ]
                        if task_id
                        else []
                    ),
                ],
            )
            self._event(
                project_id,
                issue_id,
                "task_decision_proposed",
                decision_id=decision_id,
                authority="ai_lead",
                decision_level=level.value,
            )
        return asdict(node)

    def record_lead_step(
        self,
        project_id: str,
        issue_id: str,
        note_id: str,
        statement: str,
        rationale: str,
        impact_flags: dict[str, str],
        *,
        task_id: str | None = None,
        evidence_ids: list[str] | None = None,
        source_log_id: str | None = None,
    ) -> dict[str, Any]:
        """Retain a Lead choice as a decision or bounded execution note."""
        self.check_access(project_id, write=True)
        if self.principal.role != "agent":
            raise PermissionError("AI lead route required")
        note_id = text(note_id, "note_id")
        statement = text(statement, "statement")
        rationale = text(rationale, "rationale")
        if len(note_id) > 128 or len(statement) > 2000 or len(rationale) > 4000:
            raise ValueError("Lead step exceeds length limit")
        if (
            not isinstance(impact_flags, dict)
            or set(impact_flags) != IMPACT_DOMAINS
            or any(
                value not in {"yes", "no", "unknown"} for value in impact_flags.values()
            )
        ):
            raise ValueError("Invalid impact classification")
        if not isinstance(evidence_ids, list) and evidence_ids is not None:
            raise ValueError("evidence_ids must be a list")
        evidence_ids = list(evidence_ids or [])
        with self.store.transaction():
            self.issue_context(project_id, issue_id)
            self._evidence(project_id, evidence_ids)
            if task_id is not None:
                task = self.store.get_record(project_id, "tasks", task_id)
                if not task or task.get("issue_id") != issue_id:
                    raise ValueError("Lead step task must belong to its issue")
            if any(value != "no" for value in impact_flags.values()):
                decision_id = "decision:lead:" + note_id
                decision = self.record_task_decision(
                    project_id,
                    issue_id,
                    decision_id,
                    statement,
                    rationale,
                    evidence_ids,
                    related_record_ids=[] if evidence_ids else [issue_id],
                    task_id=task_id,
                    impact_flags=impact_flags,
                    source_log_id=source_log_id,
                )
                return {
                    "route": "registry_decision",
                    "decision_id": decision_id,
                    "decision": decision,
                }
            if source_log_id is not None:
                raise ValueError("Log promotion requires a material impact")
            if self.store.get_record(project_id, "execution_logs", note_id):
                raise ValueError("Lead execution note already exists")
            log = {
                "route": "execution_log",
                "note_id": note_id,
                "issue_id": issue_id,
                "task_id": task_id,
                "statement": statement,
                "rationale": rationale,
                "impact_flags": deepcopy(impact_flags),
                "evidence_ids": evidence_ids,
                "actor": self.principal.actor_id,
                "recorded_at": timestamp(),
            }
            self.store.put_record(project_id, "execution_logs", note_id, log)
            return log

    def record_client_project_brief(
        self, project: str, brief_id: str, definition: dict
    ) -> dict:
        from .client_brief import record_client_project_brief

        return record_client_project_brief(self, project, brief_id, definition)

    def client_project_brief(self, project: str) -> dict | None:
        from .client_brief import client_project_brief

        return client_project_brief(self, project)

    def client_project_brief_view(self, project: str) -> dict:
        """Read-only current/stale view; strict execution routes use client_project_brief."""
        self.check_access(project)
        pointer = self.store.get_record(
            project, "active_client_project_brief", "active"
        )
        if not pointer:
            return {"status": "missing", "brief": None}
        brief = self.store.get_record(
            project, "client_project_briefs", pointer["brief_id"]
        )
        try:
            self.client_project_brief(project)
        except ValueError as exc:
            return {"status": "stale", "brief": brief, "detail": str(exc)}
        return {"status": "current", "brief": brief}

    def record_client_mandate(
        self, project: str, mandate_id: str, issue_id: str, definition: dict
    ) -> dict:
        from .delegation import record_client_mandate

        return record_client_mandate(self, project, mandate_id, issue_id, definition)

    def client_mandate(
        self, project: str, issue_id: str, *, exclude_task_id: str | None = None
    ) -> dict | None:
        from .delegation import client_mandate

        return client_mandate(self, project, issue_id, exclude_task_id=exclude_task_id)

    def approve_delegation_policy(
        self, project: str, policy_id: str, definition: dict
    ) -> dict:
        from .delegation import approve_delegation_policy

        return approve_delegation_policy(self, project, policy_id, definition)

    def delegation_policy(self, project: str, policy_id: str) -> dict:
        from .delegation import delegation_policy

        return delegation_policy(self, project, policy_id)

    def revoke_delegation_policy(
        self, project: str, policy_id: str, rationale: str
    ) -> None:
        from .delegation import revoke_delegation_policy

        revoke_delegation_policy(self, project, policy_id, rationale)

    def approve_execution_policy(
        self, project: str, policy_id: str, definition: dict
    ) -> dict:
        from .execution_policy import approve_execution_policy

        return approve_execution_policy(self, project, policy_id, definition)

    def revoke_execution_policy(
        self, project: str, policy_id: str, rationale: str
    ) -> dict:
        from .execution_policy import revoke_execution_policy

        return revoke_execution_policy(self, project, policy_id, rationale)

    def decide_delegated_result(self, project: str, decision: dict) -> dict:
        from .delegation import decide_delegated_result

        return decide_delegated_result(self, project, decision)

    def record_alignment_review(
        self, project: str, task_id: str, result_id: str | None, review: dict
    ) -> dict:
        from .alignment import record_alignment_review

        return record_alignment_review(self, project, task_id, result_id, review)

    def propose_transition(
        self,
        project_id: str,
        issue_id: str,
        to_status: str,
        rationale: str,
        evidence_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self.check_access(project_id, write=True)
        with self.store.transaction():
            context = self.issue_context(project_id, issue_id)
            target = IssueStatus(to_status)
            ids = list(evidence_ids or [])
            if target == IssueStatus.CLOSED and not ids:
                raise ValueError("Closure requires closure evidence")
            self._evidence(project_id, ids)
            proposal = {
                "proposal_id": str(uuid.uuid4()),
                "project_id": project_id,
                "issue_id": issue_id,
                "to_status": target.value,
                "rationale": text(rationale, "rationale"),
                "evidence_ids": ids,
                "base_digest": context["output_digest"],
                "status": "pending",
                "proposed_by": self.principal.actor_id,
                "proposed_at": timestamp(),
            }
            proposal["proposal_digest"] = digest(proposal)
            self.store.put_record(
                project_id, "proposals", proposal["proposal_id"], proposal
            )
        return proposal

    def _evidence(self, project_id: str, evidence_ids: list[str]) -> None:
        for eid in evidence_ids:
            node = self.store.get_node(project_id, eid)
            if node is None or node.node_type != NodeType.EVIDENCE:
                raise GraphIntegrityError("Evidence must exist in the same project")

    def list_proposals(self, project_id: str) -> list[dict[str, Any]]:
        self.check_access(project_id)
        return self.store.list_records(project_id, "proposals")

    def review_proposal(
        self,
        project_id: str,
        proposal_id: str,
        proposal_digest: str,
        *,
        accept: bool,
        rationale: str,
    ) -> dict[str, Any]:
        self.check_access(project_id, write=True, human=True)
        if not isinstance(rationale, str):
            raise ValueError("review rationale must be text")
        rationale = rationale.strip()
        if type(accept) is not bool:
            raise ValueError("accept must be boolean")
        with self.store.transaction():
            proposal = self.store.get_record(project_id, "proposals", proposal_id)
            if proposal is None:
                raise GraphIntegrityError("Proposal does not exist in this project")
            if proposal_digest != proposal["proposal_digest"]:
                raise ValueError("Stale proposal digest")
            if proposal["status"] != "pending":
                if (
                    proposal.get("accepted") == accept
                    and proposal.get("reviewed_by") == self.principal.actor_id
                    and proposal.get("review_rationale") == rationale
                ):
                    return proposal
                raise ValueError("Proposal already reviewed")
            unreasoned_closure = (
                accept
                and proposal["to_status"] == IssueStatus.CLOSED.value
                and not rationale
            )
            if not rationale and not unreasoned_closure:
                raise ValueError("review rationale must not be blank")
            current = self.issue_context(project_id, proposal["issue_id"])
            if accept and proposal["base_digest"] != current["output_digest"]:
                raise ValueError(
                    "Issue changed since proposal; create a fresh proposal"
                )
            if accept and proposal["to_status"] == IssueStatus.CLOSED:
                from .outcomes import needs_assessment

                verified = {
                    n["attributes"]["result_id"]
                    for n in self.list_records(project_id, "verification")
                }
                if any(n["node_id"] not in verified for n in current["results"]):
                    raise ValueError(
                        "Closure requires recorded human verification of execution results"
                    )
                decisions = self.list_records(project_id, "decision")
                for result in current["results"]:
                    outcomes = sorted(
                        (
                            d
                            for d in decisions
                            if d["attributes"].get("result_id") == result["node_id"]
                        ),
                        key=lambda d: d["attributes"]["reviewed_at"],
                    )
                    if (
                        needs_assessment(self, project_id, result) and not outcomes
                    ) or (
                        outcomes
                        and outcomes[-1]["attributes"]["disposition"] != "accept"
                    ):
                        raise ValueError(
                            "Closure requires final human acceptance of the assessed output"
                        )
            if accept:
                self.store.transition_issue(
                    project_id,
                    proposal["issue_id"],
                    IssueStatus(proposal["to_status"]),
                    actor_id=self.principal.actor_id,
                    rationale=(rationale or "Closure recorded without a human reason."),
                    evidence_ids=proposal["evidence_ids"],
                    human_approved=True,
                )
            decision_id = f"decision:{proposal_id}"
            reviewed_at = timestamp()
            reasoning_evidence_ids = proposal["evidence_ids"] if rationale else []
            decision = GraphNode(
                decision_id,
                project_id,
                NodeType.DECISION,
                rationale or "Closure recorded without human reasoning.",
                {
                    "proposal": proposal,
                    "accepted": accept,
                    "reviewed_by": self.principal.actor_id,
                    "reviewed_at": reviewed_at,
                    "reviewed_output_digest": current["output_digest"],
                    "reasoning_evidence_status": (
                        "verified_human_reasoning"
                        if rationale
                        else "no_human_reasoning"
                    ),
                    "reasoning_evidence_ids": reasoning_evidence_ids,
                },
            )
            self.store.add_subgraph(
                [decision],
                [
                    GraphEdge(
                        decision_id + ":issue",
                        project_id,
                        proposal["issue_id"],
                        RelationshipType.DECIDED_BY,
                        decision_id,
                    ),
                    *[
                        GraphEdge(
                            decision_id + ":evidence:" + evidence_id,
                            project_id,
                            decision_id,
                            RelationshipType.SUPPORTED_BY,
                            evidence_id,
                            [evidence_id],
                        )
                        for evidence_id in reasoning_evidence_ids
                    ],
                ],
            )
            proposal.update(
                status="accepted" if accept else "rejected",
                accepted=accept,
                reviewed_by=self.principal.actor_id,
                reviewed_at=reviewed_at,
                review_rationale=rationale,
                reasoning_evidence_status=decision.attributes[
                    "reasoning_evidence_status"
                ],
            )
            self.store.put_record(
                project_id, "proposals", proposal_id, proposal, replace=True
            )
            self._event(
                project_id,
                proposal["issue_id"],
                "proposal_reviewed",
                proposal_id=proposal_id,
                accepted=accept,
                rationale=rationale or None,
                reasoning_evidence_status=decision.attributes[
                    "reasoning_evidence_status"
                ],
                output_digest=current["output_digest"],
            )
        return proposal

    def record_result(
        self,
        project_id: str,
        result_id: str,
        issue_id: str,
        task: str,
        inputs: dict[str, Any],
        method: str,
        tool_version: str,
        outputs: dict[str, Any],
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        self.check_access(project_id, write=True)
        with self.store.transaction():
            self.issue_context(project_id, issue_id)
            self._evidence(project_id, evidence_ids)
            payload = {
                "issue_id": issue_id,
                "task": text(task, "task"),
                "inputs": inputs,
                "method": text(method, "method"),
                "tool_version": text(tool_version, "tool_version"),
                "outputs": outputs,
                "evidence_ids": evidence_ids,
                "verification_state": "unverified",
            }
            node = GraphNode(result_id, project_id, NodeType.RESULT, task, payload)
            existed = self.store.get_node(project_id, result_id)
            self.store.add_subgraph(
                [node],
                [
                    GraphEdge(
                        f"{result_id}:evidence:{e}",
                        project_id,
                        result_id,
                        RelationshipType.SUPPORTED_BY,
                        e,
                        [e],
                    )
                    for e in evidence_ids
                ]
                + [
                    GraphEdge(
                        f"{result_id}:issue",
                        project_id,
                        result_id,
                        RelationshipType.RELATES_TO,
                        issue_id,
                    )
                ],
            )
            if existed is None:
                self._event(
                    project_id, issue_id, "result_recorded", result_id=result_id
                )
        return asdict(node)

    def verify_result(
        self,
        project_id: str,
        result_id: str,
        output_digest: str,
        evidence_ids: list[str],
        rationale: str,
        *,
        assessment_id: str | None = None,
        assessment_digest: str | None = None,
    ) -> dict[str, Any]:
        self.check_access(project_id, write=True, human=True)
        with self.store.transaction():
            result = self.get_record(project_id, result_id)
            if (
                result["node_type"] != NodeType.RESULT
                or digest(result) != output_digest
            ):
                raise ValueError("Stale result digest or wrong record type")
            from .outcomes import needs_assessment, validated_assessment

            assessment = None
            if needs_assessment(self, project_id, result) or assessment_id:
                assessment = validated_assessment(
                    self, project_id, result, assessment_id, assessment_digest
                )
                from .outcomes import require_outcome_review

                require_outcome_review(self, project_id, result, require_aligned=True)
                if not all(
                    check["passed"] for check in assessment["attributes"]["checks"]
                ):
                    raise ValueError(
                        "Failed assessment checks require rework before verification"
                    )
            if not evidence_ids:
                raise ValueError("Verification requires evidence")
            self._evidence(project_id, evidence_ids)
            vid = "verification:" + digest(
                {"result": result_id, "digest": output_digest}
            )
            rationale = text(rationale, "rationale")
            previous = self.store.get_node(project_id, vid)
            if previous:
                if (
                    previous.title == rationale
                    and previous.attributes["reviewed_by"] == self.principal.actor_id
                    and previous.attributes["evidence_ids"] == evidence_ids
                    and previous.attributes.get("assessment_id") == assessment_id
                ):
                    return asdict(previous)
                raise ValueError("Result already verified with a different decision")
            record = GraphNode(
                vid,
                project_id,
                NodeType.VERIFICATION,
                rationale,
                {
                    "result_id": result_id,
                    "result_digest": output_digest,
                    "evidence_ids": evidence_ids,
                    "reviewed_by": self.principal.actor_id,
                    "reviewed_at": timestamp(),
                    **(
                        {
                            "assessment_id": assessment_id,
                            "assessment_digest": assessment_digest,
                        }
                        if assessment
                        else {}
                    ),
                },
            )
            self.store.add_subgraph(
                [record],
                [
                    GraphEdge(
                        vid + ":result",
                        project_id,
                        vid,
                        RelationshipType.RELATES_TO,
                        result_id,
                    )
                ]
                + [
                    GraphEdge(
                        vid + ":evidence:" + eid,
                        project_id,
                        vid,
                        RelationshipType.SUPPORTED_BY,
                        eid,
                        [eid],
                    )
                    for eid in evidence_ids
                ],
            )
            self._event(
                project_id,
                result["attributes"]["issue_id"],
                "result_verified",
                verification_id=vid,
            )
        return asdict(record)

    def record_assessment(
        self,
        project_id: str,
        result_id: str,
        result_digest: str,
        assessment: dict[str, Any],
    ) -> dict[str, Any]:
        from .outcomes import record_assessment

        return record_assessment(self, project_id, result_id, result_digest, assessment)

    def decide_result(
        self,
        project_id: str,
        result_id: str,
        result_digest: str,
        assessment_id: str,
        assessment_digest: str,
        disposition: str,
        evidence_ids: list[str],
        rationale: str,
    ) -> dict[str, Any]:
        from .outcomes import decide_result

        return decide_result(
            self,
            project_id,
            result_id,
            result_digest,
            assessment_id,
            assessment_digest,
            disposition,
            evidence_ids,
            rationale,
        )

    def promote_lesson(
        self,
        project_id: str,
        lesson_id: str,
        title: str,
        issue_ids: list[str],
        evidence_ids: list[str],
        rationale: str,
    ) -> dict[str, Any]:
        self.check_access(project_id, write=True, human=True)
        if not issue_ids or not evidence_ids:
            raise ValueError("Lesson requires issues and evidence")
        with self.store.transaction():
            contexts = [self.issue_context(project_id, iid) for iid in issue_ids]
            self._evidence(project_id, evidence_ids)
            result_ids = sorted(
                {
                    result["node_id"]
                    for context in contexts
                    for result in context["results"]
                }
            )
            all_decisions = sorted(
                {
                    record["node_id"]: record
                    for context in contexts
                    for record in context["linked_records"]
                    if record["node_type"] == NodeType.DECISION
                }.values(),
                key=lambda record: record["node_id"],
            )
            decision_ids = [
                record["node_id"]
                for record in all_decisions
                if record["attributes"].get("reasoning_evidence_status")
                == "verified_human_reasoning"
            ]
            excluded_decision_ids = [
                record["node_id"]
                for record in all_decisions
                if record["node_id"] not in decision_ids
            ]
            verifications = sorted(
                (
                    record
                    for record in self.list_records(project_id, "verification")
                    if record["attributes"].get("result_id") in result_ids
                ),
                key=lambda record: record["node_id"],
            )
            verification_ids = [record["node_id"] for record in verifications]
            assessments = sorted(
                (
                    record
                    for record in self.list_records(project_id, "assessment")
                    if record["attributes"].get("result_id") in result_ids
                ),
                key=lambda record: record["node_id"],
            )
            assessment_ids = [record["node_id"] for record in assessments]
            learning_contexts = deepcopy(contexts)
            for context in learning_contexts:
                context["linked_records"] = [
                    record
                    for record in context["linked_records"]
                    if record["node_type"] != NodeType.DECISION
                    or record["node_id"] in decision_ids
                ]
            source_snapshot = {
                "issues": learning_contexts,
                "verifications": verifications,
                "assessments": assessments,
            }
            node = GraphNode(
                lesson_id,
                project_id,
                NodeType.LESSON,
                text(title, "title"),
                {
                    "issue_ids": issue_ids,
                    "evidence_ids": evidence_ids,
                    "scope": "project",
                    "approved_by": self.principal.actor_id,
                    "approved_at": timestamp(),
                    "rationale": text(rationale, "rationale"),
                    "source_result_ids": result_ids,
                    "source_decision_ids": decision_ids,
                    "excluded_decision_ids": excluded_decision_ids,
                    "source_verification_ids": verification_ids,
                    "source_assessment_ids": assessment_ids,
                    "source_snapshot": source_snapshot,
                    "snapshot_digest": digest(source_snapshot),
                },
            )
            self.store.add_subgraph(
                [node],
                [
                    GraphEdge(
                        lesson_id + ":issue:" + iid,
                        project_id,
                        lesson_id,
                        RelationshipType.LEARNED_FROM,
                        iid,
                    )
                    for iid in issue_ids
                ]
                + [
                    GraphEdge(
                        lesson_id + ":record:" + record_id,
                        project_id,
                        lesson_id,
                        RelationshipType.LEARNED_FROM,
                        record_id,
                    )
                    for record_id in result_ids
                    + decision_ids
                    + verification_ids
                    + assessment_ids
                ]
                + [
                    GraphEdge(
                        lesson_id + ":evidence:" + eid,
                        project_id,
                        lesson_id,
                        RelationshipType.SUPPORTED_BY,
                        eid,
                        [eid],
                    )
                    for eid in evidence_ids
                ],
            )
            self._event(project_id, lesson_id, "lesson_promoted")
        return asdict(node)

    def promote_organizational_lesson(
        self,
        organization_id: str,
        lesson_id: str,
        title: str,
        source_lessons: list[tuple[str, str]],
        *,
        observation: str,
        result: str,
        interpretation: str,
        validated_statement: str,
        applicability: str,
        limitations: str,
        relevant_standards: list[str],
        review_due: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Promote human-validated reusable knowledge into an explicit org scope."""
        self.check_access("ORG:" + organization_id, write=True, human=True)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", organization_id):
            raise ValueError("Invalid organization ID")
        if (
            not isinstance(source_lessons, list)
            or not source_lessons
            or len(source_lessons) > 20
        ):
            raise ValueError("Organizational knowledge requires 1 to 20 source lessons")
        try:
            due = date.fromisoformat(text(review_due, "review_due"))
        except ValueError as exc:
            raise ValueError("review_due must be an ISO calendar date") from exc
        if due <= datetime.now(UTC).date():
            raise ValueError(
                "Organizational knowledge review_due must be in the future"
            )
        standards = [text(item, "relevant_standard") for item in relevant_standards]
        if len(standards) != len(set(standards)):
            raise ValueError("relevant_standards contains duplicates")
        with self.store.transaction():
            snapshots = []
            seen: set[tuple[str, str]] = set()
            for project_id, source_id in source_lessons:
                project_id = text(project_id, "source_project_id")
                source_id = text(source_id, "source_lesson_id")
                if (project_id, source_id) in seen:
                    raise ValueError("source_lessons contains duplicates")
                seen.add((project_id, source_id))
                source = self.get_record(project_id, source_id)
                if (
                    source["node_type"] != NodeType.LESSON
                    or source["attributes"].get("scope") != "project"
                    or not source["attributes"].get("approved_by")
                ):
                    raise ValueError(
                        "Organizational knowledge must derive from approved project lessons"
                    )
                snapshots.append(
                    {
                        "project_id": project_id,
                        "lesson_id": source_id,
                        "lesson_digest": digest(source),
                        "snapshot": source,
                    }
                )
            if len(json.dumps(snapshots, ensure_ascii=False)) > 2_000_000:
                raise ValueError(
                    "Organizational source snapshots exceed the size limit"
                )
            scope = "ORG:" + organization_id
            attrs = {
                "scope": "organization",
                "organization_id": organization_id,
                "status": "active",
                "observation": text(observation, "observation"),
                "result": text(result, "result"),
                "interpretation": text(interpretation, "interpretation"),
                "validated_statement": text(validated_statement, "validated_statement"),
                "applicability": text(applicability, "applicability"),
                "limitations": text(limitations, "limitations"),
                "relevant_standards": standards,
                "source_lessons": snapshots,
                "source_lesson_refs": [
                    {
                        "project_id": item["project_id"],
                        "lesson_id": item["lesson_id"],
                        "digest": item["lesson_digest"],
                    }
                    for item in snapshots
                ],
                "validated_by": self.principal.actor_id,
                "validated_at": timestamp(),
                "review_due": due.isoformat(),
                "rationale": text(rationale, "rationale"),
            }
            node = GraphNode(
                lesson_id, scope, NodeType.LESSON, text(title, "title"), attrs
            )
            self.store.add_subgraph([node], [])
            self.store.put_record(
                scope,
                "organizational_lesson_status",
                lesson_id,
                {
                    "status": "active",
                    "changed_by": self.principal.actor_id,
                    "changed_at": timestamp(),
                    "rationale": attrs["rationale"],
                },
            )
            self._event(scope, lesson_id, "organizational_lesson_validated")
            return asdict(node)

    def review_organizational_lesson(
        self,
        organization_id: str,
        lesson_id: str,
        expected_digest: str,
        status: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Retire or suspend immutable organizational knowledge with attribution."""
        scope = "ORG:" + text(organization_id, "organization_id")
        self.check_access(scope, write=True, human=True)
        if status not in {"under_review", "retired", "superseded"}:
            raise ValueError(
                "Organizational lesson review must suspend, retire or supersede"
            )
        rationale = text(rationale, "rationale")
        with self.store.transaction():
            lesson = self.store.get_node(scope, text(lesson_id, "lesson_id"))
            if lesson is None or lesson.attributes.get("scope") != "organization":
                raise GraphIntegrityError(
                    "Organizational lesson does not exist in the granted scope"
                )
            previous = self.store.get_record(
                scope, "organizational_lesson_status", lesson_id
            )
            if not previous:
                raise GraphIntegrityError("Organizational lesson has no status history")
            if (
                digest({"lesson": asdict(lesson), "status": previous})
                != expected_digest
            ):
                raise ValueError(
                    "Organizational lesson status changed; refresh before review"
                )
            if previous["status"] != "active":
                if (
                    previous["status"] == status
                    and previous["changed_by"] == self.principal.actor_id
                    and previous["rationale"] == rationale
                ):
                    return previous
                raise ValueError(
                    "Only active organizational knowledge can change status"
                )
            current = {
                "status": status,
                "previous_status": previous["status"],
                "changed_by": self.principal.actor_id,
                "changed_at": timestamp(),
                "rationale": rationale,
            }
            self.store.put_record(
                scope, "organizational_lesson_status", lesson_id, current, replace=True
            )
            self._event(
                scope, lesson_id, "organizational_lesson_status_changed", status=status
            )
            return current

    def import_organizational_lesson(
        self,
        project_id: str,
        organization_id: str,
        lesson_id: str,
        import_id: str,
        rationale: str,
        source_digest: str,
    ) -> dict[str, Any]:
        """Explicitly import active org knowledge as a governed project reference."""
        self.check_access(project_id, write=True, human=True)
        scope = "ORG:" + text(organization_id, "organization_id")
        self.check_access(scope)
        rationale = text(rationale, "rationale")
        with self.store.transaction():
            source = self.store.get_node(scope, text(lesson_id, "lesson_id"))
            if source is None or source.node_type != NodeType.LESSON:
                raise GraphIntegrityError(
                    "Organizational lesson does not exist in the granted scope"
                )
            attrs = source.attributes
            current_status = self.store.get_record(
                scope, "organizational_lesson_status", lesson_id
            )
            if (
                attrs.get("scope") != "organization"
                or not current_status
                or current_status.get("status") != "active"
            ):
                raise ValueError("Only active organizational knowledge can be imported")
            if date.fromisoformat(attrs["review_due"]) <= datetime.now(UTC).date():
                raise ValueError(
                    "Organizational knowledge is due for review and cannot be imported"
                )
            source_record = asdict(source)
            if (
                digest({"lesson": source_record, "status": current_status})
                != source_digest
            ):
                raise ValueError(
                    "Organizational lesson changed; refresh before importing"
                )
            existing = self.store.get_node(project_id, import_id)
            if existing:
                if (
                    existing.attributes.get("source_organizational_lesson_digest")
                    == digest(source_record)
                    and existing.attributes.get("import_rationale") == rationale
                ):
                    return asdict(existing)
                raise ValueError(
                    "Import ID already identifies different organizational knowledge"
                )
            node = GraphNode(
                text(import_id, "import_id"),
                project_id,
                NodeType.LESSON,
                source.title,
                {
                    **attrs,
                    "scope": "imported_organizational",
                    "status": "active",
                    "source_organization_id": organization_id,
                    "source_organizational_lesson_id": lesson_id,
                    "source_organizational_lesson_digest": digest(source_record),
                    "source_organizational_lesson": source_record,
                    "imported_by": self.principal.actor_id,
                    "imported_at": timestamp(),
                    "import_rationale": rationale,
                    "approved_by": self.principal.actor_id,
                },
            )
            self.store.add_subgraph([node], [])
            self._event(
                project_id,
                import_id,
                "organizational_lesson_imported",
                organization_id=organization_id,
                source_lesson_id=lesson_id,
            )
            return asdict(node)

    def backup(self, destination: str | Path) -> None:
        if self.principal.role != "reviewer":
            raise PermissionError("Human reviewer route required")
        # A full SQLite backup includes all projects, so every project must be granted.
        projects = self.store.project_ids()
        org_scopes = {"ORG:" + value for value in self.principal.organization_ids}
        if not projects.issubset(self.principal.project_ids | org_scopes):
            raise PermissionError("Backup includes ungranted projects")
        self.store.backup(destination)
