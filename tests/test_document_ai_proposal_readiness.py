"""Document AI binds and assesses a proposal-readiness task without deciding it."""

import pytest

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.demo import seed_demo
from engineering_registry.models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationshipType,
)
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


@pytest.fixture
def proposal_flow(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("document-ai", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("reviewer", frozenset({"DEMO"}), "reviewer")
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
            "Synthetic proposal requirement reviewed",
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
                "Synthetic source authority checked",
            )
        for evidence_id in ("evidence:EB", "evidence:CANDIDATE", "evidence:TEMPLATE"):
            review = human.evidence_review("DEMO", evidence_id)
            human.review_evidence(
                "DEMO",
                evidence_id,
                review["review_digest"],
                "visually_reviewed",
                "Synthetic evidence text checked",
            )
        yield database, agent, human, DocumentAIWorkflow(agent), requirement_id


def test_document_ai_structures_exact_controlled_submission_task(proposal_flow):
    _, agent, _, workflow, requirement_id = proposal_flow
    task = workflow.structure_submission_task(
        "DEMO", "proposal-task", "issue:PROPOSAL", "submission:PROPOSAL-1"
    )
    plan = task["brain_plan"]
    context = agent.submission_context("DEMO", "submission:PROPOSAL-1")
    assert task["tool"] == "validate_proposal_readiness"
    assert task["parameters"] == {"submission_id": "submission:PROPOSAL-1"}
    assert plan["submission_id"] == "submission:PROPOSAL-1"
    assert plan["submission_digest"] == context["submission_digest"]
    assert (
        plan["proposal_readiness"]["candidate_revision_id"]
        == "document-revision:CANDIDATE:1"
    )
    assert plan["requirement_ids"] == [requirement_id]
    assert plan["acceptance_criteria"] == ["proposal_readiness"]
    assert any(
        "Contractual acceptance is unknown" in unknown for unknown in plan["unknowns"]
    )
    assert (
        workflow.structure_submission_task(
            "DEMO", "proposal-task", "issue:PROPOSAL", "submission:PROPOSAL-1"
        )
        == task
    )


def test_assessed_readiness_preserves_v2_output_and_never_records_human_decision(
    proposal_flow,
):
    _, agent, human, workflow, _ = proposal_flow
    before = {
        record_type: agent.list_records("DEMO", record_type)
        for record_type in ("decision", "verification", "lesson")
    }
    task = workflow.structure_submission_task(
        "DEMO", "proposal-task", "issue:PROPOSAL", "submission:PROPOSAL-1"
    )
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Synthetic test authorization"
    )
    outcome = workflow.execute_and_assess("DEMO", task["task_id"])
    result, assessment = outcome["result"], outcome["assessment"]
    assert result["attributes"]["outputs"]["ready_for_human_review"] is True
    assert assessment["attributes"]["checks"][0]["criterion"] == "proposal_readiness"
    assert assessment["attributes"]["checks"][0]["passed"] is True
    assert "ready_for_human_review" in assessment["attributes"]["checks"][0]["detail"]
    assert assessment["attributes"]["human_review_required"] is True
    for record_type, records in before.items():
        assert agent.list_records("DEMO", record_type) == records


def test_changed_source_control_invalidates_structured_submission_task(proposal_flow):
    _, agent, human, workflow, _ = proposal_flow
    task = workflow.structure_submission_task(
        "DEMO", "proposal-task", "issue:PROPOSAL", "submission:PROPOSAL-1"
    )
    control = human.document_control("DEMO", "document-revision:CANDIDATE:1")
    human.review_document_control(
        "DEMO",
        "document-revision:CANDIDATE:1",
        control["control_digest"],
        "superseded",
        ["evidence:CANDIDATE"],
        "Synthetic stale-source probe",
    )
    with pytest.raises(ValueError, match="different|changed"):
        workflow.structure_submission_task(
            "DEMO", "proposal-task", "issue:PROPOSAL", "submission:PROPOSAL-1"
        )
    assert (
        task["task_digest"]
        != agent.submission_context("DEMO", "submission:PROPOSAL-1")[
            "submission_digest"
        ]
    )
