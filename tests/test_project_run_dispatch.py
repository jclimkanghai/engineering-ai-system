from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from engineering_ai_system import EngineeringAISystem
from engineering_ai_system.project_run import ProjectRunService
from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from tests.support.project_run_harness import alignment_report


def _task(task_id, specialist_id=None, *, depends_on=None):
    return {
        "task_id": task_id,
        "tool": "compare_revision",
        "parameters": {
            "base_evidence_id": "evidence:EA",
            "head_evidence_id": "evidence:EB",
        },
        **(
            {
                "specialist_assignment": {
                    "specialist_id": specialist_id,
                    "discipline": "structural",
                    "skill": "structural-review",
                    "task_scope": "Review the specified source comparison.",
                    "deliverable": "Cited specialist proposal and bounded tool result.",
                }
            }
            if specialist_id
            else {}
        ),
        **({"depends_on": depends_on} if depends_on else {}),
    }


def _system(run_fixture, analysis, specialist_skills_dir=None):
    _, agent, _ = run_fixture
    alignment_report.deterministic_test_adapter = True
    alignment_report.supports_concurrent_calls = True
    analysis.deterministic_test_adapter = True
    analysis.supports_concurrent_calls = True
    brain = DocumentAIWorkflow(agent, alignment_reviewer=alignment_report)
    return EngineeringAISystem(
        agent,
        brain,
        mode="TEST",
        specialist_analysis=analysis,
        specialist_skills_dir=specialist_skills_dir,
    )


def _propose_and_authorize(system, run_fixture, run_id, tasks):
    _, agent, human = run_fixture
    run = system.project_runs.propose("DEMO", run_id, "issue:RUN", tasks)
    system.project_runs.review("DEMO", run_id, "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise specialist task"
        )
    return run


def test_dispatch_runs_independent_specialists_concurrently_and_retains_proposals(
    run_fixture,
):
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    peak = 0

    def analysis(mode, context):
        nonlocal active, peak
        assert mode == "discipline_specialist"
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=3)
        with lock:
            active -= 1
        evidence_ids = [
            item["node_id"]
            for item in context["source_snapshot"]["records"]
            if item["node_type"] == "evidence"
        ]
        return {
            "summary": "Reviewed the bounded task sources.",
            "recommendation": "Use the retained comparison for Lead assessment.",
            "evidence_ids": evidence_ids,
            "assumptions": [],
            "unknowns": [],
        }

    system = _system(run_fixture, analysis)
    _propose_and_authorize(
        system,
        run_fixture,
        "DISPATCH-INDEPENDENT",
        [_task("WORKER-A", "specialist-a"), _task("WORKER-B", "specialist-b")],
    )

    result = system.dispatch_ready_tasks("DEMO", "DISPATCH-INDEPENDENT", max_workers=2)

    assert peak == 2
    assert result["batches"][0]["task_ids"] == ["WORKER-A", "WORKER-B"]
    assert result["schedule"]["completed_task_ids"] == ["WORKER-A", "WORKER-B"]
    outputs = system.registry.list_control_records(
        "DEMO", "project_run_specialist_outputs"
    )
    assert {item["task_id"] for item in outputs} == {"WORKER-A", "WORKER-B"}
    status_outputs = system.project_runs.get_status("DEMO", "DISPATCH-INDEPENDENT")[
        "specialist_outputs"
    ]
    assert {item["task_id"] for item in status_outputs} == {
        "WORKER-A",
        "WORKER-B",
    }
    first_output = system.project_runs.get_specialist_output(
        "DEMO", status_outputs[0]["output_id"]
    )
    assert first_output["record_digest"]
    assert first_output["proposal"]["evidence_ids"]


def test_failed_specialist_does_not_cancel_independent_ready_task(run_fixture):
    def analysis(mode, context):
        if context["task_id"] == "WORKER-FAIL":
            raise RuntimeError("specialist provider unavailable")
        return {
            "summary": "Reviewed the bounded task sources.",
            "recommendation": "Use the retained comparison for Lead assessment.",
            "evidence_ids": [
                item["node_id"]
                for item in context["source_snapshot"]["records"]
                if item["node_type"] == "evidence"
            ],
            "assumptions": [],
            "unknowns": [],
        }

    system = _system(run_fixture, analysis)
    _propose_and_authorize(
        system,
        run_fixture,
        "DISPATCH-FAILURE",
        [_task("WORKER-FAIL", "specialist-fail"), _task("WORKER-OK", "specialist-ok")],
    )

    result = system.dispatch_ready_tasks("DEMO", "DISPATCH-FAILURE", max_workers=2)

    outcomes = {
        item["task_id"]: item
        for batch in result["batches"]
        for item in batch["outcomes"]
    }
    assert outcomes["WORKER-FAIL"]["state"] == "failed"
    assert "specialist provider unavailable" in outcomes["WORKER-FAIL"]["error"]
    assert outcomes["WORKER-OK"]["state"] == "succeeded"
    assert outcomes["WORKER-OK"]["result_id"]
    assert result["schedule"]["completed_task_ids"] == ["WORKER-OK"]


def test_dispatch_releases_dependent_task_only_after_assessed_predecessor(run_fixture):
    calls = []

    def analysis(mode, context):
        calls.append(context["task_id"])
        return {
            "summary": "Reviewed the bounded task sources.",
            "recommendation": "Use the retained comparison for Lead assessment.",
            "evidence_ids": [
                item["node_id"]
                for item in context["source_snapshot"]["records"]
                if item["node_type"] == "evidence"
            ],
            "assumptions": [],
            "unknowns": [],
        }

    system = _system(run_fixture, analysis)
    _propose_and_authorize(
        system,
        run_fixture,
        "DISPATCH-DEPENDENCY",
        [
            _task("WORKER-PREDECESSOR", "specialist-a"),
            _task("WORKER-DEPENDENT", depends_on=["WORKER-PREDECESSOR"]),
        ],
    )

    result = system.dispatch_ready_tasks("DEMO", "DISPATCH-DEPENDENCY", max_workers=2)

    assert [batch["task_ids"] for batch in result["batches"]] == [
        ["WORKER-PREDECESSOR"],
        ["WORKER-DEPENDENT"],
    ]
    assert calls == ["WORKER-PREDECESSOR"]
    assert result["schedule"]["completed_task_ids"] == [
        "WORKER-DEPENDENT",
        "WORKER-PREDECESSOR",
    ]


def test_dispatch_requires_file_backed_store_for_parallel_workers(run_fixture):
    _, agent, _ = run_fixture
    system = object.__new__(EngineeringAISystem)
    system.registry = agent
    system.project_runs = ProjectRunService(agent, DocumentAIWorkflow(agent))
    system._dispatch_lock = threading.Lock()
    original_path = agent.store.path
    agent.store.path = ":memory:"

    try:
        try:
            system.dispatch_ready_tasks("DEMO", "missing", max_workers=2)
        except ValueError as exc:
            assert "file-backed Registry" in str(exc)
        else:
            raise AssertionError("parallel in-memory dispatch should be rejected")
    finally:
        agent.store.path = original_path


def test_parallel_dispatch_requires_concurrency_capable_adapters(run_fixture):
    _, agent, _ = run_fixture

    def reviewer(_context):
        return {}

    reviewer.deterministic_test_adapter = True
    brain = DocumentAIWorkflow(agent, alignment_reviewer=reviewer)
    brain.analysis = lambda _phase, _context: {}
    brain.analysis.deterministic_test_adapter = True
    system = EngineeringAISystem(agent, brain, mode="TEST")

    try:
        system.dispatch_ready_tasks("DEMO", "missing", max_workers=2)
    except ValueError as exc:
        assert "concurrency-capable" in str(exc)
    else:
        raise AssertionError("parallel dispatch should reject unmarked adapters")


def test_reasoning_workflow_opens_registry_connection_inside_worker_thread(
    run_fixture,
):
    from pipelines.llm.models import LLMResponse
    from pipelines.review.execution_workflow import build_reasoning_workflow

    _, agent, _ = run_fixture

    class AnalysisAdapter:
        supports_concurrent_calls = True

        def __init__(self):
            self.request = None

        def analyse(self, request):
            self.request = request
            return LLMResponse(
                model="offline",
                response_id="worker-local-registry",
                findings=[],
            )

    adapter = AnalysisAdapter()
    brain = build_reasoning_workflow(agent, analysis_adapter=adapter)
    records = []
    for node_id in ("requirement:R-LOAD", "evidence:EB"):
        node = agent.store.get_node("DEMO", node_id)
        records.append(
            {
                "node_id": node.node_id,
                "node_type": node.node_type.value,
                "attributes": node.attributes,
            }
        )
    context = {
        "project_id": "DEMO",
        "source_snapshot": {"records": records},
        "approved_lessons": [],
    }

    error = []

    def invoke_from_worker():
        try:
            brain.analysis("execution_planning", context)
        except Exception as exc:  # surfaced in the parent thread below
            error.append(exc)

    worker = threading.Thread(target=invoke_from_worker)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert error == []
    assert adapter.request.requirements[0]["source_evidence_ids"] == ["evidence:EB"]


def test_specialist_skill_digest_is_frozen_before_task_authorization(
    run_fixture, tmp_path
):
    bundled = Path(__file__).resolve().parents[1] / "skills" / "structural-review"
    skill_root = tmp_path / "skills"
    shutil.copytree(bundled, skill_root / "structural-review")
    def analysis(_mode, _context):
        return {
            "summary": "Reviewed the assigned evidence.",
            "recommendation": "Request discipline-owner verification.",
            "evidence_ids": ["evidence:EA", "evidence:EB"],
            "assumptions": [],
            "unknowns": [],
        }
    analysis.deterministic_test_adapter = True
    analysis.supports_concurrent_calls = True
    system = _system(run_fixture, analysis, specialist_skills_dir=skill_root)
    run = _propose_and_authorize(
        system,
        run_fixture,
        "SKILL-DIGEST-FROZEN",
        [_task("SKILL-FROZEN-TASK", "structural-agent")],
    )
    bound = run["plan"]["tasks"][0]["specialist_assignment"]
    assert bound["skill_digest"]
    assert bound["validation_status"] == "draft"

    skill_file = skill_root / "structural-review" / "SKILL.md"
    skill_file.write_text(skill_file.read_text() + "\nChanged after plan review.\n")

    with pytest.raises(ValueError, match="skill changed after the plan"):
        system.project_runs.execute_task("DEMO", run["run_id"], "SKILL-FROZEN-TASK")

    task = system.execution.get_task("DEMO", "SKILL-FROZEN-TASK")
    assert task["state"] == "queued"
    assert task["attempts"] == []
