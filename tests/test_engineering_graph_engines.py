import pytest

from pipelines.engineering_graph.engines import (
    ContextEngine,
    ControlEngine,
    KnowledgeEngine,
    LearningEngine,
    ReviewEngine,
)
from pipelines.engineering_graph.models import (
    EngineeringIssue,
    GraphNode,
    IssueStatus,
    NodeType,
)
from pipelines.engineering_graph.store import GraphIntegrityError, SQLiteGraphStore


class RecordingPipeline:
    def __init__(self):
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        return {"status": "complete", "request": request}


def test_context_engine_builds_project_scoped_issue_context(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))
    context = ContextEngine(store).assemble("P-1", "Check the pile capacity", ["I-1"])

    assert context.project_id == "P-1"
    assert context.task == "Check the pile capacity"
    assert [issue.issue_id for issue in context.issues] == ["I-1"]
    with pytest.raises(GraphIntegrityError):
        ContextEngine(store).assemble("P-2", "Cross-project lookup", ["I-1"])
    store.close()


def test_knowledge_engine_rejects_cross_project_nodes(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    engine = KnowledgeEngine(store)
    with pytest.raises(GraphIntegrityError):
        engine.register_node(
            "P-1",
            GraphNode("D-1", "P-2", NodeType.DOCUMENT, "Other project"),
        )
    store.close()


def test_review_engine_delegates_one_request_to_existing_pipeline():
    pipeline = RecordingPipeline()
    result = ReviewEngine(pipeline).run({"review_mode": "allin"})

    assert len(pipeline.calls) == 1
    assert pipeline.calls[0] == {"review_mode": "allin"}
    assert result["status"] == "complete"


def test_control_engine_requires_human_approval(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))
    with pytest.raises(ValueError, match="human"):
        ControlEngine(store).transition(
            "P-1",
            "I-1",
            IssueStatus.OPEN,
            actor_id="agent",
            rationale="Model selected open",
            human_approved=False,
        )
    store.close()


def test_learning_engine_requires_human_governance_and_keeps_lesson_project_scoped(
    tmp_path,
):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.add_node(GraphNode("E-1", "P-1", NodeType.EVIDENCE, "Approved note §2"))
    store.save_issue(EngineeringIssue("I-1", "P-1", "Pile installation issue"))
    engine = LearningEngine(store)

    with pytest.raises(ValueError, match="human"):
        engine.promote_project_lesson(
            "P-1",
            "L-1",
            "Check driving criteria",
            issue_ids=["I-1"],
            evidence_ids=["E-1"],
            actor_id="reviewer-1",
            rationale="Verified",
            human_approved=False,
        )
    lesson = engine.promote_project_lesson(
        "P-1",
        "L-1",
        "Check driving criteria",
        issue_ids=["I-1"],
        evidence_ids=["E-1"],
        actor_id="reviewer-1",
        rationale="Verified",
        human_approved=True,
    )
    assert lesson.project_id == "P-1"
    assert lesson.node_type is NodeType.LESSON
    assert store.get_node("P-2", "L-1") is None
    assert store.get_node("P-1", "L-1").attributes["approved_by"] == "reviewer-1"
    store.close()
