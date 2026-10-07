"""Human approval gate for larger discipline-specialist project plans."""

from datetime import UTC, datetime, timedelta

import pytest

from engineering_ai_system.project_run import ProjectRunService
from engineering_execution import ExecutionService
from engineering_registry.desk import apply_action, render_page
from engineering_registry.service import digest
from tests.support.project_run_harness import alignment_report, integrate, workflow_for


def _task(task_id, specialist_id=None, discipline=None):
    task = {
        "task_id": task_id,
        "tool": "compare_revision",
        "parameters": {
            "base_evidence_id": "evidence:EA",
            "head_evidence_id": "evidence:EB",
        },
    }
    if specialist_id is not None:
        task["specialist_assignment"] = {
            "specialist_id": specialist_id,
            "discipline": discipline,
            "skill": {
                "structural": "structural-review",
                "geotechnical": "geotechnical-review",
                "marine and coastal": "marine-coastal-review",
                "hydraulic and environmental": "hydraulic-environmental-review",
            }[discipline],
            "task_scope": f"Review the {discipline} evidence for {task_id}.",
            "deliverable": f"Evidence-linked {discipline} findings for {task_id}.",
        }
    return task


def _service(run_fixture):
    _, agent, human = run_fixture
    return ProjectRunService(
        agent,
        workflow_for(run_fixture),
        reviewer=alignment_report,
        integrator=integrate,
        execution_mode="TEST",
    )


def test_governed_project_run_rejects_unvalidated_discipline_skill_before_writes(
    run_fixture,
):
    _, agent, _ = run_fixture
    service = ProjectRunService(
        agent,
        workflow_for(run_fixture),
        reviewer=alignment_report,
        integrator=integrate,
    )

    with pytest.raises(ValueError, match="discipline-owner validation"):
        service.propose(
            "DEMO",
            "UNVALIDATED-SKILL",
            "issue:RUN",
            [_task("UNVALIDATED-TASK", "agent-struct", "structural")],
        )

    assert agent.get_control_record("DEMO", "project_runs", "UNVALIDATED-SKILL") is None


def _four_specialist_tasks():
    return [
        _task("STRUCT", "agent-struct", "structural"),
        _task("GEO", "agent-geo", "geotechnical"),
        _task("MARINE", "agent-marine", "marine and coastal"),
        _task("HYDRO", "agent-hydro", "hydraulic and environmental"),
    ]


def _routine_policy():
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


def test_four_discipline_specialists_require_human_decision_before_authorization(
    run_fixture,
):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO", "SPECIALIST-APPROVAL", "issue:RUN", _four_specialist_tasks()
    )
    service.review("DEMO", run["run_id"], "plan")
    first = run["plan"]["tasks"][0]

    with pytest.raises(ValueError, match="discipline specialist plan approval"):
        ExecutionService(human).authorize_task(
            "DEMO", first["task_id"], first["task_digest"], "Authorize"
        )

    status = service.get_status("DEMO", run["run_id"])
    request = status["specialist_approval_request"]
    assert request["required"] is True
    assert request["specialist_count"] == 4
    assert request["threshold"] == 3
    structural = next(
        item for item in request["specialists"] if item["discipline"] == "structural"
    )
    assert structural["assignments"][0]["skill"] == "structural-review"
    assert structural["assignments"][0]["tools"] == [
        {"name": "compare_revision", "version": "1"}
    ]
    assert structural["assignments"][0]["source_access"]["evidence_ids"] == [
        "evidence:EA",
        "evidence:EB",
    ]

    decision = service.decide_specialist_plan(
        human.principal,
        "DEMO",
        run["run_id"],
        run["plan_digest"],
        "approve",
        "Four discipline assignments are appropriately bounded.",
    )
    assert decision["disposition"] == "approve"
    assert decision["plan_digest"] == run["plan_digest"]

    authorized = ExecutionService(human).authorize_task(
        "DEMO", first["task_id"], first["task_digest"], "Authorize"
    )
    assert authorized["state"] == "queued"


def test_three_distinct_specialists_do_not_trigger_additional_approval(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    tasks = _four_specialist_tasks()[:3]
    run = service.propose("DEMO", "THREE-SPECIALISTS", "issue:RUN", tasks)
    service.review("DEMO", run["run_id"], "plan")

    first = run["plan"]["tasks"][0]
    authorized = ExecutionService(human).authorize_task(
        "DEMO", first["task_id"], first["task_digest"], "Authorize"
    )

    assert authorized["state"] == "queued"
    assert service.get_status("DEMO", run["run_id"])[
        "specialist_approval_request"
    ]["required"] is False


def test_repeated_tasks_for_one_specialist_count_as_one_agent(run_fixture):
    service = _service(run_fixture)
    run = service.propose(
        "DEMO",
        "ONE-SPECIALIST-MULTIPLE-TASKS",
        "issue:RUN",
        [
            _task("STRUCT-A", "agent-struct", "structural"),
            _task("STRUCT-B", "agent-struct", "structural"),
            _task("STRUCT-C", "agent-struct", "structural"),
            _task("STRUCT-D", "agent-struct", "structural"),
        ],
    )

    request = service.get_status("DEMO", run["run_id"])[
        "specialist_approval_request"
    ]
    assert request["specialist_count"] == 1
    assert request["required"] is False
    assert len(request["specialists"][0]["assignments"]) == 4


@pytest.mark.parametrize("disposition", ["hold", "revise_and_resubmit"])
def test_nonapproval_specialist_decisions_block_task_authorization(
    run_fixture, disposition
):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO", "SPECIALIST-DECISION-" + disposition, "issue:RUN", _four_specialist_tasks()
    )
    service.review("DEMO", run["run_id"], "plan")
    service.decide_specialist_plan(
        human.principal,
        "DEMO",
        run["run_id"],
        run["plan_digest"],
        disposition,
        "Resolve the stated planning concern before dispatch.",
    )
    first = run["plan"]["tasks"][0]

    with pytest.raises(ValueError, match="discipline specialist plan approval"):
        ExecutionService(human).authorize_task(
            "DEMO", first["task_id"], first["task_digest"], "Authorize"
        )


def test_conditional_approval_requires_explicit_human_condition_confirmation(
    run_fixture,
):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO", "CONDITIONAL-SPECIALISTS", "issue:RUN", _four_specialist_tasks()
    )
    service.review("DEMO", run["run_id"], "plan")
    conditional = service.decide_specialist_plan(
        human.principal,
        "DEMO",
        run["run_id"],
        run["plan_digest"],
        "approve_with_conditions",
        "Approve after the human confirms the stated prerequisite.",
        conditions=["Confirm each specialist's inputs are within the listed scope."],
    )
    first = run["plan"]["tasks"][0]

    with pytest.raises(ValueError, match="discipline specialist plan approval"):
        ExecutionService(human).authorize_task(
            "DEMO", first["task_id"], first["task_digest"], "Authorize"
        )
    with pytest.raises(ValueError, match="Every approval condition"):
        service.confirm_specialist_plan_conditions(
            human.principal,
            "DEMO",
            run["run_id"],
            run["plan_digest"],
            [],
            "Conditions remain outstanding.",
        )

    service.confirm_specialist_plan_conditions(
        human.principal,
        "DEMO",
        run["run_id"],
        run["plan_digest"],
        conditional["condition_ids"],
        "I confirmed the stated condition is satisfied.",
    )
    assert ExecutionService(human).authorize_task(
        "DEMO", first["task_id"], first["task_digest"], "Authorize"
    )["state"] == "queued"


def test_review_desk_shows_roster_and_records_human_specialist_decision(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO", "SPECIALIST-DESK", "issue:RUN", _four_specialist_tasks()
    )
    service.review("DEMO", run["run_id"], "plan")

    page = render_page(human, demo=False)
    assert "Discipline specialist plan — human decision required" in page
    assert "structural-review" in page
    assert "compare_revision" in page
    assert "Approve with conditions" in page

    apply_action(
        human,
        {
            "project_id": "DEMO",
            "record_id": run["run_id"],
            "operation": "specialist_plan_decision",
            "digest": run["plan_digest"],
            "disposition": "approve",
            "rationale": "Reviewed the discipline assignments and task boundaries.",
        },
        workflow_factory=lambda _registry: workflow_for(run_fixture),
    )

    decision = service.get_status("DEMO", run["run_id"])[
        "specialist_plan_decision"
    ]
    assert decision["disposition"] == "approve"
    assert decision["decided_by"] == human.principal.actor_id


def test_specialist_approval_rejects_wrong_plan_digest_and_nonhuman(run_fixture):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    run = service.propose(
        "DEMO", "SPECIALIST-APPROVAL-IDENTITY", "issue:RUN", _four_specialist_tasks()
    )
    service.review("DEMO", run["run_id"], "plan")

    with pytest.raises(ValueError, match="plan digest"):
        service.decide_specialist_plan(
            human.principal,
            "DEMO",
            run["run_id"],
            "wrong-digest",
            "approve",
            "Approve the plan.",
        )
    with pytest.raises(PermissionError):
        service.decide_specialist_plan(
            agent.principal,
            "DEMO",
            run["run_id"],
            run["plan_digest"],
            "approve",
            "Approve the plan.",
        )


def test_routine_four_specialist_plan_is_blocked_until_human_approval(run_fixture):
    service = _service(run_fixture)
    _, agent, human = run_fixture
    policy = human.approve_execution_policy("DEMO", "P-SPECIALIST", _routine_policy())
    run = service.propose(
        "DEMO",
        "ROUTINE-SPECIALISTS",
        "issue:RUN",
        _four_specialist_tasks(),
        execution_class="routine",
        policy_id="P-SPECIALIST",
    )
    first = run["plan"]["tasks"][0]

    with pytest.raises(ValueError, match="discipline specialist plan approval"):
        ExecutionService(agent).authorize_routine_task(
            "DEMO", first["task_id"], "P-SPECIALIST", policy["policy_digest"]
        )

    service.decide_specialist_plan(
        human.principal,
        "DEMO",
        run["run_id"],
        run["plan_digest"],
        "approve",
        "Approve the listed bounded discipline assignments.",
    )
    assert ExecutionService(agent).authorize_routine_task(
        "DEMO", first["task_id"], "P-SPECIALIST", policy["policy_digest"]
    )["state"] == "queued"


def test_critical_plan_approval_covers_specialist_roster_digest(run_fixture):
    service = _service(run_fixture)
    _, _, human = run_fixture
    run = service.propose(
        "DEMO",
        "CRITICAL-SPECIALISTS",
        "issue:RUN",
        _four_specialist_tasks(),
        execution_class="critical",
    )
    service.review("DEMO", run["run_id"], "plan")
    approval = service.approve_critical_plan(
        human.principal,
        "DEMO",
        run["run_id"],
        run["plan_digest"],
        "Approve the complete plan and specialist assignments.",
    )

    assert approval["specialist_assignment_digest"] == digest(
        service.get_status("DEMO", run["run_id"])["specialist_approval_request"][
            "specialists"
        ]
    )
    assert (
        service.get_status("DEMO", run["run_id"])["specialist_plan_decision"]
        == approval
    )
    first = run["plan"]["tasks"][0]
    assert ExecutionService(human).authorize_task(
        "DEMO", first["task_id"], first["task_digest"], "Authorize Critical work"
    )["state"] == "queued"
def test_invalid_specialist_metadata_is_rejected_before_tasks_are_retained(run_fixture):
    service = _service(run_fixture)
    _, agent, _ = run_fixture
    tasks = _four_specialist_tasks()
    tasks[0]["specialist_assignment"]["tools"] = ["shell"]

    with pytest.raises(ValueError, match="Invalid discipline specialist assignment"):
        service.propose("DEMO", "INVALID-SPECIALIST", "issue:RUN", tasks)

    assert not agent.get_control_record("DEMO", "tasks", "STRUCT")
