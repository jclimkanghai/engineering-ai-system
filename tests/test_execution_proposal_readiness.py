"""Deterministic V2 checks for bounded proposal-readiness inputs."""

import copy

import pytest

from engineering_execution.tools import TOOLS, run_tool, validate_parameters
from engineering_registry.demo import seed_demo
from engineering_registry.models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationshipType,
)
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore


@pytest.fixture
def proposal_snapshot(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("proposal-agent", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("proposal-reviewer", frozenset({"DEMO"}), "reviewer")
        )
        candidate = {
            "requirement_id": "R-PROPOSAL",
            "title": "Proposal requirement",
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
            "Synthetic proposal fixture",
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
        nodes, edges = [], []
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
        agent.propose_submission(
            "DEMO",
            "submission:PROPOSAL-1",
            "Synthetic proposal",
            "issue:PROPOSAL",
            "document-revision:CANDIDATE:1",
            "document-revision:TEMPLATE:A",
            [requirement_id],
            [
                {
                    "template_evidence_id": "evidence:TEMPLATE",
                    "candidate_evidence_id": "evidence:CANDIDATE",
                    "label": "Terms",
                }
            ],
            [
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
        )
        for revision_id, evidence_id in (
            ("document_revision:BASIS-B%3AB", "evidence:EB"),
            ("document-revision:CANDIDATE:1", "evidence:CANDIDATE"),
            ("document-revision:TEMPLATE:A", "evidence:TEMPLATE"),
        ):
            control = human.document_control("DEMO", revision_id)
            human.review_document_control(
                "DEMO",
                revision_id,
                control["control_digest"],
                "current",
                [evidence_id],
                "Synthetic fixture source checked",
            )
        for evidence_id in ("evidence:EB", "evidence:CANDIDATE", "evidence:TEMPLATE"):
            review = human.evidence_review("DEMO", evidence_id)
            human.review_evidence(
                "DEMO",
                evidence_id,
                review["review_digest"],
                "visually_reviewed",
                "Synthetic fixture text checked",
            )
        snapshot = agent.task_context("DEMO", "issue:PROPOSAL")["execution_snapshot"]
        yield snapshot


def test_proposal_readiness_checks_exact_protected_text_and_controls(proposal_snapshot):
    result = run_tool(
        "validate_proposal_readiness",
        proposal_snapshot,
        {"submission_id": "submission:PROPOSAL-1"},
    )
    assert TOOLS["validate_proposal_readiness"] == "1"
    assert result["ready_for_human_review"] is True
    assert result["submission_id"] == "submission:PROPOSAL-1"
    assert result["protected_section_checks"][0]["unchanged"] is True
    assert result["requirement_coverage"]["exact_coverage"] is True
    assert "does not establish contractual acceptance" in result["warnings"][0]


def test_changed_protected_text_fails_and_identifies_both_evidence_records(
    proposal_snapshot,
):
    snapshot = copy.deepcopy(proposal_snapshot)
    evidence = next(
        n for n in snapshot["records"] if n["node_id"] == "evidence:CANDIDATE"
    )
    evidence["attributes"]["text"] = "Scope\nChanged price\nValidity"
    result = run_tool(
        "validate_proposal_readiness",
        snapshot,
        {"submission_id": "submission:PROPOSAL-1"},
    )
    check = result["protected_section_checks"][0]
    assert result["ready_for_human_review"] is False
    assert check["template_evidence_id"] == "evidence:TEMPLATE"
    assert check["candidate_evidence_id"] == "evidence:CANDIDATE"
    assert check["removed_lines"] == ["Price"]
    assert check["added_lines"] == ["Changed price"]


@pytest.mark.parametrize("attribute", ["text", "locator"])
def test_missing_protected_source_data_fails_closed(proposal_snapshot, attribute):
    snapshot = copy.deepcopy(proposal_snapshot)
    evidence = next(
        n for n in snapshot["records"] if n["node_id"] == "evidence:CANDIDATE"
    )
    if attribute == "locator":
        for key in ("page", "section", "locator"):
            evidence["attributes"].pop(key, None)
    else:
        evidence["attributes"].pop(attribute)
    result = run_tool(
        "validate_proposal_readiness",
        snapshot,
        {"submission_id": "submission:PROPOSAL-1"},
    )
    assert result["ready_for_human_review"] is False
    assert any(
        "UNKNOWN / INSUFFICIENT INFORMATION" in item for item in result["warnings"]
    )


def test_requirement_coverage_and_unresolved_rows_are_explicit(proposal_snapshot):
    snapshot = copy.deepcopy(proposal_snapshot)
    submission = next(
        n for n in snapshot["records"] if n["node_id"] == "submission:PROPOSAL-1"
    )
    row = submission["attributes"]["scope_matrix"][0]
    row.update(
        disposition="unresolved",
        unresolved_question="Confirm battery limit",
        owner="Client",
    )
    content = {
        k: v for k, v in submission["attributes"].items() if k != "content_digest"
    }
    submission["attributes"]["content_digest"] = digest(content)
    result = run_tool(
        "validate_proposal_readiness",
        snapshot,
        {"submission_id": "submission:PROPOSAL-1"},
    )
    assert result["ready_for_human_review"] is False
    assert result["unresolved_matrix_rows"] == ["matrix:R-PROPOSAL"]

    row["disposition"] = "included"
    submission["attributes"]["scope_matrix"] = []
    content = {
        k: v for k, v in submission["attributes"].items() if k != "content_digest"
    }
    submission["attributes"]["content_digest"] = digest(content)
    omitted = run_tool(
        "validate_proposal_readiness",
        snapshot,
        {"submission_id": "submission:PROPOSAL-1"},
    )
    assert omitted["ready_for_human_review"] is False
    assert omitted["requirement_coverage"]["omitted_requirement_ids"] == [
        "requirement:R-PROPOSAL"
    ]


def test_parameters_are_exact_and_source_review_blockers_prevent_readiness(
    proposal_snapshot,
):
    with pytest.raises(ValueError, match="Invalid parameters"):
        validate_parameters(
            "validate_proposal_readiness", {"submission_id": "x", "extra": 1}
        )
    snapshot = copy.deepcopy(proposal_snapshot)
    evidence_review = next(
        row
        for row in snapshot["evidence_review"]
        if row["evidence_id"] == "evidence:EB"
    )
    evidence_review["status"] = "insufficient"
    result = run_tool(
        "validate_proposal_readiness",
        snapshot,
        {"submission_id": "submission:PROPOSAL-1"},
    )
    assert result["ready_for_human_review"] is False
    assert any(
        check["evidence_id"] == "evidence:EB" and not check["passed"]
        for check in result["source_checks"]
    )
