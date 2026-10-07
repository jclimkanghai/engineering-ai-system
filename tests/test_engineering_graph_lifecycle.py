import pytest

from pipelines.engineering_graph.models import (
    EngineeringIssue,
    GraphNode,
    IssueStatus,
    NodeType,
)
from pipelines.engineering_graph.store import GraphIntegrityError, SQLiteGraphStore


def test_lifecycle_requires_human_actor_and_rationale(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))

    with pytest.raises(ValueError, match="actor"):
        store.transition_issue(
            "P-1",
            "I-1",
            IssueStatus.OPEN,
            actor_id=" ",
            rationale="Reviewed",
            human_approved=True,
        )
    with pytest.raises(ValueError, match="human"):
        store.transition_issue(
            "P-1",
            "I-1",
            IssueStatus.OPEN,
            actor_id="engineer-1",
            rationale="Reviewed",
            human_approved=False,
        )
    assert store.get_issue("P-1", "I-1").status is IssueStatus.PROPOSED
    assert store.list_lifecycle_events("P-1", "I-1") == []
    store.close()


def test_issue_cannot_close_without_human_approval_and_resolving_evidence(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))
    store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.OPEN,
        actor_id="engineer-1",
        rationale="Accepted for project review",
        human_approved=True,
    )

    store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.ACCEPTED,
        actor_id="engineer-1",
        rationale="Reviewed and accepted",
        human_approved=True,
    )
    with pytest.raises(ValueError, match="closure evidence"):
        store.transition_issue(
            "P-1",
            "I-1",
            IssueStatus.CLOSED,
            actor_id="engineer-1",
            rationale="Resolved",
            human_approved=True,
        )
    with pytest.raises(ValueError, match="human"):
        store.transition_issue(
            "P-1",
            "I-1",
            IssueStatus.CLOSED,
            actor_id="engineer-1",
            rationale="Resolved",
            evidence_ids=["E-1"],
            human_approved=False,
        )
    assert store.get_issue("P-1", "I-1").status is IssueStatus.ACCEPTED
    assert len(store.list_lifecycle_events("P-1", "I-1")) == 2
    store.close()


def test_lifecycle_event_evidence_ids_must_resolve_even_before_closure(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))

    with pytest.raises(GraphIntegrityError, match="evidence"):
        store.transition_issue(
            "P-1",
            "I-1",
            IssueStatus.OPEN,
            actor_id="engineer-1",
            rationale="Based on missing evidence",
            evidence_ids=["MISSING"],
            human_approved=True,
        )
    assert store.get_issue("P-1", "I-1").status is IssueStatus.PROPOSED
    assert store.list_lifecycle_events("P-1", "I-1") == []
    store.close()


def test_close_records_approved_evidence_and_append_only_history(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.add_node(
        GraphNode("E-1", "P-1", NodeType.EVIDENCE, "Checked calculation Rev 4")
    )
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))
    store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.OPEN,
        actor_id="engineer-1",
        rationale="Issue reviewed",
        human_approved=True,
    )
    store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.ACCEPTED,
        actor_id="engineer-1",
        rationale="Reviewed and accepted",
        human_approved=True,
    )
    closed = store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.CLOSED,
        actor_id="engineer-1",
        rationale="Rev 4 check closes the query",
        evidence_ids=["E-1"],
        human_approved=True,
    )

    events = store.list_lifecycle_events("P-1", "I-1")
    assert closed.status is IssueStatus.CLOSED
    assert closed.closure_evidence_ids == ["E-1"]
    assert [event["to_status"] for event in events] == ["open", "accepted", "closed"]
    assert events[-1]["actor_id"] == "engineer-1"
    assert events[-1]["evidence_ids"] == ["E-1"]
    assert events[-1]["human_approved"] is True
    store.close()


def test_cross_project_closure_evidence_is_rejected_atomically(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.add_node(GraphNode("E-1", "P-2", NodeType.EVIDENCE, "Other project evidence"))
    store.save_issue(EngineeringIssue("I-1", "P-1", "Check pile capacity"))
    store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.OPEN,
        actor_id="engineer-1",
        rationale="Issue reviewed",
        human_approved=True,
    )
    store.transition_issue(
        "P-1",
        "I-1",
        IssueStatus.ACCEPTED,
        actor_id="engineer-1",
        rationale="Reviewed and accepted",
        human_approved=True,
    )

    with pytest.raises(GraphIntegrityError):
        store.transition_issue(
            "P-1",
            "I-1",
            IssueStatus.CLOSED,
            actor_id="engineer-1",
            rationale="Attempt cross-project evidence",
            evidence_ids=["E-1"],
            human_approved=True,
        )
    assert store.get_issue("P-1", "I-1").status is IssueStatus.ACCEPTED
    assert len(store.list_lifecycle_events("P-1", "I-1")) == 2
    store.close()
