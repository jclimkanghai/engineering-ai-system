import importlib

import pytest

from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


def setup(tmp_path):
    assert importlib.util.find_spec("engineering_execution.service") is not None
    cls = importlib.import_module("engineering_execution.service").ExecutionService
    store = SQLiteGraphStore(tmp_path / "memory.sqlite")
    agent = RegistryService(store, Principal("worker", frozenset({"P"})))
    human = RegistryService(store, Principal("engineer", frozenset({"P"}), "reviewer"))
    for eid, revision, wording in [
        ("A", "A", "Load: 10 kN\nInspect weekly"),
        ("B", "B", "Load: 20 kN\nInspect weekly"),
    ]:
        agent.register(
            GraphNode(
                eid,
                "P",
                NodeType.EVIDENCE,
                revision,
                {"text": wording, "revision": revision, "page": 1},
            )
        )
    agent.propose_issue("P", "I", "Load change", ["A", "B"])
    return store, agent, human, cls(agent), cls(human)


def proposed(worker, task_id="T", tool="compare_revision", parameters=None):
    return worker.propose_task(
        "P",
        task_id,
        "I",
        tool,
        parameters
        if parameters is not None
        else {"base_evidence_id": "A", "head_evidence_id": "B"},
    )


def authorized(worker, reviewer, task_id="T", tool="compare_revision", parameters=None):
    p = proposed(worker, task_id, tool, parameters)
    reviewer.authorize_task("P", task_id, p["task_digest"], "Scope checked")
    return p


def test_unapproved_and_agent_self_approved_tasks_cannot_run(tmp_path):
    store, _, _, worker, reviewer = setup(tmp_path)
    try:
        p = proposed(worker)
        with pytest.raises(PermissionError):
            worker.authorize_task("P", "T", p["task_digest"], "Fake approval")
        with pytest.raises(ValueError, match="authoris"):
            worker.execute("P", "T")
        assert worker.get_task("P", "T")["state"] == "proposed"
        assert store.list_nodes("P", NodeType.RESULT) == []
    finally:
        store.close()


def test_authorized_compare_runs_once_and_preserves_provenance_on_reopen(tmp_path):
    store, _, _, worker, reviewer = setup(tmp_path)
    authorized(worker, reviewer)
    first = worker.execute("P", "T")
    assert first["attributes"]["outputs"]["removed_lines"] == ["Load: 10 kN"]
    assert first["attributes"]["outputs"]["added_lines"] == ["Load: 20 kN"]
    assert first["attributes"]["verification_state"] == "unverified"
    assert worker.execute("P", "T") == first
    assert len(store.list_nodes("P", NodeType.RESULT)) == 1
    task = worker.get_task("P", "T")
    assert task["authorization"]["actor_id"] == "engineer"
    assert len(task["attempts"]) == 1
    store.close()
    with SQLiteGraphStore(tmp_path / "memory.sqlite") as reopened:
        assert reopened.get_node("P", first["node_id"]).attributes["evidence_ids"] == [
            "A",
            "B",
        ]
        assert reopened.get_issue("P", "I").status.value == "proposed"


def test_stale_input_and_changed_task_parameters_rejected(tmp_path):
    store, agent, human, worker, reviewer = setup(tmp_path)
    try:
        authorized(worker, reviewer)
        with pytest.raises(ValueError, match="different"):
            proposed(
                worker, parameters={"base_evidence_id": "B", "head_evidence_id": "A"}
            )
        p = agent.propose_transition("P", "I", "open", "Review")
        human.review_proposal(
            "P",
            p["proposal_id"],
            p["proposal_digest"],
            accept=True,
            rationale="Checked",
        )
        with pytest.raises(ValueError, match="changed"):
            worker.execute("P", "T")
        assert store.list_nodes("P", NodeType.RESULT) == []
    finally:
        store.close()


def test_failure_explicit_retry_cancel_and_recovery_are_durable(tmp_path, monkeypatch):
    store, agent, _, worker, reviewer = setup(tmp_path)
    try:
        authorized(worker, reviewer)
        original = worker._run_tool
        monkeypatch.setattr(
            worker,
            "_run_tool",
            lambda *_: (_ for _ in ()).throw(RuntimeError("offline failure")),
        )
        assert worker.execute("P", "T")["state"] == "failed"
        assert "offline failure" in worker.get_task("P", "T")["attempts"][0]["error"]
        monkeypatch.setattr(worker, "_run_tool", original)
        with pytest.raises(ValueError):
            worker.execute("P", "T")
        reviewer.retry("P", "T", "Failure investigated")
        assert worker.execute("P", "T")["node_type"] == "result"
        assert len(worker.get_task("P", "T")["attempts"]) == 2
        authorized(worker, reviewer, "C")
        worker.cancel("P", "C", "No longer needed")
        with pytest.raises(ValueError):
            worker.execute("P", "C")
        authorized(worker, reviewer, "R")
        task = worker.get_task("P", "R")
        task["state"] = "running"
        task["attempts"] = [{"started_at": "test-crash", "state": "running"}]
        store.put_record("P", "tasks", "R", task, replace=True)
        with pytest.raises(PermissionError):
            worker.recover("P", "R", "Crash")
        reviewer.recover("P", "R", "Crash confirmed")
        assert worker.get_task("P", "R")["state"] == "failed"
        assert any(e["kind"] == "task_recovered" for e in agent.history("P", "R"))
    finally:
        store.close()


def test_traceability_review_pack_and_tool_boundaries(tmp_path):
    store, _, _, worker, reviewer = setup(tmp_path)
    try:
        authorized(worker, reviewer, "trace", "validate_traceability", {})
        trace = worker.execute("P", "trace")["attributes"]["outputs"]
        assert trace["evidence_count"] == 2
        assert trace["missing_locators"] == []
        assert "UNKNOWN / INSUFFICIENT INFORMATION" in trace["requirement_status"]
        authorized(worker, reviewer, "pack", "generate_review_pack", {})
        pack = worker.execute("P", "pack")["attributes"]["outputs"]
        assert pack["issue"]["issue_id"] == "I"
        assert pack["human_review_required"] is True
        with pytest.raises(ValueError):
            proposed(worker, "shell", "shell", {"command": "whoami"})
        with pytest.raises(ValueError):
            proposed(worker, "path", "generate_review_pack", {"path": "/tmp/arbitrary"})
        with pytest.raises(PermissionError):
            worker.get_task("OTHER", "T")
    finally:
        store.close()


def test_interrupted_worker_cannot_finish_a_newer_execution_attempt(
    tmp_path, monkeypatch
):
    store, _, _, worker, reviewer = setup(tmp_path)
    try:
        authorized(worker, reviewer)
        original = worker._run_tool

        def resumed_while_old_worker_returns(task):
            reviewer.recover("P", "T", "First process interrupted")
            reviewer.retry("P", "T", "Resume reviewed inputs")
            replacement = worker.get_task("P", "T")
            replacement["state"] = "running"
            replacement["attempts"].append(
                {
                    "attempt_id": "replacement-worker",
                    "state": "running",
                    "started_at": "fixture",
                }
            )
            store.put_record("P", "tasks", "T", replacement, replace=True)
            return original(task)

        monkeypatch.setattr(worker, "_run_tool", resumed_while_old_worker_returns)
        worker.execute("P", "T")
        assert worker.get_task("P", "T")["state"] == "running"
        assert store.list_nodes("P", NodeType.RESULT) == []
        assert (
            worker.get_task("P", "T")["attempts"][1]["attempt_id"]
            == "replacement-worker"
        )
    finally:
        store.close()
