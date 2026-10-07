from dataclasses import asdict

import pytest

from pipelines.engineering_graph.models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    IssueStatus,
    NodeType,
    RelationshipType,
)


def test_graph_nodes_and_edges_keep_typed_project_provenance():
    evidence = GraphNode(
        node_id="evidence-1",
        project_id="project-a",
        node_type=NodeType.EVIDENCE,
        title="Calculation Rev 3 §8.4",
        attributes={"document_id": "doc-1", "revision": "3", "locator": "§8.4"},
    )
    issue = GraphNode(
        node_id="issue-1",
        project_id="project-a",
        node_type=NodeType.ENGINEERING_ISSUE,
        title="Pile capacity requires review",
    )
    link = GraphEdge(
        edge_id="edge-1",
        project_id="project-a",
        source_node_id=issue.node_id,
        relationship=RelationshipType.SUPPORTED_BY,
        target_node_id=evidence.node_id,
        provenance_evidence_ids=["evidence-1"],
    )

    assert issue.node_type is NodeType.ENGINEERING_ISSUE
    assert link.relationship is RelationshipType.SUPPORTED_BY
    assert link.provenance_evidence_ids == [evidence.node_id]
    assert evidence.attributes["locator"] == "§8.4"


def test_engineering_issue_preserves_distinct_evidence_classes_and_links():
    issue = EngineeringIssue(
        issue_id="issue-1",
        project_id="project-a",
        title="Potential pile capacity shortfall",
        question="Is the proposed pile capacity adequate?",
        source_facts=["Demand is 1,850 kN; capacity is 1,420 kN."],
        interpretation="The stated capacity appears below the stated demand.",
        engineering_judgement="A design check is required before acceptance.",
        assumptions=["The cited load combination is governing."],
        unknowns=["Pile group interaction is not shown."],
        evidence_ids=["evidence-1", "evidence-2"],
        requirement_ids=["requirement-1"],
        status=IssueStatus.PROPOSED,
    )

    serialized = asdict(issue)
    assert serialized["source_facts"] != serialized["interpretation"]
    assert serialized["engineering_judgement"]
    assert serialized["assumptions"] == ["The cited load combination is governing."]
    assert serialized["unknowns"] == ["Pile group interaction is not shown."]
    assert serialized["evidence_ids"] == ["evidence-1", "evidence-2"]
    assert issue.status is IssueStatus.PROPOSED
    assert issue.human_review_required is True


def test_graph_models_reject_blank_project_identity_and_duplicate_links():
    with pytest.raises(ValueError, match="project_id"):
        GraphNode(
            node_id="node-1", project_id=" ", node_type=NodeType.DOCUMENT, title="Doc"
        )

    with pytest.raises(ValueError, match="duplicate"):
        EngineeringIssue(
            issue_id="issue-1",
            project_id="project-a",
            title="Issue",
            evidence_ids=["evidence-1", "evidence-1"],
        )
