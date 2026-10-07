"""Registry-side guard for tasks bound to a project-wide plan."""

from __future__ import annotations

from .service import RegistryService, digest
from .store import GraphIntegrityError

DEPENDENCY_BINDINGS = "project_run_dependency_bindings"


def dependency_binding_id(run_id: str, task_id: str) -> str:
    return "project_run_dependency_binding:" + digest([run_id, task_id])


def _require_current_dependency_binding(
    registry: RegistryService, project_id: str, run: dict, task: dict
) -> None:
    bound_tasks = {item["task_id"]: item for item in run["plan"]["tasks"]}
    bound_task = bound_tasks[task["task_id"]]
    predecessor_ids = sorted(bound_task.get("depends_on", []))
    if not predecessor_ids:
        return
    binding = registry.get_control_record(
        project_id,
        DEPENDENCY_BINDINGS,
        dependency_binding_id(run["run_id"], task["task_id"]),
    )
    if not binding:
        raise ValueError("Project-run dependency binding is required before execution")
    payload = {key: value for key, value in binding.items() if key != "binding_digest"}
    if (
        binding.get("run_id") != run["run_id"]
        or binding.get("plan_digest") != run["plan_digest"]
        or binding.get("task_id") != task["task_id"]
        or binding.get("task_digest") != task["task_digest"]
        or [item.get("task_id") for item in binding.get("dependencies", [])]
        != predecessor_ids
        or digest(payload) != binding.get("binding_digest")
    ):
        raise ValueError("Project-run dependency binding is invalid or stale")

    for item in binding["dependencies"]:
        predecessor_id = item["task_id"]
        predecessor = registry.get_control_record(project_id, "tasks", predecessor_id)
        expected = bound_tasks[predecessor_id]
        if (
            not predecessor
            or predecessor["state"] != "succeeded"
            or predecessor["task_digest"] != expected["task_digest"]
            or predecessor.get("result_id") != item.get("result_id")
        ):
            raise ValueError("Project-run dependency binding is stale")
        try:
            result = registry.get_record(project_id, predecessor["result_id"])
        except GraphIntegrityError as exc:
            raise ValueError("Project-run dependency result changed after binding") from exc
        if not result or digest(result) != item.get("result_digest"):
            raise ValueError("Project-run dependency result changed after binding")
        assessments = [
            record
            for record in registry.list_records(project_id, "assessment")
            if record["attributes"].get("result_id") == result["node_id"]
            and record["attributes"].get("result_digest") == digest(result)
            and record["attributes"].get("task_digest") == predecessor["task_digest"]
        ]
        assessments.sort(key=lambda record: record["attributes"].get("produced_at", ""))
        assessment = assessments[-1] if assessments else None
        if (
            not assessment
            or assessment["node_id"] != item.get("assessment_id")
            or digest(assessment) != item.get("assessment_digest")
        ):
            raise ValueError("Project-run dependency assessment changed after binding")
        decisions = [
            record
            for record in registry.list_records(project_id, "decision")
            if record["attributes"].get("result_id") == result["node_id"]
            and record["attributes"].get("disposition")
            in {"accept", "hold", "rework", "reject"}
        ]
        decisions.sort(key=lambda record: record["attributes"].get("proposed_at", ""))
        if decisions and decisions[-1]["attributes"]["disposition"] in {
            "hold",
            "rework",
            "reject",
        }:
            raise ValueError("Project-run predecessor has a blocking human disposition")


def _require_specialist_plan_decision(
    registry: RegistryService, project_id: str, run: dict
) -> None:
    specialists = run.get("plan", {}).get("discipline_specialists", [])
    if len(specialists) <= 3:
        return
    specialist_digest = digest(specialists)
    if run.get("execution_class") == "critical":
        approval_id = run.get("human_plan_approval_id")
        decision = (
            registry.get_control_record(
                project_id, "project_run_approvals", approval_id
            )
            if approval_id
            else None
        )
        valid = (
            decision
            and decision.get("run_id") == run["run_id"]
            and decision.get("plan_digest") == run["plan_digest"]
            and decision.get("specialist_assignment_digest") == specialist_digest
            and decision.get("approved_by")
        )
    else:
        decision_id = run.get("specialist_plan_decision_id")
        decision = (
            registry.get_control_record(
                project_id, "project_run_specialist_decisions", decision_id
            )
            if decision_id
            else None
        )
        valid = (
            decision
            and decision.get("run_id") == run["run_id"]
            and decision.get("plan_digest") == run["plan_digest"]
            and decision.get("specialist_assignment_digest") == specialist_digest
            and decision.get("disposition") == "approve"
            and decision.get("decided_by")
        )
    if not valid:
        raise ValueError(
            "Project-run discipline specialist plan approval is required or stale"
        )


def require_no_new_material_decisions(
    registry: RegistryService, project_id: str, run: dict
) -> None:
    """A Routine run must stop when the Lead adds a material issue decision."""
    if any(
        record["attributes"].get("decision_type") == "task"
        and record["attributes"].get("issue_id") == run["issue_id"]
        and record["attributes"].get("proposed_at", "") >= run["created_at"]
        for record in registry.list_records(project_id, "decision")
    ):
        raise ValueError("Routine run has a new material Lead decision; reclassify")


def require_run_plan_gate(
    registry: RegistryService,
    project_id: str,
    task: dict,
    *,
    require_dependency_binding: bool = False,
) -> None:
    """Block direct V2 paths until the exact complete run plan passed Gate 1."""
    runs = [
        run
        for run in registry.store.list_records(project_id, "project_runs")
        if task["task_id"] in run.get("task_ids", [])
    ]
    if not runs:
        if (task.get("brain_plan") or {}).get("execution_class") == "routine":
            raise ValueError("Routine task requires a retained, finalized project run")
        return
    if len(runs) != 1:
        raise ValueError("A V2 task cannot belong to multiple project runs")
    run = runs[0]
    if run.get("state") == "preparing" or "plan" not in run:
        raise ValueError("Project run plan is not finalized")
    bound = {item["task_id"]: item["task_digest"] for item in run["plan"]["tasks"]}
    if (
        task["task_id"] not in bound
        or task["task_digest"] != bound[task["task_id"]]
        or run["plan_digest"] != digest(run["plan"])
    ):
        raise ValueError(
            "Project-run task or complete plan changed; create a fresh run"
        )
    if run.get("classification_status") == "hold":
        raise ValueError("Project-run class is on hold pending human resolution")
    if run.get("execution_class") == "routine":
        from .execution_policy import current_execution_policy, require_policy_tasks

        require_no_new_material_decisions(registry, project_id, run)
        policy = current_execution_policy(registry, project_id, run.get("policy_id"))
        if (
            policy["policy_digest"] != run.get("policy_digest")
            or task.get("brain_plan", {}).get("execution_class") != "routine"
            or task["brain_plan"].get("run_id") != run["run_id"]
            or task["brain_plan"].get("execution_policy_digest") != run["policy_digest"]
        ):
            raise ValueError("Routine task policy or run binding changed")
        require_policy_tasks(policy, [task])
        _require_specialist_plan_decision(registry, project_id, run)
        if require_dependency_binding:
            _require_current_dependency_binding(registry, project_id, run, task)
        return
    review_id = run.get("gate1_review_id")
    review = (
        registry.store.get_record(project_id, "project_run_reviews", review_id)
        if review_id
        else None
    )
    if (
        not review
        or review["phase"] != "plan"
        or review["plan_digest"] != run["plan_digest"]
        or review["report"]["status"] != "aligned"
        or review["report"]["unknowns"]
    ):
        raise ValueError(
            "Project-run Gate 1 requires an aligned review of the complete plan"
        )
    if run.get("execution_class") == "critical":
        approval_id = run.get("human_plan_approval_id")
        approval = (
            registry.store.get_record(project_id, "project_run_approvals", approval_id)
            if approval_id
            else None
        )
        if (
            not approval
            or approval.get("run_id") != run["run_id"]
            or approval.get("plan_digest") != run["plan_digest"]
            or approval.get("gate1_review_id") != review_id
            or not approval.get("approved_by")
        ):
            raise ValueError("Critical run requires current human plan approval")
    _require_specialist_plan_decision(registry, project_id, run)
    if require_dependency_binding:
        _require_current_dependency_binding(registry, project_id, run, task)
