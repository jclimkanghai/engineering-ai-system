import pytest

from pipelines.engineering_graph.models import EngineeringIssue, GraphNode, NodeType
from pipelines.engineering_graph.retrieval import ObjectCentricRetriever
from pipelines.engineering_graph.store import GraphIntegrityError, SQLiteGraphStore


def test_issue_retrieval_keeps_revision_conflicts_visible_and_traceable(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.add_node(
        GraphNode(
            "E-REV3",
            "P-1",
            NodeType.EVIDENCE,
            "Pile check Rev 3 §8.4",
            {
                "document_id": "CALC-1",
                "revision": "3",
                "page": 22,
                "section": "8.4",
                "locator": "§8.4",
                "text": "Capacity is 1,420 kN.",
                "issue_status": "approved",
                "governing_status": "superseded",
            },
        )
    )
    store.add_node(
        GraphNode(
            "E-REV4",
            "P-1",
            NodeType.EVIDENCE,
            "Pile check Rev 4 §8.4",
            {
                "document_id": "CALC-1",
                "revision": "4",
                "page": 25,
                "section": "8.4",
                "locator": "§8.4",
                "text": "Capacity is 1,850 kN.",
                "issue_status": "approved",
                "governing_status": "governing",
            },
        )
    )
    store.save_issue(
        EngineeringIssue(
            "I-1", "P-1", "Pile capacity comparison", evidence_ids=["E-REV3", "E-REV4"]
        )
    )

    result = ObjectCentricRetriever(store).retrieve_issue(
        "P-1", "I-1", requested_revision="4", task_query="pile capacity"
    )

    assert result.issue_id == "I-1"
    assert [item.evidence_id for item in result.evidence] == ["E-REV4", "E-REV3"]
    assert result.evidence[0].requested_revision_match is True
    assert result.evidence[1].requested_revision_match is False
    assert result.evidence[1].governing_status == "superseded"
    assert result.evidence[1].page == 22
    assert result.evidence[1].locator == "§8.4"
    assert any("revision" in warning.lower() for warning in result.warnings)
    store.close()


def test_retrieval_cannot_cross_project_boundaries(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "Private project issue"))

    with pytest.raises(GraphIntegrityError):
        ObjectCentricRetriever(store).retrieve_issue("P-2", "I-1")
    store.close()


def test_retrieval_rejects_dangling_evidence_in_issue_payload(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    issue = EngineeringIssue(
        "I-1", "P-1", "Unresolved evidence", evidence_ids=["E-MISSING"]
    )
    with pytest.raises(GraphIntegrityError):
        store.save_issue(issue)
    store.close()


def test_retrieval_marks_issue_without_evidence_as_insufficient_information(tmp_path):
    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    store.save_issue(EngineeringIssue("I-1", "P-1", "No source attached"))

    result = ObjectCentricRetriever(store).retrieve_issue("P-1", "I-1")

    assert result.evidence == []
    assert any("UNKNOWN / INSUFFICIENT INFORMATION" in item for item in result.warnings)
    store.close()
