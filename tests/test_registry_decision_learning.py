import pytest

from engineering_registry.models import GraphEdge, GraphNode, NodeType, RelationshipType
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import GraphIntegrityError
from tests.support.registry_decision_harness import setup_registry


def test_task_decision_level_sets_human_control_without_ai_override(tmp_path):
    store, agent, _ = setup_registry(tmp_path)
    try:
        decision = agent.record_task_decision(
            "P1",
            "I-P1",
            "D3",
            "Retain the interface and assess alternatives.",
            "The accepted arrangement constrains feasible options.",
            ["E-P1"],
            decision_level="D3",
        )
        attrs = decision["attributes"]
        assert attrs["authority"] == "ai_lead"
        assert attrs["status"] == "proposed"
        assert attrs["decision_level"] == "D3"
        assert attrs["human_review_required"] is True
        assert attrs["human_acceptance_required"] is True
        assert attrs["formal_authority_required"] is False
        with pytest.raises(ValueError, match="derived"):
            agent.record_task_decision(
                "P1",
                "I-P1",
                "D3b",
                "Override",
                "Reason",
                ["E-P1"],
                decision_level="D3",
                human_acceptance_required=False,
            )
    finally:
        store.close()


def test_task_decision_rejects_requirement_outside_issue_source_context(tmp_path):
    store, agent, _ = setup_registry(tmp_path)
    try:
        with pytest.raises(GraphIntegrityError, match="linked to its issue sources"):
            agent.record_task_decision(
                "P1",
                "I-P1",
                "D-INVALID",
                "Apply a requirement",
                "It controls the design",
                ["E-P1"],
                requirement_ids=["requirement:FOREIGN"],
            )
        assert agent.list_records("P1", "decision") == []
    finally:
        store.close()


def test_human_review_is_digest_bound_and_added_to_future_context(tmp_path):
    store, agent, human = setup_registry(tmp_path)
    try:
        proposed = agent.record_task_decision(
            "P1",
            "I-P1",
            "D3",
            "Retain layout",
            "Interface basis",
            ["E-P1"],
            decision_level="D3",
        )
        with pytest.raises(PermissionError):
            agent.review_task_decision(
                "P1", "D3", digest(proposed), True, ["E-P1"], "Approved"
            )
        with pytest.raises(ValueError, match="Stale"):
            human.review_task_decision(
                "P1", "D3", "old-digest", True, ["E-P1"], "Approved"
            )
        review = human.review_task_decision(
            "P1",
            "D3",
            digest(proposed),
            True,
            ["E-P1"],
            "Checked source and accepted constraint.",
        )
        context = agent.task_context("P1", "I-P1")
        decisions = [r for r in context["records"] if r["node_type"] == "decision"]
        assert {r["attributes"].get("decision_type") for r in decisions} == {
            "task",
            "human_task_review",
        }
        task_proposal = next(r for r in decisions if r["node_id"] == "D3")
        assert (
            task_proposal["attributes"]["human_review"]["node_id"] == review["node_id"]
        )
        assert (
            task_proposal["attributes"]["human_review"]["attributes"]["disposition"]
            == "accept"
        )
        assert review["attributes"]["reviewed_by"] == "engineer"
        with pytest.raises(ValueError, match="already reviewed"):
            human.review_task_decision(
                "P1", "D3", digest(proposed), False, ["E-P1"], "Changed mind"
            )
    finally:
        store.close()


def test_d4_acceptance_requires_and_links_formal_authority_reference(tmp_path):
    store, agent, human = setup_registry(tmp_path)
    try:
        proposal = agent.record_task_decision(
            "P1",
            "I-P1",
            "D4",
            "Apply approved basis",
            "Authority governs this choice",
            ["E-P1"],
            decision_level="D4",
        )
        with pytest.raises(ValueError, match="formal authority"):
            human.review_task_decision(
                "P1", "D4", digest(proposal), True, ["E-P1"], "Accepted without source"
            )
        review = human.review_task_decision(
            "P1",
            "D4",
            digest(proposal),
            True,
            ["E-P1"],
            "Accepted against authority",
            formal_authority_reference="E-P1",
        )
        edges = store.get_edges("P1", review["node_id"])
        assert any(edge.target_node_id == "E-P1" for edge in edges)
        assert review["attributes"]["formal_authority_reference"] == "E-P1"
    finally:
        store.close()


def test_human_outcome_decisions_are_reconstructed_in_later_task_context(tmp_path):
    store, agent, human = setup_registry(tmp_path)
    try:
        decision = GraphNode(
            "HUMAN-OUTCOME",
            "P1",
            NodeType.DECISION,
            "Retain the reviewed interface",
            {
                "issue_id": "I-P1",
                "disposition": "accept",
                "authority": "human_engineer",
                "rationale": "The cited interface requirement remains governing.",
                "reviewed_by": "engineer",
                "evidence_ids": ["E-P1"],
                "reasoning_evidence_status": "verified_human_reasoning",
                "reasoning_evidence_ids": ["E-P1"],
            },
        )
        store.add_subgraph(
            [decision],
            [
                GraphEdge(
                    "HUMAN-OUTCOME:issue",
                    "P1",
                    "I-P1",
                    RelationshipType.DECIDED_BY,
                    "HUMAN-OUTCOME",
                )
            ],
        )
        context = agent.task_context("P1", "I-P1")
        stored = next(
            record
            for record in context["records"]
            if record["node_id"] == "HUMAN-OUTCOME"
        )
        assert (
            stored["attributes"]["reasoning_evidence_status"]
            == "verified_human_reasoning"
        )
        assert stored["attributes"]["reviewed_by"] == "engineer"
    finally:
        store.close()


def test_relevant_prior_decisions_follow_shared_project_evidence_into_new_issue(
    tmp_path,
):
    store, agent, _ = setup_registry(tmp_path)
    try:
        prior = GraphNode(
            "PRIOR-HUMAN-DECISION",
            "P1",
            NodeType.DECISION,
            "Retain tested interface",
            {
                "issue_id": "I-P1",
                "disposition": "accept",
                "authority": "human_engineer",
                "rationale": "The cited interface basis was reviewed.",
                "reviewed_by": "engineer",
                "evidence_ids": ["E-P1"],
                "reasoning_evidence_status": "verified_human_reasoning",
                "reasoning_evidence_ids": ["E-P1"],
            },
        )
        store.add_subgraph(
            [prior],
            [
                GraphEdge(
                    "PRIOR-HUMAN-DECISION:evidence",
                    "P1",
                    "PRIOR-HUMAN-DECISION",
                    RelationshipType.SUPPORTED_BY,
                    "E-P1",
                    ["E-P1"],
                )
            ],
        )
        agent.propose_issue("P1", "I-NEW", "Related review", ["E-P1"])
        context = agent.task_context("P1", "I-NEW")
        prior_context = next(
            record
            for record in context["records"]
            if record["node_id"] == "PRIOR-HUMAN-DECISION"
        )
        assert prior_context["context_match"]["shared_source_ids"] == ["E-P1"]
    finally:
        store.close()


def test_organizational_lesson_requires_grant_human_validation_and_explicit_import(
    tmp_path,
):
    store, agent, human = setup_registry(tmp_path)
    try:
        project_lesson = human.promote_lesson(
            "P1",
            "L-P1",
            "Validated project lesson",
            ["I-P1"],
            ["E-P1"],
            "Reviewed outcome",
        )
        organization_lesson = human.promote_organizational_lesson(
            "ACME",
            "OL-1",
            "Lesson across comparable marine interfaces",
            [("P1", "L-P1")],
            observation="The project retained pile spacing.",
            result="The selected interface passed the local review gate.",
            interpretation="The fixed interface constrained the alternatives.",
            validated_statement="For comparable fixed interfaces, test alternatives without assuming the precedent governs.",
            applicability="Comparable marine interfaces with matching constraints.",
            limitations="Not a design rule; verify project requirements first.",
            relevant_standards=["Project standards take precedence"],
            review_due="2027-10-01",
            rationale="Two reviewers confirmed the applicability boundary.",
        )
        assert organization_lesson["project_id"] == "ORG:ACME"
        assert organization_lesson["attributes"]["status"] == "active"
        org_lesson_digest = digest(
            {
                "lesson": human.get_record("ORG:ACME", "OL-1"),
                "status": store.get_record(
                    "ORG:ACME", "organizational_lesson_status", "OL-1"
                ),
            }
        )

        with pytest.raises(PermissionError):
            agent.import_organizational_lesson(
                "P2",
                "ACME",
                "OL-1",
                "IMPORT-1",
                "Use as a reference",
                org_lesson_digest,
            )
        imported = human.import_organizational_lesson(
            "P2",
            "ACME",
            "OL-1",
            "IMPORT-1",
            "Reviewed applicability for P2",
            org_lesson_digest,
        )
        assert imported["attributes"]["scope"] == "imported_organizational"
        p1_context = agent.task_context("P1", "I-P1")
        p2_context = agent.task_context("P2", "I-P2")
        assert "OL-1" not in p1_context["lesson_ids"]
        assert "IMPORT-1" not in p1_context["lesson_ids"]
        assert "IMPORT-1" in p2_context["lesson_ids"]
        assert project_lesson["attributes"]["scope"] == "project"

        with pytest.raises(PermissionError):
            agent.review_organizational_lesson(
                "ACME", "OL-1", org_lesson_digest, "retired", "Withdrawn"
            )
        current_lesson = human.get_record("ORG:ACME", "OL-1")
        current_status = store.get_record(
            "ORG:ACME", "organizational_lesson_status", "OL-1"
        )
        human.review_organizational_lesson(
            "ACME",
            "OL-1",
            digest({"lesson": current_lesson, "status": current_status}),
            "retired",
            "New standards supersede this guidance.",
        )
        assert "IMPORT-1" not in agent.task_context("P2", "I-P2")["lesson_ids"]

        no_org_grant = RegistryService(
            store, Principal("reviewer-no-org", frozenset({"P1", "P2"}), "reviewer")
        )
        with pytest.raises(PermissionError):
            no_org_grant.promote_organizational_lesson(
                "ACME",
                "OL-2",
                "Should fail",
                [("P1", "L-P1")],
                observation="A",
                result="B",
                interpretation="C",
                validated_statement="D",
                applicability="A",
                limitations="B",
                review_due="2027-10-01",
                rationale="C",
                relevant_standards=[],
            )
    finally:
        store.close()
