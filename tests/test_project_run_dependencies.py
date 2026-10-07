"""Dependency graph and readiness behavior for ProjectRun."""

import pytest

from engineering_ai_system.project_run import ProjectRunService
from engineering_execution import ExecutionService
from tests.support.project_run_harness import (
    alignment_report,
    integrate,
    workflow_for,
)


def _service(run_fixture):
    _, agent, _ = run_fixture
    return ProjectRunService(
        agent,
        workflow_for(run_fixture),
        reviewer=alignment_report,
        integrator=integrate,
    )


def _task(task_id, *, depends_on=None):
    task = {
        "task_id": task_id,
        "tool": "compare_revision",
        "parameters": {
            "base_evidence_id": "evidence:EA",
            "head_evidence_id": "evidence:EB",
        },
    }
    if depends_on is not None:
        task["depends_on"] = depends_on
    return task


def test_changed_brief_stales_completed_task_and_blocks_dependent(run_fixture):
    from engineering_registry.extended_demo import synthetic_client_project_brief

    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "STALE-BRIEF",
        "issue:RUN",
        [_task("STALE-A"), _task("STALE-B", depends_on=["STALE-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.execute_task("DEMO", run["run_id"], "STALE-A")
    retained_task = service.brain.execution.get_task("DEMO", "STALE-A")
    assert (
        service.brain.execution.execute("DEMO", "STALE-A")["node_id"]
        == retained_task["result_id"]
    )
    assert service.get_schedule("DEMO", run["run_id"])["completed_task_ids"] == [
        "STALE-A"
    ]
    human.record_client_project_brief(
        "DEMO", "B2", synthetic_client_project_brief(["evidence:EA", "evidence:EB"])
    )
    with pytest.raises(ValueError, match="brief changed"):
        service.brain.execution.execute("DEMO", "STALE-A")
    schedule = service.get_schedule("DEMO", run["run_id"])
    assert schedule["completed_task_ids"] == []
    assert schedule["stale_task_ids"] == ["STALE-A"]
    assert schedule["blocked_task_ids"] == ["STALE-B"]


def test_project_run_accepts_and_freezes_dependency_edges(run_fixture):
    service = _service(run_fixture)
    run = service.propose(
        "DEMO",
        "DEPENDENCY-PLAN",
        "issue:RUN",
        [
            _task("DEP-A"),
            _task("DEP-B"),
            _task("DEP-C", depends_on=["DEP-A", "DEP-B"]),
        ],
    )

    dependencies = {
        item["task_id"]: item["depends_on"] for item in run["plan"]["tasks"]
    }
    assert dependencies == {
        "DEP-A": [],
        "DEP-B": [],
        "DEP-C": ["DEP-A", "DEP-B"],
    }


@pytest.mark.parametrize(
    ("tasks", "message"),
    [
        (
            [_task("DEP-A"), _task("DEP-B", depends_on=["MISSING"])],
            "unknown predecessor",
        ),
        ([_task("DEP-A", depends_on=["DEP-A"])], "depend on itself"),
        (
            [
                _task("DEP-A", depends_on=["DEP-B"]),
                _task("DEP-B", depends_on=["DEP-A"]),
            ],
            "cycle",
        ),
        ([_task("DEP-A", depends_on=["DEP-B", "DEP-B"]), _task("DEP-B")], "duplicate"),
    ],
)
def test_invalid_dependency_graph_fails_before_tasks_are_retained(
    run_fixture, tasks, message
):
    service = _service(run_fixture)
    _, agent, _ = run_fixture

    with pytest.raises(ValueError, match=message):
        service.propose("DEMO", "BAD-DEPENDENCY-PLAN", "issue:RUN", tasks)

    assert not agent.list_control_records("DEMO", "project_runs")
    assert not any(
        agent.get_control_record("DEMO", "tasks", task["task_id"]) for task in tasks
    )


def test_schedule_exposes_independent_ready_tasks_and_blocks_dependents(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-SCHEDULE",
        "issue:RUN",
        [
            _task("SCHED-A"),
            _task("SCHED-B"),
            _task("SCHED-C", depends_on=["SCHED-A"]),
        ],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )

    schedule = service.get_schedule("DEMO", run["run_id"])
    states = {item["task_id"]: item for item in schedule["tasks"]}

    assert schedule["ready_task_ids"] == ["SCHED-A", "SCHED-B"]
    assert schedule["blocked_task_ids"] == ["SCHED-C"]
    assert states["SCHED-C"]["blocking_task_ids"] == ["SCHED-A"]


def test_schedule_releases_dependent_after_current_predecessor_assessment(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-RELEASE",
        "issue:RUN",
        [_task("RELEASE-A"), _task("RELEASE-B", depends_on=["RELEASE-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )

    assert service.get_schedule("DEMO", run["run_id"])["ready_task_ids"] == [
        "RELEASE-A"
    ]
    result = service.execute_task("DEMO", run["run_id"], "RELEASE-A")
    assert result["result"]["node_type"] == "result"
    schedule = service.get_schedule("DEMO", run["run_id"])

    assert schedule["completed_task_ids"] == ["RELEASE-A"]
    assert schedule["ready_task_ids"] == ["RELEASE-B"]


def test_schedule_propagates_cancelled_predecessor_to_dependent(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-CANCELLED",
        "issue:RUN",
        [_task("CANCEL-A"), _task("CANCEL-B", depends_on=["CANCEL-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    ExecutionService(human).cancel("DEMO", "CANCEL-A", "Upstream task cancelled")

    schedule = service.get_schedule("DEMO", run["run_id"])
    states = {item["task_id"]: item for item in schedule["tasks"]}

    assert schedule["failed_task_ids"] == ["CANCEL-A"]
    assert schedule["blocked_task_ids"] == ["CANCEL-B"]
    assert states["CANCEL-B"]["blocking_task_ids"] == ["CANCEL-A"]


def test_schedule_does_not_complete_predecessor_without_current_assessment(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-NO-ASSESSMENT",
        "issue:RUN",
        [_task("NOASSESS-A"), _task("NOASSESS-B", depends_on=["NOASSESS-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.brain.execution.execute("DEMO", "NOASSESS-A")

    schedule = service.get_schedule("DEMO", run["run_id"])
    states = {item["task_id"]: item for item in schedule["tasks"]}

    assert schedule["stale_task_ids"] == ["NOASSESS-A"]
    assert schedule["blocked_task_ids"] == ["NOASSESS-B"]
    assert states["NOASSESS-A"]["block_reasons"] == [
        "current_result_or_assessment_missing"
    ]


def test_dependent_task_cannot_execute_before_its_predecessor(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-ENFORCE",
        "issue:RUN",
        [_task("ENFORCE-A"), _task("ENFORCE-B", depends_on=["ENFORCE-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )

    with pytest.raises(ValueError, match="dependency"):
        service.execute_task("DEMO", run["run_id"], "ENFORCE-B")

    assert service.brain.execution.get_task("DEMO", "ENFORCE-B")["state"] == "queued"


def test_dependent_execution_records_exact_upstream_result_and_assessment_digests(
    run_fixture,
):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-BIND",
        "issue:RUN",
        [_task("BIND-A"), _task("BIND-B", depends_on=["BIND-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    predecessor_outcome = service.execute_task("DEMO", run["run_id"], "BIND-A")
    downstream_outcome = service.execute_task("DEMO", run["run_id"], "BIND-B")
    bindings = agent.list_control_records("DEMO", "project_run_dependency_bindings")

    assert downstream_outcome["result"]["node_type"] == "result"
    assert len(bindings) == 1
    binding = bindings[0]
    upstream = binding["dependencies"][0]
    assert upstream["task_id"] == "BIND-A"
    assert upstream["result_id"] == predecessor_outcome["result"]["node_id"]
    assert upstream["result_digest"]
    assert upstream["assessment_id"] == predecessor_outcome["assessment"]["node_id"]
    assert upstream["assessment_digest"]


def test_direct_execution_cannot_bypass_missing_dependency_binding(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-BYPASS",
        "issue:RUN",
        [_task("BYPASS-A"), _task("BYPASS-B", depends_on=["BYPASS-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.execute_task("DEMO", run["run_id"], "BYPASS-A")

    with pytest.raises(ValueError, match="dependency binding"):
        service.brain.execution.execute("DEMO", "BYPASS-B")


def test_upstream_change_after_binding_blocks_dependent_execution(run_fixture):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-STALE-BINDING",
        "issue:RUN",
        [_task("STALE-A"), _task("STALE-B", depends_on=["STALE-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.execute_task("DEMO", run["run_id"], "STALE-A")
    binding = service.bind_task_dependencies("DEMO", run["run_id"], "STALE-B")
    upstream = agent.get_control_record("DEMO", "tasks", "STALE-A")
    upstream["result_id"] = "result:changed-after-binding"
    agent.store.put_record("DEMO", "tasks", "STALE-A", upstream, replace=True)

    with pytest.raises(ValueError, match="dependency.*stale|dependency result changed"):
        ExecutionService(human).execute("DEMO", "STALE-B")

    assert binding["dependencies"][0]["result_id"] != upstream["result_id"]


def test_idempotent_dependent_execution_rejects_changed_upstream(run_fixture):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-STALE-RESULT",
        "issue:RUN",
        [
            _task("STALE-RESULT-A"),
            _task("STALE-RESULT-B", depends_on=["STALE-RESULT-A"]),
        ],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.execute_task("DEMO", run["run_id"], "STALE-RESULT-A")
    downstream = service.execute_task("DEMO", run["run_id"], "STALE-RESULT-B")
    upstream = agent.get_control_record("DEMO", "tasks", "STALE-RESULT-A")
    upstream["result_id"] = "result:changed-after-downstream"
    agent.store.put_record("DEMO", "tasks", "STALE-RESULT-A", upstream, replace=True)

    with pytest.raises(ValueError, match="dependency.*stale|dependency result changed"):
        ExecutionService(human).execute("DEMO", "STALE-RESULT-B")

    assert downstream["result"]["node_type"] == "result"


def test_lead_and_gate2_receive_exact_dependency_provenance(run_fixture):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-INTEGRATION",
        "issue:RUN",
        [_task("INTEGRATE-A"), _task("INTEGRATE-B", depends_on=["INTEGRATE-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.execute_task("DEMO", run["run_id"], "INTEGRATE-A")
    service.execute_task("DEMO", run["run_id"], "INTEGRATE-B")
    integration = service.integrate("DEMO", run["run_id"])
    assessment = agent.get_control_record(
        "DEMO", "project_run_assessments", integration["assessment_id"]
    )
    schedule = service.get_schedule("DEMO", run["run_id"])
    gate2 = service.review("DEMO", run["run_id"], "outcome")
    dependent_outcome = next(
        item
        for item in integration["task_outcomes"]
        if item["task_id"] == "INTEGRATE-B"
    )

    assert dependent_outcome["dependency_binding"]["dependencies"][0]["task_id"] == (
        "INTEGRATE-A"
    )
    assert (
        dependent_outcome["provenance"]["dependency_binding_digest"]
        == (dependent_outcome["dependency_binding"]["binding_digest"])
    )
    assert integration["dependency_provenance"][0]["task_id"] == "INTEGRATE-B"
    assert assessment["dependency_provenance"] == integration["dependency_provenance"]
    assert gate2["integrated_assessment_id"] == integration["assessment_id"]
    assert gate2["integrated_assessment_digest"] == integration["assessment_digest"]
    assert schedule["completed_task_ids"] == ["INTEGRATE-A", "INTEGRATE-B"]


def test_changed_dependency_binding_blocks_lead_integration(run_fixture):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    run = service.propose(
        "DEMO",
        "DEPENDENCY-STALE-INTEGRATION",
        "issue:RUN",
        [_task("INTEG-STALE-A"), _task("INTEG-STALE-B", depends_on=["INTEG-STALE-A"])],
    )
    service.review("DEMO", run["run_id"], "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise task"
        )
    service.execute_task("DEMO", run["run_id"], "INTEG-STALE-A")
    service.execute_task("DEMO", run["run_id"], "INTEG-STALE-B")
    upstream = agent.get_control_record("DEMO", "tasks", "INTEG-STALE-A")
    upstream["result_id"] = "result:changed-before-integration"
    agent.store.put_record("DEMO", "tasks", "INTEG-STALE-A", upstream, replace=True)

    with pytest.raises(ValueError, match="dependency.*stale|dependency result changed"):
        service.integrate("DEMO", run["run_id"])
