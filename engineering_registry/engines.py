from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    IssueStatus,
    NodeType,
    RelationshipType,
)
from .retrieval import ObjectCentricRetriever
from .store import EngineeringGraphStore, GraphIntegrityError


@dataclass(frozen=True)
class ReviewContext:
    project_id: str
    task: str
    issues: list[EngineeringIssue]


class ReviewPipeline(Protocol):
    def run(self, request: Any) -> Any: ...


class ContextEngine:
    def __init__(self, store: EngineeringGraphStore) -> None:
        self.store = store

    def assemble(
        self, project_id: str, task: str, issue_ids: list[str]
    ) -> ReviewContext:
        if not project_id.strip():
            raise ValueError("project_id must not be blank")
        if not task.strip():
            raise ValueError("task must not be blank")
        issues = []
        for issue_id in issue_ids:
            issue = self.store.get_issue(project_id, issue_id)
            if issue is None:
                raise GraphIntegrityError(
                    f"Issue is not available in project {project_id}: {issue_id}"
                )
            issues.append(issue)
        return ReviewContext(project_id=project_id, task=task.strip(), issues=issues)


class KnowledgeEngine:
    """Register extracted source objects without changing their provenance."""

    def __init__(self, store: EngineeringGraphStore) -> None:
        self.store = store

    def register_node(self, project_id: str, node: GraphNode) -> None:
        if node.project_id != project_id:
            raise GraphIntegrityError(
                "Knowledge record project_id does not match the active project"
            )
        self.store.add_node(node)


class ReviewEngine:
    """Run the existing review pipeline once and expose graph retrieval."""

    def __init__(
        self, pipeline: ReviewPipeline, store: EngineeringGraphStore | None = None
    ) -> None:
        self.pipeline = pipeline
        self.retriever = ObjectCentricRetriever(store) if store is not None else None

    def run(self, request: Any) -> Any:
        return self.pipeline.run(request)

    def retrieve_issue(self, project_id: str, issue_id: str, **filters: Any) -> Any:
        if self.retriever is None:
            raise RuntimeError(
                "ReviewEngine needs a graph store for object-centric retrieval"
            )
        return self.retriever.retrieve_issue(project_id, issue_id, **filters)


class ControlEngine:
    def __init__(self, store: EngineeringGraphStore) -> None:
        self.store = store

    def transition(
        self,
        project_id: str,
        issue_id: str,
        to_status: IssueStatus,
        *,
        actor_id: str,
        rationale: str,
        evidence_ids: list[str] | None = None,
        human_approved: bool = False,
    ) -> EngineeringIssue:
        return self.store.transition_issue(
            project_id,
            issue_id,
            to_status,
            actor_id=actor_id,
            rationale=rationale,
            evidence_ids=evidence_ids,
            human_approved=human_approved,
        )


class LearningEngine:
    """Promote a project-local lesson only after an explicit human gate."""

    def __init__(self, store: EngineeringGraphStore) -> None:
        self.store = store

    def promote_project_lesson(
        self,
        project_id: str,
        lesson_id: str,
        title: str,
        *,
        issue_ids: list[str],
        evidence_ids: list[str],
        actor_id: str,
        rationale: str,
        human_approved: bool,
    ) -> GraphNode:
        if human_approved is not True:
            raise ValueError(
                "A human approval decision is required before promoting a lesson"
            )
        if not actor_id.strip() or not rationale.strip():
            raise ValueError("Lesson promotion requires a human actor and rationale")
        if not issue_ids or not evidence_ids:
            raise ValueError(
                "A project lesson must cite at least one issue and evidence record"
            )
        for issue_id in issue_ids:
            if self.store.get_issue(project_id, issue_id) is None:
                raise GraphIntegrityError(
                    f"Lesson issue does not exist in this project: {issue_id}"
                )
        for evidence_id in evidence_ids:
            node = self.store.get_node(project_id, evidence_id)
            if node is None or node.node_type != NodeType.EVIDENCE:
                raise GraphIntegrityError(
                    f"Lesson evidence is missing or mistyped: {evidence_id}"
                )

        lesson = GraphNode(
            lesson_id,
            project_id,
            NodeType.LESSON,
            title,
            {
                "approved_by": actor_id.strip(),
                "approval_rationale": rationale.strip(),
                "human_approved": True,
                "scope": "project",
            },
        )
        edges = [
            GraphEdge(
                f"{lesson_id}:issue:{issue_id}",
                project_id,
                lesson_id,
                RelationshipType.LEARNED_FROM,
                issue_id,
            )
            for issue_id in issue_ids
        ]
        edges.extend(
            GraphEdge(
                f"{lesson_id}:evidence:{evidence_id}",
                project_id,
                lesson_id,
                RelationshipType.SUPPORTED_BY,
                evidence_id,
                [evidence_id],
            )
            for evidence_id in evidence_ids
        )
        self.store.add_subgraph([lesson], edges)
        return lesson
