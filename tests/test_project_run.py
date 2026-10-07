"""Project-wide gates, integration and human knowledge promotion."""

import pytest

from engineering_ai_system import EngineeringAISystem
from engineering_ai_system.project_run import ProjectRunService
from engineering_execution import ExecutionService
from engineering_registry.service import Principal, RegistryService
from tests.support.project_run_harness import (
    alignment_report,
    integrate,
    workflow_for,
)


def review_as(context, status):
    report = alignment_report(context)
    report["status"] = status
    report["checks"] = [{**check, "status": status} for check in report["checks"]]
    if status == "insufficient_information":
        report["unknowns"] = [
            "Integrated run assessment contains unresolved uncertainty."
        ]
    return report


def generalize(_context):
    return {
        "title": "Revision comparison lesson",
        "observation": "Two changes were compared.",
        "result": "Comparison completed.",
        "interpretation": "Controlled comparison is reusable.",
        "validated_statement": "Compare controlled revisions with source evidence.",
        "applicability": "Only when current project requirements permit.",
        "limitations": "Recheck revision status and project context.",
        "relevant_standards": [],
        "review_due": "2099-01-01",
    }


def test_two_task_run_and_human_knowledge_promotion(run_fixture):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)
    service = ProjectRunService(
        agent,
        brain,
        reviewer=alignment_report,
        integrator=integrate,
        generalizer=generalize,
    )
    specs = [
        {
            "task_id": task_id,
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
        for task_id in ("RUN-T1", "RUN-T2")
    ]
    run = service.propose("DEMO", "RUN-1", "issue:RUN", specs)
    with pytest.raises(ValueError, match="Gate 1"):
        ExecutionService(human).authorize_task(
            "DEMO", "RUN-T1", run["plan"]["tasks"][0]["task_digest"], "Authorise"
        )
    service.review("DEMO", "RUN-1", "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise"
        )
        brain.execute_and_assess("DEMO", task["task_id"])
    integration = service.integrate("DEMO", "RUN-1")
    assert len(integration["task_outcomes"]) == 2
    assert all(
        item["result"] and item["assessment"] for item in integration["task_outcomes"]
    )
    assert all(
        item["source_snapshot"]["records"] for item in integration["task_outcomes"]
    )
    assert all(
        item["provenance"]["source_revisions"] for item in integration["task_outcomes"]
    )
    retained_assessment = agent.store.get_record(
        "DEMO", "project_run_assessments", integration["assessment_id"]
    )
    assert retained_assessment["assessment_digest"] == integration["assessment_digest"]
    gate2 = service.review("DEMO", "RUN-1", "outcome")
    assert gate2["integrated_assessment_id"] == integration["assessment_id"]
    assert gate2["integrated_assessment_digest"] == integration["assessment_digest"]
    current_run = agent.store.get_record("DEMO", "project_runs", "RUN-1")
    assert (
        service._context("DEMO", current_run, "outcome")["integrated_run_assessment"]
        == retained_assessment
    )
    with pytest.raises(PermissionError):
        service.decide(
            agent.principal, "DEMO", "RUN-1", "accept", ["evidence:EA"], "Accept"
        )
    outcome = service.decide(
        human.principal,
        "DEMO",
        "RUN-1",
        "accept",
        ["evidence:EA", "evidence:EB"],
        "Accepted combined result",
        lesson_id="LESSON-RUN-1",
        lesson_title="Project revision lesson",
    )
    assert outcome["project_lesson_id"] == "LESSON-RUN-1"
    draft = service.propose_candidate("DEMO", "RUN-1", "CAND-1")
    assert draft["status"] == "candidate" and draft["proposal"] is None
    candidate = service.generalize("DEMO", "CAND-1")
    with pytest.raises(PermissionError):
        service.validate_candidate(
            agent.principal,
            "DEMO",
            "CAND-1",
            candidate["candidate_digest"],
            True,
            "Approved",
            organization_id="ORG1",
            organization_lesson_id="ORG-LESSON-1",
        )
    curator = Principal("curator", frozenset({"DEMO"}), "reviewer", frozenset({"ORG1"}))
    validated = service.validate_candidate(
        curator,
        "DEMO",
        "CAND-1",
        candidate["candidate_digest"],
        True,
        "Validated for reusable context",
        organization_id="ORG1",
        organization_lesson_id="ORG-LESSON-1",
    )
    assert validated["status"] == "validated"
    assert RegistryService(agent.store, curator).get_record("ORG:ORG1", "ORG-LESSON-1")


@pytest.mark.parametrize("execution_class", ["engineering", "critical"])
def test_governed_facade_blocks_gate2_when_reviewer_unavailable(run_fixture, execution_class):
    _, agent, human = run_fixture
    system = EngineeringAISystem(agent, workflow_for(run_fixture), run_integrator=integrate)
    runs = system.project_runs
    run = runs.propose("DEMO", "RUN", "issue:RUN", [{
        "task_id": "T1",
        "tool": "compare_revision",
        "parameters": {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    }], execution_class=execution_class)
    runs.review("DEMO", "RUN", "plan")
    if execution_class == "critical":
        runs.approve_critical_plan(human.principal, "DEMO", "RUN", run["plan_digest"], "Approve complete plan")
    system.authorize_execution(human.principal, "DEMO", "T1", run["plan"]["tasks"][0]["task_digest"], "Authorise")
    system.execute_and_assess("DEMO", "T1")
    runs.integrate("DEMO", "RUN")
    runs.reviewer = None
    with pytest.raises(ValueError, match="reviewer unavailable"):
        runs.review("DEMO", "RUN", "outcome")
    with pytest.raises(ValueError, match="Gate 2"):
        runs.decide(human.principal, "DEMO", "RUN", "accept", ["evidence:EA"], "Accept")
    assert runs.get_status("DEMO", "RUN")["outcome"] is None


def test_incomplete_v2_run_cannot_be_accepted(run_fixture):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)

    def incomplete(context):
        proposal = integrate(context)
        proposal["unknowns"] = ["Second V2 task has not completed."]
        return proposal

    service = ProjectRunService(
        agent,
        brain,
        reviewer=lambda context: (
            review_as(context, "insufficient_information")
            if context["phase"] == "outcome"
            else alignment_report(context)
        ),
        integrator=incomplete,
    )
    specs = [
        {
            "task_id": task_id,
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
        for task_id in ("PART-T1", "PART-T2")
    ]
    run = service.propose("DEMO", "PART", "issue:RUN", specs)
    service.review("DEMO", "PART", "plan")
    first = run["plan"]["tasks"][0]
    ExecutionService(human).authorize_task(
        "DEMO", first["task_id"], first["task_digest"], "Authorise"
    )
    brain.execute_and_assess("DEMO", first["task_id"])
    integration = service.integrate("DEMO", "PART")
    assert integration["task_outcomes"][1]["state"] == "proposed"
    service.review("DEMO", "PART", "outcome")
    with pytest.raises(ValueError, match="Unresolved"):
        service.decide(
            human.principal, "DEMO", "PART", "accept", ["evidence:EA"], "Accept"
        )
    held = service.decide(
        human.principal,
        "DEMO",
        "PART",
        "hold",
        ["evidence:EA"],
        "Await second specialist",
    )
    assert held["disposition"] == "hold"
    with pytest.raises(ValueError, match="Candidate requires"):
        service.propose_candidate("DEMO", "PART", "PART-CAND")


def test_conflicting_lead_integration_blocks_acceptance(run_fixture):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)

    def conflicting(context):
        proposal = integrate(context)
        proposal["conflicts"] = ["Specialist outputs require reconciliation."]
        proposal["integrated_assessment"]["status"] = "concerns"
        proposal["integrated_assessment"]["cross_task_checks"][-2].update(
            status="concern", detail="Specialist outputs contradict one another."
        )
        proposal["recommendation"] = "Hold for specialist reconciliation."
        return proposal

    service = ProjectRunService(
        agent,
        brain,
        reviewer=lambda context: (
            review_as(context, "misaligned")
            if context["phase"] == "outcome"
            else alignment_report(context)
        ),
        integrator=conflicting,
    )
    specs = [
        {
            "task_id": task_id,
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
        for task_id in ("CON-T1", "CON-T2")
    ]
    run = service.propose("DEMO", "CON", "issue:RUN", specs)
    service.review("DEMO", "CON", "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Authorise"
        )
        brain.execute_and_assess("DEMO", task["task_id"])
    service.integrate("DEMO", "CON")
    service.review("DEMO", "CON", "outcome")
    with pytest.raises(ValueError, match="Unresolved"):
        service.decide(
            human.principal, "DEMO", "CON", "accept", ["evidence:EA"], "Accept"
        )


def test_integrated_assessment_rejects_unsubstantiated_not_applicable_checks(
    run_fixture,
):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)

    def suppress_checks(context):
        proposal = integrate(context)
        for check in proposal["integrated_assessment"]["cross_task_checks"]:
            check.update(status="not_applicable", detail="Not applicable.")
        return proposal

    service = ProjectRunService(
        agent, brain, reviewer=alignment_report, integrator=suppress_checks
    )
    run = service.propose(
        "DEMO",
        "N-A-RUN",
        "issue:RUN",
        [
            {
                "task_id": "N-A-T1",
                "tool": "compare_revision",
                "parameters": {
                    "base_evidence_id": "evidence:EA",
                    "head_evidence_id": "evidence:EB",
                },
            }
        ],
    )
    service.review("DEMO", "N-A-RUN", "plan")
    task = run["plan"]["tasks"][0]
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Authorise"
    )
    brain.execute_and_assess("DEMO", task["task_id"])

    with pytest.raises(ValueError, match="Invalid integrated cross-task status"):
        service.integrate("DEMO", "N-A-RUN")


def test_gate2_rejects_stale_integrated_assessment(run_fixture):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)
    service = ProjectRunService(
        agent, brain, reviewer=alignment_report, integrator=integrate
    )
    specs = [
        {
            "task_id": "STALE-T1",
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
    ]
    run = service.propose("DEMO", "STALE", "issue:RUN", specs)
    service.review("DEMO", "STALE", "plan")
    task = run["plan"]["tasks"][0]
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Run"
    )
    brain.execute_and_assess("DEMO", task["task_id"])
    integration = service.integrate("DEMO", "STALE")
    assessment = agent.store.get_record(
        "DEMO", "project_run_assessments", integration["assessment_id"]
    )
    assessment["assessment"]["status"] = "concerns"
    agent.store.put_record(
        "DEMO",
        "project_run_assessments",
        integration["assessment_id"],
        assessment,
        replace=True,
    )
    with pytest.raises(ValueError, match="assessment|digest|changed"):
        service.review("DEMO", "STALE", "outcome")


def test_gate2_rejects_changed_source_revision_after_integration(run_fixture):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)
    service = ProjectRunService(
        agent, brain, reviewer=alignment_report, integrator=integrate
    )
    specs = [
        {
            "task_id": "REV-T1",
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
    ]
    run = service.propose("DEMO", "REV", "issue:RUN", specs)
    service.review("DEMO", "REV", "plan")
    task = run["plan"]["tasks"][0]
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Run"
    )
    brain.execute_and_assess("DEMO", task["task_id"])
    service.integrate("DEMO", "REV")
    current = human.document_control("DEMO", "document_revision:BASIS-B%3AB")
    human.review_document_control(
        "DEMO",
        "document_revision:BASIS-B%3AB",
        current["control_digest"],
        "superseded",
        ["evidence:EB"],
        "A newer controlled revision was received.",
    )
    with pytest.raises(ValueError, match="stale|changed|revision|source"):
        service.review("DEMO", "REV", "outcome")


def test_material_assumption_conflict_cannot_receive_clean_recommendation(run_fixture):
    configured = run_fixture
    _, agent, human = configured
    brain = workflow_for(configured)

    def conflicting_assumption(context):
        proposal = integrate(context)
        proposal["integrated_assessment"]["status"] = "concerns"
        proposal["integrated_assessment"]["assumptions"] = [
            {
                "statement": "Both specialists assumed the same support condition.",
                "status": "concern",
                "task_ids": [item["task_id"] for item in context["task_outcomes"]],
                "evidence_ids": ["evidence:EA", "evidence:EB"],
            }
        ]
        proposal["integrated_assessment"]["cross_task_checks"][1].update(
            status="concern", detail="The specialists used inconsistent assumptions."
        )
        proposal["recommendation"] = "Accept the combined result."
        return proposal

    service = ProjectRunService(
        agent, brain, reviewer=alignment_report, integrator=conflicting_assumption
    )
    specs = [
        {
            "task_id": task_id,
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
        for task_id in ("ASM-T1", "ASM-T2")
    ]
    run = service.propose("DEMO", "ASM", "issue:RUN", specs)
    service.review("DEMO", "ASM", "plan")
    for task in run["plan"]["tasks"]:
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Run"
        )
        brain.execute_and_assess("DEMO", task["task_id"])
    with pytest.raises(
        ValueError, match="recommendation.*hold|recommendation.*qualify"
    ):
        service.integrate("DEMO", "ASM")


def test_run_rejects_task_prepared_before_complete_plan(run_fixture):
    configured = run_fixture
    _, agent, _ = configured
    brain = workflow_for(configured)
    brain.structure_task(
        "DEMO",
        "OLD-T1",
        "issue:RUN",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )
    service = ProjectRunService(agent, brain, reviewer=alignment_report)
    specs = [
        {
            "task_id": task_id,
            "tool": "compare_revision",
            "parameters": {
                "base_evidence_id": "evidence:EA",
                "head_evidence_id": "evidence:EB",
            },
        }
        for task_id in ("OLD-T1", "NEW-T2")
    ]
    with pytest.raises(ValueError, match="new, unexecuted"):
        service.propose("DEMO", "INVALID", "issue:RUN", specs)
    assert agent.store.get_record("DEMO", "tasks", "NEW-T2") is None


def test_run_mode_is_retained_and_tampering_cannot_downgrade_governance(run_fixture):
    configured = run_fixture
    _, agent, _ = configured
    service = ProjectRunService(
        agent, workflow_for(configured), reviewer=alignment_report
    )
    run = service.propose(
        "DEMO",
        "MODE-RUN",
        "issue:RUN",
        [
            {
                "task_id": "MODE-T1",
                "tool": "compare_revision",
                "parameters": {
                    "base_evidence_id": "evidence:EA",
                    "head_evidence_id": "evidence:EB",
                },
            }
        ],
    )
    assert run["execution_mode"] == "GOVERNED"
    run["execution_mode"] = "TEST"
    agent.store.put_record("DEMO", "project_runs", "MODE-RUN", run, replace=True)
    with pytest.raises(ValueError, match="execution mode changed"):
        service.get_status("DEMO", "MODE-RUN")
