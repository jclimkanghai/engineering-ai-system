"""Governed execution classes and human-approved Routine policy."""

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

from engineering_ai_system import EngineeringAISystem
from engineering_ai_system.project_run import ProjectRunService
from engineering_execution import ExecutionService
from engineering_registry.models import EngineeringIssue, GraphNode, NodeType
from tests.support.delegation_harness import mandate_definition
from tests.support.project_run_harness import alignment_report, workflow_for
from tests.support.project_run_harness import integrate as _integrate


def classify_run(*args):
    return import_module("engineering_ai_system.run_classification").classify_run(*args)


@pytest.fixture
def services(configured):
    return configured


@pytest.fixture
def routine_issue(services):
    _, agent, human = services
    agent.store.save_issue(
        EngineeringIssue(
            "issue:ROUTINE",
            "DEMO",
            "Text comparison",
            risk_level="low",
            confidence="high",
            human_review_required=True,
            evidence_ids=["evidence:EA", "evidence:EB"],
            requirement_ids=["requirement:R-LOAD"],
        )
    )
    human.record_client_mandate(
        "DEMO", "M-ROUTINE", "issue:ROUTINE", mandate_definition()
    )
    return "issue:ROUTINE"


def policy_definition():
    return {
        "allowed_tasks": [
            {
                "tool": "compare_revision",
                "parameters": {
                    "base_evidence_id": "evidence:EA",
                    "head_evidence_id": "evidence:EB",
                },
            }
        ],
        "max_risk_level": "low",
        "max_importance_level": "low",
        "require_evidence": True,
        "expires_on": (datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
        "rationale": "Permit bounded reversible text comparison.",
    }


def task_spec():
    return {
        "task_id": "ROUTINE-T1",
        "tool": "compare_revision",
        "parameters": {
            "base_evidence_id": "evidence:EA",
            "head_evidence_id": "evidence:EB",
        },
    }


def test_human_policy_binds_low_risk_routine_class(services, routine_issue):
    _, agent, human = services
    with pytest.raises(PermissionError):
        agent.approve_execution_policy("DEMO", "RP1", policy_definition())
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
    result = classify_run(agent, "DEMO", routine_issue, "routine", [task_spec()], "RP1")
    assert result["execution_class"] == "routine"
    assert result["policy_digest"] == policy["policy_digest"]
    assert result["status"] == "ready"


def test_routine_policy_accepts_allowlisted_tool_without_parameters(services):
    _, _, human = services
    definition = policy_definition()
    definition["allowed_tasks"] = [{"tool": "validate_traceability", "parameters": {}}]
    policy = human.approve_execution_policy("DEMO", "EMPTY-PARAMS", definition)
    from engineering_registry.execution_policy import require_policy_tasks

    require_policy_tasks(policy, [{"tool": "validate_traceability", "parameters": {}}])


def test_unknown_issue_risk_escalates_and_cannot_use_routine(services):
    _, agent, human = services
    human.approve_execution_policy("DEMO", "RP1", policy_definition())
    result = classify_run(
        agent, "DEMO", "issue:F-LOAD", "routine", [task_spec()], "RP1"
    )
    assert result["execution_class"] == "critical"
    assert result["status"] == "hold"


def test_routine_requires_current_matching_policy(services, routine_issue):
    _, agent, human = services
    with pytest.raises(ValueError, match="policy"):
        classify_run(agent, "DEMO", routine_issue, "routine", [task_spec()], "missing")
    human.approve_execution_policy("DEMO", "RP1", policy_definition())
    mismatch = task_spec()
    mismatch["parameters"]["head_evidence_id"] = "evidence:EA"
    with pytest.raises(ValueError, match="policy"):
        classify_run(agent, "DEMO", routine_issue, "routine", [mismatch], "RP1")
    human.revoke_execution_policy("DEMO", "RP1", "Stop routine use")
    with pytest.raises(ValueError, match="policy"):
        classify_run(agent, "DEMO", routine_issue, "routine", [task_spec()], "RP1")


def test_expired_policy_and_external_solver_are_not_routine(services, routine_issue):
    _, agent, human = services
    definition = policy_definition()
    definition["expires_on"] = (
        datetime.now(UTC).date() - timedelta(days=1)
    ).isoformat()
    with pytest.raises(ValueError, match="future"):
        human.approve_execution_policy("DEMO", "EXPIRED", definition)
    human.approve_execution_policy("DEMO", "RP1", policy_definition())
    solver = {"task_id": "SOLVER", "tool": "external_solver", "parameters": {}}
    classified = classify_run(agent, "DEMO", routine_issue, "routine", [solver], "RP1")
    assert classified["execution_class"] == "critical"
    assert classified["status"] == "hold"


def test_explicit_critical_class_is_never_downgraded(services, routine_issue):
    _, agent, _ = services
    result = classify_run(agent, "DEMO", routine_issue, "critical", [task_spec()])
    assert result["execution_class"] == "critical"


def test_linked_uncertain_requirement_blocks_routine(services):
    _, agent, human = services
    agent.register(
        GraphNode(
            "requirement:UNCERTAIN",
            "DEMO",
            NodeType.REQUIREMENT,
            "Governing load uncertain",
            {"uncertainties": ["Current load basis unresolved"]},
        )
    )
    agent.store.save_issue(
        EngineeringIssue(
            "issue:LOW-UNCERTAIN",
            "DEMO",
            "Simple source check",
            risk_level="low",
            confidence="high",
            human_review_required=True,
            evidence_ids=["evidence:EA", "evidence:EB"],
            requirement_ids=["requirement:UNCERTAIN"],
        )
    )
    human.record_client_mandate(
        "DEMO", "M-UNCERTAIN", "issue:LOW-UNCERTAIN", mandate_definition()
    )
    human.approve_execution_policy("DEMO", "RP1", policy_definition())
    classified = classify_run(
        agent, "DEMO", "issue:LOW-UNCERTAIN", "routine", [task_spec()], "RP1"
    )
    assert classified["execution_class"] == "critical"
    assert classified["status"] == "hold"


@pytest.mark.parametrize("level,expected", [("D0", "engineering"), ("D4", "critical")])
def test_planned_lead_decision_escalates_routine(
    services, routine_issue, level, expected
):
    _, agent, human = services
    human.approve_execution_policy("DEMO", "RP1", policy_definition())
    classified = classify_run(
        agent,
        "DEMO",
        routine_issue,
        "routine",
        [task_spec()],
        "RP1",
        [{"analysis": {"task_decisions": [{"decision_level": level}]}}],
    )
    assert classified["execution_class"] == expected


def test_routine_run_uses_policy_without_reviewer_gates(services, routine_issue):
    _, agent, human = services
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())

    def reviewer_should_not_run(_context):
        raise AssertionError("Routine must not invoke an AI Reviewer gate")

    brain = workflow_for(services, reviewer=reviewer_should_not_run)
    runs = ProjectRunService(agent, brain)
    run = runs.propose(
        "DEMO",
        "R1",
        routine_issue,
        [task_spec()],
        execution_class="routine",
        policy_id="RP1",
    )
    assert run["execution_class"] == "routine"
    assert agent.store.list_records("DEMO", "project_run_reviews") == []
    task = brain.execution.get_task("DEMO", "ROUTINE-T1")
    assert task["brain_plan"].get("alignment_review_required") is not True
    ExecutionService(agent).authorize_routine_task(
        "DEMO", "ROUTINE-T1", "RP1", policy["policy_digest"]
    )
    assert (
        brain.execute_and_assess("DEMO", "ROUTINE-T1")["result"]["node_type"]
        == "result"
    )


def test_governed_facade_completes_routine_without_reviewer(services, routine_issue):
    database, agent, human = services
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
    system = EngineeringAISystem.open_project(
        database, "DEMO", agent.principal, run_integrator=_integrate
    )
    try:
        runs = system.project_runs
        runs.propose("DEMO", "R1", routine_issue, [task_spec()], execution_class="routine", policy_id="RP1")
        system.execution.authorize_routine_task("DEMO", "ROUTINE-T1", "RP1", policy["policy_digest"])
        system.execute_and_assess("DEMO", "ROUTINE-T1")
        runs.integrate("DEMO", "R1")
        outcome = runs.complete_routine("DEMO", "R1")
        assert outcome["disposition"] == "process_complete"
        assert outcome["authority"] == "ai_lead_process_only"
        assert system.registry.list_control_records("DEMO", "project_run_reviews") == []
    finally:
        system.close()


@pytest.mark.parametrize("execution_class", ["engineering", "critical"])
def test_governed_facade_blocks_nonroutine_gates_without_reviewer(services, routine_issue, execution_class):
    database, agent, human = services
    system = EngineeringAISystem.open_project(database, "DEMO", agent.principal)
    try:
        run = system.project_runs.propose("DEMO", "R1", routine_issue, [task_spec()], execution_class=execution_class)
        with pytest.raises(ValueError, match="reviewer unavailable"):
            system.project_runs.review("DEMO", "R1", "plan")
        with pytest.raises(ValueError, match="Gate 1"):
            system.authorize_execution(human.principal, "DEMO", "ROUTINE-T1", run["plan"]["tasks"][0]["task_digest"], "Authorise")
        assert system.execution.get_task("DEMO", "ROUTINE-T1")["state"] == "proposed"
    finally:
        system.close()


def test_governed_facade_without_reviewer_rechecks_routine_policy(services, routine_issue):
    database, agent, human = services
    system = EngineeringAISystem.open_project(database, "DEMO", agent.principal)
    try:
        with pytest.raises(ValueError, match="policy"):
            system.project_runs.propose("DEMO", "R1", routine_issue, [task_spec()], execution_class="routine", policy_id="missing")
        policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
        system.project_runs.propose("DEMO", "R1", routine_issue, [task_spec()], execution_class="routine", policy_id="RP1")
        system.execution.authorize_routine_task("DEMO", "ROUTINE-T1", "RP1", policy["policy_digest"])
        human.revoke_execution_policy("DEMO", "RP1", "Policy revoked before execution")
        with pytest.raises(ValueError, match="policy"):
            system.execute_and_assess("DEMO", "ROUTINE-T1")
    finally:
        system.close()


@pytest.mark.parametrize("action", ["authorize", "execute"])
def test_governed_facade_without_reviewer_blocks_standalone_execution(services, routine_issue, action):
    database, agent, human = services
    system = EngineeringAISystem.open_project(database, "DEMO", agent.principal)
    try:
        spec = task_spec()
        task = system.structure_task("DEMO", spec["task_id"], routine_issue, spec["tool"], spec["parameters"])
        if action == "authorize":
            with pytest.raises(ValueError, match="alignment reviewer"):
                system.authorize_execution(human.principal, "DEMO", spec["task_id"], task["task_digest"], "Authorise")
        else:
            ExecutionService(human).authorize_task("DEMO", spec["task_id"], task["task_digest"], "Legacy host authorisation")
            with pytest.raises(ValueError, match="alignment reviewer"):
                system.execute_and_assess("DEMO", spec["task_id"])
        assert system.execution.get_task("DEMO", spec["task_id"])["state"] != "succeeded"
    finally:
        system.close()


def test_routine_execution_rechecks_revoked_policy(services, routine_issue):
    _, agent, human = services
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
    brain = workflow_for(services, reviewer=lambda _: None)
    ProjectRunService(agent, brain).propose(
        "DEMO",
        "R1",
        routine_issue,
        [task_spec()],
        execution_class="routine",
        policy_id="RP1",
    )
    ExecutionService(agent).authorize_routine_task(
        "DEMO", "ROUTINE-T1", "RP1", policy["policy_digest"]
    )
    human.revoke_execution_policy("DEMO", "RP1", "Stop routine use")
    with pytest.raises(ValueError, match="policy"):
        ExecutionService(agent).execute("DEMO", "ROUTINE-T1")


def test_orphan_routine_task_cannot_use_policy_authorization(services, routine_issue):
    _, agent, human = services
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
    brain = workflow_for(services, reviewer=lambda _: None)
    runs = ProjectRunService(agent, brain)
    runs.propose(
        "DEMO",
        "R1",
        routine_issue,
        [task_spec()],
        execution_class="routine",
        policy_id="RP1",
    )
    copied_plan = brain.execution.get_task("DEMO", "ROUTINE-T1")["brain_plan"]
    brain.execution.propose_task(
        "DEMO",
        "ORPHAN-T",
        routine_issue,
        "compare_revision",
        task_spec()["parameters"],
        brain_plan=copied_plan,
    )
    with pytest.raises(ValueError, match="run|Routine"):
        ExecutionService(agent).authorize_routine_task(
            "DEMO", "ORPHAN-T", "RP1", policy["policy_digest"]
        )


def test_post_plan_unknown_rolls_back_routine_task(
    services, routine_issue, monkeypatch
):
    _, agent, human = services
    human.approve_execution_policy("DEMO", "RP1", policy_definition())
    brain = workflow_for(services, reviewer=lambda _: None)
    original = brain._plan

    def uncertain_plan(*args):
        plan = original(*args)
        plan["unknowns"].append("Source meaning requires review")
        return plan

    monkeypatch.setattr(brain, "_plan", uncertain_plan)
    with pytest.raises(ValueError, match="Routine|class"):
        ProjectRunService(agent, brain).propose(
            "DEMO",
            "R1",
            routine_issue,
            [task_spec()],
            execution_class="routine",
            policy_id="RP1",
        )
    assert agent.store.get_record("DEMO", "tasks", "ROUTINE-T1") is None
    assert agent.store.get_record("DEMO", "project_runs", "R1") is None


def test_critical_plan_requires_human_approval_after_gate1(services, routine_issue):
    _, agent, human = services
    brain = workflow_for(services, reviewer=alignment_report)
    runs = ProjectRunService(
        agent, brain, reviewer=alignment_report, integrator=_integrate
    )
    run = runs.propose(
        "DEMO", "C1", routine_issue, [task_spec()], execution_class="critical"
    )
    runs.review("DEMO", "C1", "plan")
    task = run["plan"]["tasks"][0]
    with pytest.raises(ValueError, match="human plan approval"):
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Start"
        )
    with pytest.raises(PermissionError):
        runs.approve_critical_plan(
            agent.principal, "DEMO", "C1", run["plan_digest"], "Approve"
        )
    runs.approve_critical_plan(
        human.principal, "DEMO", "C1", run["plan_digest"], "Approve bounded scope"
    )
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Start"
    )


def test_routine_process_completion_has_no_engineering_acceptance(
    services, routine_issue
):
    _, agent, human = services
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
    brain = workflow_for(services, reviewer=lambda _: None)
    runs = ProjectRunService(agent, brain, integrator=_integrate)
    runs.propose(
        "DEMO",
        "R1",
        routine_issue,
        [task_spec()],
        execution_class="routine",
        policy_id="RP1",
    )
    with pytest.raises(ValueError, match="Routine"):
        runs.review("DEMO", "R1", "plan")
    ExecutionService(agent).authorize_routine_task(
        "DEMO", "ROUTINE-T1", "RP1", policy["policy_digest"]
    )
    brain.execute_and_assess("DEMO", "ROUTINE-T1")
    runs.integrate("DEMO", "R1")
    completed = runs.complete_routine("DEMO", "R1")
    assert completed["disposition"] == "process_complete"
    assert completed["authority"] == "ai_lead_process_only"
    with pytest.raises(ValueError, match="accepted run"):
        runs.propose_candidate("DEMO", "R1", "NOT-A-LESSON")


def test_material_lead_decision_blocks_routine_completion(services, routine_issue):
    from engineering_registry.service import IMPACT_DOMAINS

    _, agent, human = services
    policy = human.approve_execution_policy("DEMO", "RP1", policy_definition())
    brain = workflow_for(services, reviewer=lambda _: None)
    runs = ProjectRunService(agent, brain, integrator=_integrate)
    runs.propose(
        "DEMO",
        "R1",
        routine_issue,
        [task_spec()],
        execution_class="routine",
        policy_id="RP1",
    )
    ExecutionService(agent).authorize_routine_task(
        "DEMO", "ROUTINE-T1", "RP1", policy["policy_digest"]
    )
    brain.execute_and_assess("DEMO", "ROUTINE-T1")
    impacts = dict.fromkeys(IMPACT_DOMAINS, "no")
    impacts["design_basis"] = "yes"
    agent.record_lead_step(
        "DEMO",
        routine_issue,
        "N-MATERIAL",
        "Revise design basis",
        "New project consequence",
        impacts,
        task_id="ROUTINE-T1",
        evidence_ids=["evidence:EA"],
    )
    runs.integrate("DEMO", "R1")
    with pytest.raises(ValueError, match="material|Routine"):
        runs.complete_routine("DEMO", "R1")


def test_critical_outcome_requires_human_and_engineering_can_use_delegation(
    services, routine_issue
):
    _, agent, human = services
    for run_id, task_id, execution_class in (
        ("C1", "CRIT-T", "critical"),
        ("E1", "ENG-T", "engineering"),
    ):
        brain = workflow_for(services, reviewer=alignment_report)
        runs = ProjectRunService(
            agent, brain, reviewer=alignment_report, integrator=_integrate
        )
        spec = {**task_spec(), "task_id": task_id}
        run = runs.propose(
            "DEMO", run_id, routine_issue, [spec], execution_class=execution_class
        )
        runs.review("DEMO", run_id, "plan")
        if execution_class == "critical":
            runs.approve_critical_plan(
                human.principal, "DEMO", run_id, run["plan_digest"], "Approve"
            )
        task = run["plan"]["tasks"][0]
        ExecutionService(human).authorize_task(
            "DEMO", task_id, task["task_digest"], "Start"
        )
        brain.execute_and_assess("DEMO", task_id)
        runs.integrate("DEMO", run_id)
        runs.review("DEMO", run_id, "outcome")
        if execution_class == "critical":
            with pytest.raises(PermissionError):
                runs.decide(
                    agent.principal, "DEMO", run_id, "accept", ["evidence:EA"], "Accept"
                )
            assert (
                runs.decide(
                    human.principal, "DEMO", run_id, "accept", ["evidence:EA"], "Accept"
                )["decided_by"]
                == human.principal.actor_id
            )
        else:
            assert (
                runs.complete_engineering("DEMO", run_id, "P1")["authority"]
                == "human_approved_result_delegation"
            )


def test_legacy_run_without_class_keeps_two_gate_guard(services, routine_issue):
    _, agent, human = services
    brain = workflow_for(services, reviewer=alignment_report)
    runs = ProjectRunService(agent, brain, reviewer=alignment_report)
    run = runs.propose("DEMO", "LEGACY", routine_issue, [task_spec()])
    for field in (
        "execution_class",
        "classification_status",
        "classification_reasons",
        "classification_source_digest",
        "policy_id",
        "policy_digest",
        "schema_version",
    ):
        run.pop(field, None)
    agent.store.put_record("DEMO", "project_runs", "LEGACY", run, replace=True)
    task = run["plan"]["tasks"][0]
    with pytest.raises(ValueError, match="Gate 1"):
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Start"
        )
    runs.review("DEMO", "LEGACY", "plan")
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Start"
    )
