"""Immutable, project-scoped proposal readiness records."""

import copy

import pytest

from engineering_registry.demo import seed_demo
from engineering_registry.models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationshipType,
)
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import GraphIntegrityError, SQLiteGraphStore


@pytest.fixture
def proposal_registry(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("proposal-agent", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("proposal-reviewer", frozenset({"DEMO"}), "reviewer")
        )
        candidate = {
            "requirement_id": "R-PROPOSAL",
            "title": "Synthetic proposal requirement",
            "source_text": "Inspect weekly",
            "requirement_type": "mandatory",
            "obligation_type": "testing",
            "action": "inspect",
            "source_evidence_id": "evidence:EB",
            "confidence": "high",
            "uncertainties": [],
            "extraction_method": "deterministic_text",
        }
        pending = agent.propose_requirement_candidate("DEMO", candidate)
        human.review_requirement_candidate(
            "DEMO",
            pending["candidate_id"],
            pending["candidate_digest"],
            True,
            "Synthetic fixture source checked",
        )
        requirement_id = "requirement:R-PROPOSAL"
        store.save_issue(
            EngineeringIssue(
                "issue:PROPOSAL",
                "DEMO",
                "Synthetic proposal readiness review",
                requirement_ids=[requirement_id],
                attributes={"work_type": "proposal_readiness"},
            )
        )
        nodes = []
        edges = []
        for document_id, revision, evidence_id, body in (
            ("TEMPLATE", "A", "evidence:TEMPLATE", "Scope\nPrice\nValidity"),
            ("CANDIDATE", "1", "evidence:CANDIDATE", "Scope\nPrice\nValidity"),
        ):
            document_node_id = f"document:{document_id}"
            revision_node_id = f"document-revision:{document_id}:{revision}"
            nodes.extend(
                [
                    GraphNode(
                        document_node_id,
                        "DEMO",
                        NodeType.DOCUMENT,
                        document_id,
                        {"document_id": document_id},
                    ),
                    GraphNode(
                        revision_node_id,
                        "DEMO",
                        NodeType.DOCUMENT_REVISION,
                        f"{document_id} revision {revision}",
                        {
                            "document_id": document_id,
                            "revision": revision,
                            "governing_status": "current",
                        },
                    ),
                    GraphNode(
                        evidence_id,
                        "DEMO",
                        NodeType.EVIDENCE,
                        evidence_id,
                        {
                            "document_id": document_id,
                            "revision": revision,
                            "page": 1,
                            "locator": "page=1",
                            "text": body,
                            "governing_status": "current",
                        },
                    ),
                ]
            )
            edges.extend(
                [
                    GraphEdge(
                        f"{document_node_id}:revision:{revision}",
                        "DEMO",
                        document_node_id,
                        RelationshipType.HAS_REVISION,
                        revision_node_id,
                    ),
                    GraphEdge(
                        f"{evidence_id}:revision",
                        "DEMO",
                        evidence_id,
                        RelationshipType.DERIVED_FROM,
                        revision_node_id,
                        [evidence_id],
                    ),
                ]
            )
        store.add_subgraph(nodes, edges)
        yield database, agent, human, requirement_id


def valid_payload(requirement_id="requirement:R-PROPOSAL"):
    return {
        "project_id": "DEMO",
        "submission_id": "submission:PROPOSAL-1",
        "title": "Synthetic proposal submission",
        "issue_id": "issue:PROPOSAL",
        "candidate_revision_id": "document-revision:CANDIDATE:1",
        "template_revision_id": "document-revision:TEMPLATE:A",
        "source_requirement_ids": [requirement_id],
        "protected_sections": [
            {
                "template_evidence_id": "evidence:TEMPLATE",
                "candidate_evidence_id": "evidence:CANDIDATE",
                "label": "Commercial terms",
            }
        ],
        "scope_matrix": [
            {
                "matrix_id": "matrix:R-PROPOSAL",
                "requirement_id": requirement_id,
                "disposition": "included",
                "proposal_evidence_ids": ["evidence:CANDIDATE"],
                "qualification": None,
                "unresolved_question": None,
                "owner": None,
            }
        ],
    }


def test_submission_persists_exact_revision_requirement_and_evidence_links(
    proposal_registry,
):
    _, agent, _, requirement_id = proposal_registry
    payload = valid_payload(requirement_id)
    record = agent.propose_submission(**payload)

    assert record["node_type"] == NodeType.SUBMISSION
    assert (
        record["attributes"]["candidate_revision_id"]
        == payload["candidate_revision_id"]
    )
    assert (
        record["attributes"]["template_revision_id"] == payload["template_revision_id"]
    )
    assert record["attributes"]["scope_matrix"] == payload["scope_matrix"]
    assert record == agent.propose_submission(**payload)
    links = {
        edge.target_node_id for edge in agent.store.get_edges("DEMO", record["node_id"])
    }
    assert {
        "issue:PROPOSAL",
        payload["candidate_revision_id"],
        payload["template_revision_id"],
        requirement_id,
        "evidence:TEMPLATE",
        "evidence:CANDIDATE",
    } <= links
    with pytest.raises(GraphIntegrityError):
        agent.propose_submission(**{**payload, "title": "Changed content"})


def test_invalid_submission_reference_rolls_back_every_graph_write(proposal_registry):
    _, agent, _, requirement_id = proposal_registry
    payload = valid_payload(requirement_id)
    payload["submission_id"] = "submission:INVALID"
    payload["scope_matrix"] = copy.deepcopy(payload["scope_matrix"])
    payload["scope_matrix"][0]["proposal_evidence_ids"] = ["missing-evidence"]

    with pytest.raises(GraphIntegrityError):
        agent.propose_submission(**payload)
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "submission:INVALID")


def test_submission_context_binds_linked_sources_and_current_human_review_state(
    proposal_registry,
):
    _, agent, human, requirement_id = proposal_registry
    payload = valid_payload(requirement_id)
    agent.propose_submission(**payload)
    before = agent.submission_context("DEMO", payload["submission_id"])
    assert before["submission_digest"]
    assert before["submission"]["node_type"] == NodeType.SUBMISSION
    assert [row["node_id"] for row in before["requirements"]] == [requirement_id]
    assert set(before["source_evidence_ids"]) == {
        "evidence:EB",
        "evidence:TEMPLATE",
        "evidence:CANDIDATE",
    }
    assert {row["evidence_id"] for row in before["source_control"]} == set(
        before["source_evidence_ids"]
    )
    assert all(
        row["status"] == "unreviewed_text_available"
        for row in before["evidence_review"]
    )

    source = human.document_control("DEMO", "document-revision:CANDIDATE:1")
    human.review_document_control(
        "DEMO",
        "document-revision:CANDIDATE:1",
        source["control_digest"],
        "current",
        ["evidence:CANDIDATE"],
        "Synthetic candidate is current",
    )
    review = human.evidence_review("DEMO", "evidence:CANDIDATE")
    human.review_evidence(
        "DEMO",
        "evidence:CANDIDATE",
        review["review_digest"],
        "visually_reviewed",
        "Synthetic candidate text checked",
    )
    after = agent.submission_context("DEMO", payload["submission_id"])
    assert after["submission_digest"] != before["submission_digest"]
    assert (
        next(
            row
            for row in after["source_control"]
            if row["evidence_id"] == "evidence:CANDIDATE"
        )["status"]
        == "current"
    )
    assert (
        next(
            row
            for row in after["evidence_review"]
            if row["evidence_id"] == "evidence:CANDIDATE"
        )["status"]
        == "visually_reviewed"
    )


@pytest.mark.parametrize(
    "case",
    [
        "foreign_issue",
        "foreign_requirement",
        "foreign_evidence",
        "wrong_revision_type",
        "missing_requirement",
        "duplicate_requirement_row",
        "missing_qualification",
        "missing_owner",
        "missing_unresolved_question",
        "over_limit_sections",
        "over_limit_rows",
        "candidate_evidence_from_template",
    ],
)
def test_invalid_submission_contract_is_rejected_without_partial_records(
    proposal_registry, case
):
    _, agent, _, requirement_id = proposal_registry
    payload = valid_payload(requirement_id)
    payload["submission_id"] = "submission:INVALID-" + case
    if case == "foreign_issue":
        payload["issue_id"] = "issue:FOREIGN"
        agent.store.save_issue(
            EngineeringIssue(
                "issue:FOREIGN",
                "OTHER",
                "Foreign proposal readiness issue",
                attributes={"work_type": "proposal_readiness"},
            )
        )
    elif case == "foreign_requirement":
        foreign_id = "requirement:FOREIGN"
        agent.store.add_node(
            GraphNode(
                foreign_id,
                "OTHER",
                NodeType.REQUIREMENT,
                "Foreign accepted requirement",
                {"review_status": "reviewed", "review_decision_id": "decision:FOREIGN"},
            )
        )
        payload["source_requirement_ids"] = [foreign_id]
        payload["scope_matrix"][0]["requirement_id"] = foreign_id
    elif case == "foreign_evidence":
        revision_id = "document-revision:FOREIGN:1"
        agent.store.add_subgraph(
            [
                GraphNode(
                    revision_id,
                    "OTHER",
                    NodeType.DOCUMENT_REVISION,
                    "Foreign revision",
                    {"document_id": "FOREIGN", "revision": "1"},
                ),
                GraphNode(
                    "evidence:FOREIGN",
                    "OTHER",
                    NodeType.EVIDENCE,
                    "Foreign evidence",
                    {"document_id": "FOREIGN", "revision": "1", "text": "Foreign"},
                ),
            ],
            [
                GraphEdge(
                    "foreign-evidence:revision",
                    "OTHER",
                    "evidence:FOREIGN",
                    RelationshipType.DERIVED_FROM,
                    revision_id,
                    ["evidence:FOREIGN"],
                )
            ],
        )
        payload["scope_matrix"][0]["proposal_evidence_ids"] = ["evidence:FOREIGN"]
    elif case == "wrong_revision_type":
        payload["candidate_revision_id"] = "evidence:CANDIDATE"
    elif case == "missing_requirement":
        payload["source_requirement_ids"] = ["requirement:MISSING"]
    elif case == "duplicate_requirement_row":
        payload["scope_matrix"] *= 2
        payload["scope_matrix"][1]["matrix_id"] = "matrix:duplicate"
    elif case == "missing_qualification":
        payload["scope_matrix"][0].update(disposition="excluded", qualification=" ")
    elif case == "missing_owner":
        payload["scope_matrix"][0].update(disposition="interface_only", owner=None)
    elif case == "missing_unresolved_question":
        payload["scope_matrix"][0].update(
            disposition="unresolved", unresolved_question=" "
        )
    elif case == "over_limit_sections":
        payload["protected_sections"] *= 21
    elif case == "over_limit_rows":
        payload["scope_matrix"] *= 201
    elif case == "candidate_evidence_from_template":
        payload["scope_matrix"][0]["proposal_evidence_ids"] = ["evidence:TEMPLATE"]

    with pytest.raises((GraphIntegrityError, ValueError)):
        agent.propose_submission(**payload)
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", payload["submission_id"])
