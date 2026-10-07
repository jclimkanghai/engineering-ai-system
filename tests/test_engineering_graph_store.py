import sqlite3

import pytest

from pipelines.engineering_graph.models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationshipType,
)
from pipelines.engineering_graph.store import (
    GraphIntegrityError,
    GraphStoreError,
    SQLiteGraphStore,
)


def test_sqlite_graph_round_trips_nodes_and_edges(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    issue = GraphNode("issue-1", "project-a", NodeType.ENGINEERING_ISSUE, "Review pile")
    evidence = GraphNode("evidence-1", "project-a", NodeType.EVIDENCE, "Calc §8.4")
    store.add_node(issue)
    store.add_node(evidence)
    edge = GraphEdge(
        "edge-1",
        "project-a",
        "issue-1",
        RelationshipType.SUPPORTED_BY,
        "evidence-1",
        ["evidence-1"],
    )
    store.add_edge(edge)
    store.close()

    reopened = SQLiteGraphStore(tmp_path / "graph.sqlite")
    assert reopened.get_node("project-a", "issue-1") == issue
    assert reopened.get_edges("project-a", "issue-1") == [edge]
    assert reopened.schema_version == 1
    reopened.close()


def test_edges_cannot_cross_project_or_reference_missing_nodes(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.add_node(GraphNode("node-1", "project-a", NodeType.DOCUMENT, "Basis"))
    store.add_node(GraphNode("node-1", "project-b", NodeType.DOCUMENT, "Other basis"))

    with pytest.raises(GraphIntegrityError):
        store.add_edge(
            GraphEdge(
                "bad-edge",
                "project-a",
                "node-1",
                RelationshipType.RELATES_TO,
                "missing",
            )
        )
    assert store.get_edges("project-a", "node-1") == []
    assert store.get_node("project-b", "node-1").title == "Other basis"
    store.close()


def test_saving_issue_is_atomic_when_a_link_is_missing(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    issue = EngineeringIssue(
        "issue-1", "project-a", "Pile capacity", evidence_ids=["unknown-evidence"]
    )

    with pytest.raises(GraphIntegrityError):
        store.save_issue(issue)
    assert store.get_node("project-a", "issue-1") is None
    store.close()


def test_reimport_cannot_bypass_issue_lifecycle_history(tmp_path):
    from pipelines.engineering_graph.models import IssueStatus

    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    proposed = EngineeringIssue("issue-1", "project-a", "Pile capacity")
    store.save_issue(proposed)
    store.transition_issue(
        "project-a",
        "issue-1",
        IssueStatus.OPEN,
        actor_id="engineer-1",
        rationale="Reviewed",
        human_approved=True,
    )

    with pytest.raises(GraphIntegrityError, match="lifecycle"):
        store.save_issue(proposed)
    assert store.get_issue("project-a", "issue-1").status is IssueStatus.OPEN
    assert len(store.list_lifecycle_events("project-a", "issue-1")) == 1
    store.close()


def test_repeated_node_and_edge_writes_are_idempotent(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    issue = GraphNode("issue-1", "project-a", NodeType.ENGINEERING_ISSUE, "Review pile")
    evidence = GraphNode("evidence-1", "project-a", NodeType.EVIDENCE, "Calc §8.4")
    edge = GraphEdge(
        "edge-1", "project-a", "issue-1", RelationshipType.SUPPORTED_BY, "evidence-1"
    )

    store.add_node(issue)
    store.add_node(issue)
    store.add_node(evidence)
    store.add_edge(edge)
    store.add_edge(edge)

    assert store.get_edges("project-a", "issue-1") == [edge]
    store.close()


def test_subgraph_write_rolls_back_nodes_when_an_edge_is_invalid(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    new_node = GraphNode("new-1", "project-a", NodeType.LESSON, "Lesson")
    invalid_edge = GraphEdge(
        "edge-1", "project-a", "new-1", RelationshipType.DERIVED_FROM, "missing"
    )

    with pytest.raises(GraphIntegrityError):
        store.add_subgraph([new_node], [invalid_edge])
    assert store.get_node("project-a", "new-1") is None
    store.close()


def test_store_migrates_version_zero_schema_and_rejects_newer_schema(tmp_path):
    path = tmp_path / "graph.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE graph_schema(singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO graph_schema VALUES (1, 0)")
    connection.commit()
    connection.close()

    store = SQLiteGraphStore(path)
    assert store.schema_version == 1
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("UPDATE graph_schema SET version=99 WHERE singleton=1")
    connection.commit()
    connection.close()
    with pytest.raises(GraphStoreError, match="newer"):
        SQLiteGraphStore(path)


def test_edge_provenance_must_reference_an_evidence_node(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.add_node(GraphNode("A", "project-a", NodeType.DOCUMENT, "Document"))
    store.add_node(GraphNode("B", "project-a", NodeType.COMPONENT, "Component"))

    with pytest.raises(GraphIntegrityError, match="evidence"):
        store.add_edge(
            GraphEdge(
                "edge-1", "project-a", "B", RelationshipType.DERIVED_FROM, "A", ["A"]
            )
        )
    assert store.get_edges("project-a", "B") == []
    store.close()


def test_store_expands_home_directory_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    store = SQLiteGraphStore("~/private/graph.sqlite")

    assert (tmp_path / "private" / "graph.sqlite").exists()
    store.close()
