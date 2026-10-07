import pytest

from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import GraphIntegrityError, SQLiteGraphStore


def services(tmp_path):
    store = SQLiteGraphStore(tmp_path / "registry.sqlite")
    agent = RegistryService(store, Principal("agent", frozenset({"P"})))
    human = RegistryService(store, Principal("human", frozenset({"P"}), "reviewer"))
    agent.register(
        GraphNode("E", "P", NodeType.EVIDENCE, "Source", {"text": "Verified source"})
    )
    agent.propose_issue("P", "I", "Check", ["E"])
    return store, agent, human


def review(agent, human, status):
    p = agent.propose_transition("P", "I", status, "Review", ["E"])
    return human.review_proposal(
        "P", p["proposal_id"], p["proposal_digest"], accept=True, rationale="Reviewed"
    )


def test_agent_cannot_forge_lesson_or_verification(tmp_path):
    store, agent, _ = services(tmp_path)
    try:
        for kind in (
            NodeType.LESSON,
            NodeType.VERIFICATION,
            NodeType.DECISION,
            NodeType.TASK,
        ):
            with pytest.raises((ValueError, PermissionError)):
                agent.register(
                    GraphNode("forged", "P", kind, "Approved", {"human_approved": True})
                )
        assert store.get_node("P", "forged") is None
    finally:
        store.close()


def test_closure_is_digest_bound_human_decision_and_idempotent(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        review(agent, human, "open")
        review(agent, human, "accepted")
        p = agent.propose_transition("P", "I", "closed", "Close", ["E"])
        with pytest.raises(PermissionError):
            agent.review_proposal(
                "P",
                p["proposal_id"],
                p["proposal_digest"],
                accept=True,
                rationale="Fake",
            )
        result = human.review_proposal(
            "P",
            p["proposal_id"],
            p["proposal_digest"],
            accept=True,
            rationale="Evidence checked",
        )
        repeated = human.review_proposal(
            "P",
            p["proposal_id"],
            p["proposal_digest"],
            accept=True,
            rationale="Evidence checked",
        )
        assert repeated == result
        assert store.get_issue("P", "I").status.value == "closed"
        assert len(store.list_lifecycle_events("P", "I")) == 3
        decisions = human.list_records("P", "decision")
        assert len(decisions) == 3
        assert decisions[-1]["attributes"].get("reviewed_at")
    finally:
        store.close()


def test_unreasoned_closure_is_not_verified_decision_evidence_or_learning(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        review(agent, human, "open")
        review(agent, human, "accepted")
        proposal = agent.propose_transition("P", "I", "closed", "Close", ["E"])
        human.review_proposal(
            "P",
            proposal["proposal_id"],
            proposal["proposal_digest"],
            accept=True,
            rationale="",
        )
        decisions = human.list_records("P", "decision")
        closure = next(
            decision
            for decision in decisions
            if decision["attributes"]["reasoning_evidence_status"]
            == "no_human_reasoning"
        )
        assert store.get_issue("P", "I").status.value == "closed"
        assert (
            closure["attributes"]["reasoning_evidence_status"] == "no_human_reasoning"
        )
        assert closure["attributes"]["reasoning_evidence_ids"] == []
        assert store.get_edges("P", closure["node_id"]) == []

        lesson = human.promote_lesson("P", "L", "Lesson", ["I"], ["E"], "Retain basis")
        assert closure["node_id"] not in lesson["attributes"]["source_decision_ids"]
        assert closure["node_id"] in lesson["attributes"]["excluded_decision_ids"]
        assert closure["node_id"] not in {
            record["node_id"]
            for context in lesson["attributes"]["source_snapshot"]["issues"]
            for record in context["linked_records"]
        }
    finally:
        store.close()


def test_reasoned_human_decision_is_linked_as_verified_decision_evidence(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        proposal = agent.propose_transition("P", "I", "open", "Open", ["E"])
        human.review_proposal(
            "P",
            proposal["proposal_id"],
            proposal["proposal_digest"],
            accept=True,
            rationale="Reviewed the cited source and confirmed the issue should be opened.",
        )
        decision = human.list_records("P", "decision")[0]
        assert (
            decision["attributes"]["reasoning_evidence_status"]
            == "verified_human_reasoning"
        )
        assert decision["attributes"]["reasoning_evidence_ids"] == ["E"]
        assert {
            edge.target_node_id for edge in store.get_edges("P", decision["node_id"])
        } == {"E"}
    finally:
        store.close()


def test_document_ai_can_record_evidence_linked_task_decision_for_future_context(
    tmp_path,
):
    store, agent, _ = services(tmp_path)
    try:
        decision = agent.record_task_decision(
            "P",
            "I",
            "decision:task:spacing",
            "Retain pile spacing while assessing two diameter alternatives.",
            "The topside arrangement is confirmed and changing spacing would affect its interface.",
            ["E"],
            alternatives=["Change pile spacing"],
            assumptions=["The topside arrangement remains frozen."],
            impact="Constrains the alternatives assessed by the task.",
            decision_level="D3",
        )

        assert decision["node_type"] == NodeType.DECISION
        assert decision["attributes"]["decision_type"] == "task"
        assert decision["attributes"]["authority"] == "ai_lead"
        assert decision["attributes"]["status"] == "proposed"
        assert decision["attributes"]["human_acceptance_required"] is True
        assert decision["attributes"]["evidence_ids"] == ["E"]
        assert {edge.target_node_id for edge in store.get_edges("P", "I")} >= {
            "decision:task:spacing"
        }

        context = agent.task_context("P", "I")
        assert "decision:task:spacing" in {
            record["node_id"] for record in context["records"]
        }
        assert "decision:task:spacing" in {
            record["node_id"] for record in context["execution_snapshot"]["records"]
        }
    finally:
        store.close()


def test_task_decision_rejects_invalid_authority_and_foreign_evidence(tmp_path):
    store, agent, _ = services(tmp_path)
    try:
        with pytest.raises(ValueError, match="AI lead"):
            agent.record_task_decision(
                "P",
                "I",
                "D1",
                "Retain layout",
                "Interface basis",
                ["E"],
                authority="human_engineer",
            )
        with pytest.raises(GraphIntegrityError):
            agent.record_task_decision(
                "P", "I", "D2", "Retain layout", "Interface basis", ["FOREIGN"]
            )
        assert store.get_node("P", "D1") is None
        assert store.get_node("P", "D2") is None
    finally:
        store.close()


def test_task_decision_is_not_writable_by_reviewer_or_alignment_reviewer(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        alignment_reviewer = RegistryService(
            store, Principal("reviewer-ai", frozenset({"P"}), "ai_reviewer")
        )
        for service in (human, alignment_reviewer):
            with pytest.raises(PermissionError, match="AI lead route"):
                service.record_task_decision(
                    "P", "I", "D1", "Retain layout", "Interface basis", ["E"]
                )
        assert store.get_node("P", "D1") is None
    finally:
        store.close()


def test_stale_issue_proposal_rolls_back(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        stale = agent.propose_transition("P", "I", "open", "First")
        review(agent, human, "open")
        with pytest.raises(ValueError, match="changed"):
            human.review_proposal(
                "P",
                stale["proposal_id"],
                stale["proposal_digest"],
                accept=True,
                rationale="Late",
            )
        assert store.get_issue("P", "I").status.value == "open"
        assert len(store.list_lifecycle_events("P", "I")) == 1
    finally:
        store.close()


def test_backup_cannot_include_ungranted_projects(tmp_path):
    store, _, human = services(tmp_path)
    try:
        store.add_node(GraphNode("private", "OTHER", NodeType.PROJECT, "Private"))
        with pytest.raises(PermissionError):
            human.backup(tmp_path / "backup.sqlite")
        assert not (tmp_path / "backup.sqlite").exists()
    finally:
        store.close()


def test_project_denied_and_reader_cannot_write(tmp_path):
    store, agent, _ = services(tmp_path)
    try:
        with pytest.raises(PermissionError):
            agent.list_records("OTHER")
        reader = RegistryService(store, Principal("reader", frozenset({"P"}), "reader"))
        with pytest.raises(PermissionError):
            reader.register(GraphNode("N", "P", NodeType.EVIDENCE, "New"))
    finally:
        store.close()


def test_immutable_registered_evidence_conflicts(tmp_path):
    store, agent, _ = services(tmp_path)
    try:
        with pytest.raises(GraphIntegrityError):
            agent.register(
                GraphNode("E", "P", NodeType.EVIDENCE, "Source", {"text": "Changed"})
            )
        assert store.get_node("P", "E").attributes["text"] == "Verified source"
    finally:
        store.close()


def test_verification_and_lesson_are_linked_attributed_and_repeatable(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        result = agent.record_result(
            "P", "R", "I", "Check", {}, "traceability", "1", {"ok": True}, ["E"]
        )
        with pytest.raises(PermissionError):
            agent.verify_result("P", "R", digest(result), ["E"], "Checked")
        verified = human.verify_result("P", "R", digest(result), ["E"], "Checked")
        assert (
            human.verify_result("P", "R", digest(result), ["E"], "Checked") == verified
        )
        assert verified["attributes"]["reviewed_by"] == "human"
        assert {
            e.target_node_id for e in store.get_edges("P", verified["node_id"])
        } == {"R", "E"}
        lesson = human.promote_lesson(
            "P", "L", "Lesson", ["I"], ["E"], "Reviewed source"
        )
        assert {e.target_node_id for e in store.get_edges("P", "L")} == {
            "I",
            "E",
            "R",
            verified["node_id"],
        }
        assert lesson["attributes"]["approved_by"] == "human"
        assert (
            len([e for e in human.history("P", "I") if e["kind"] == "result_verified"])
            == 1
        )
    finally:
        store.close()


def test_closure_with_execution_results_requires_recorded_verification(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        review(agent, human, "open")
        review(agent, human, "accepted")
        result = agent.record_result(
            "P", "R", "I", "Check", {}, "traceability", "1", {"ok": True}, ["E"]
        )
        proposal = agent.propose_transition("P", "I", "closed", "Close", ["E"])
        with pytest.raises(ValueError, match="verification"):
            human.review_proposal(
                "P",
                proposal["proposal_id"],
                proposal["proposal_digest"],
                accept=True,
                rationale="Close",
            )
        assert store.get_issue("P", "I").status.value == "accepted"
        human.verify_result("P", "R", digest(result), ["E"], "Results checked")
        review(agent, human, "closed")
        assert store.get_issue("P", "I").status.value == "closed"
    finally:
        store.close()


def test_learning_retains_final_result_decisions_and_snapshot_after_reopen(tmp_path):
    store, agent, human = services(tmp_path)
    try:
        review(agent, human, "open")
        review(agent, human, "accepted")
        result = agent.record_result(
            "P",
            "R",
            "I",
            "Final check",
            {"load": 20},
            "document_check",
            "1",
            {"conclusion": "Source wording checked; engineering adequacy unknown"},
            ["E"],
        )
        verification = human.verify_result(
            "P", "R", digest(result), ["E"], "Checked against the cited source"
        )
        review(agent, human, "closed")
        decision_ids = sorted(n["node_id"] for n in human.list_records("P", "decision"))
        agent.propose_issue("P", "OTHER", "Unrelated issue", ["E"])
        agent.record_result(
            "P", "UNRELATED", "OTHER", "Other check", {}, "check", "1", {}, ["E"]
        )
        with pytest.raises(PermissionError):
            agent.promote_lesson(
                "P", "L", "Retain the source basis", ["I"], ["E"], "Reviewed"
            )
        lesson = human.promote_lesson(
            "P", "L", "Retain the source basis", ["I"], ["E"], "Reviewed"
        )
        attrs = lesson["attributes"]
        assert attrs["source_result_ids"] == ["R"]
        assert attrs["source_decision_ids"] == decision_ids
        assert attrs["source_verification_ids"] == [verification["node_id"]]
        snapshot = attrs["source_snapshot"]
        assert snapshot["issues"][0]["issue"]["status"] == "closed"
        assert snapshot["issues"][0]["results"] == [result]
        assert snapshot["verifications"] == [verification]
        assert attrs["snapshot_digest"] == digest(snapshot)
        assert {e.target_node_id for e in store.get_edges("P", "L")} == {
            "I",
            "E",
            "R",
            verification["node_id"],
            *decision_ids,
        }
        # Later lifecycle changes must not rewrite the approved learning basis.
        review(agent, human, "open")
    finally:
        store.close()
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as reopened:
        retained = reopened.get_node("P", "L")
        assert retained.attributes == attrs
        assert (
            retained.attributes["source_snapshot"]["issues"][0]["issue"]["status"]
            == "closed"
        )
        assert reopened.get_issue("P", "I").status.value == "open"
