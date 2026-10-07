"""Durable V2 tasks with explicit authority, bounded inputs and reproducible results."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable
from typing import Any, cast

from engineering_registry.models import GraphEdge, GraphNode, NodeType, RelationshipType
from engineering_registry.service import RegistryService, digest, text, timestamp
from engineering_registry.store import GraphIntegrityError

from .fem import fem_task_binding, run_linear_static_job_isolated
from .solvers import SolverCatalog
from .tools import TOOLS, run_tool, validate_parameters


class ExecutionService:
    def __init__(
        self, registry: RegistryService, *, solver_catalog: SolverCatalog | None = None
    ) -> None:
        self.registry = registry
        self.store = registry.store
        self.solver_catalog = solver_catalog

    def _snapshot(
        self, project_id: str, issue_id: str, *, exclude_task_id: str | None = None
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self.registry.task_context(
                project_id, issue_id, exclude_task_id=exclude_task_id
            )["execution_snapshot"],
        )

    def input_snapshot(
        self,
        project_id: str,
        issue_id: str,
        *,
        exclude_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Public bounded input view for brain planning and stale-call rechecks."""
        return self._snapshot(project_id, issue_id, exclude_task_id=exclude_task_id)

    def get_task(self, project_id: str, task_id: str) -> dict[str, Any]:
        self.registry.check_access(project_id)
        task = self.store.get_record(project_id, "tasks", task_id)
        if task is None:
            raise GraphIntegrityError("Task does not exist in this project")
        return task

    def list_tasks(self, project_id: str) -> list[dict[str, Any]]:
        self.registry.check_access(project_id)
        return self.store.list_records(project_id, "tasks")

    def _save(self, task: dict[str, Any], kind: str, **details: Any) -> None:
        self.store.put_record(
            task["project_id"], "tasks", task["task_id"], task, replace=True
        )
        self.registry._event(task["project_id"], task["task_id"], kind, **details)

    def propose_task(
        self,
        project_id: str,
        task_id: str,
        issue_id: str,
        tool: str,
        parameters: dict[str, Any],
        *,
        brain_plan: dict[str, Any] | None = None,
        expected_input_digest: str | None = None,
    ) -> dict[str, Any]:
        self.registry.check_access(project_id, write=True)
        text(task_id, "task_id")
        if len(task_id) > 512:
            raise ValueError("Task ID exceeds limit")
        validate_parameters(tool, parameters)
        if brain_plan is not None:
            from .contracts import validate_brain_plan

            validate_brain_plan(brain_plan)
        with self.store.transaction():
            snapshot = self._snapshot(project_id, issue_id)
            solver_binding = None
            fem_binding = None
            if tool == "external_solver":
                if self.solver_catalog is None:
                    raise ValueError(
                        "External solver requires a validated host catalogue"
                    )
                if brain_plan is None or "analysis" not in brain_plan:
                    raise ValueError(
                        "External solver requires Document AI reasoning planning and assessment"
                    )
                solver_binding = self.solver_catalog.validate_inputs(
                    parameters, snapshot
                )
                if brain_plan.get("solver_binding") != solver_binding:
                    raise ValueError(
                        "Solver definition changed during Document AI planning"
                    )
            elif tool == "fem_linear_static":
                if brain_plan is None or "analysis" not in brain_plan:
                    raise ValueError(
                        "FEM requires Document AI reasoning planning and assessment"
                    )
                fem_binding = fem_task_binding(parameters["job"], snapshot)
                if brain_plan.get("fem_binding") != fem_binding:
                    raise ValueError("FEM model or sources changed during planning")
            elif brain_plan is not None and "solver_binding" in brain_plan:
                raise ValueError("Solver bindings require the external solver tool")
            project_brief = self.registry.client_project_brief(project_id)
            if (brain_plan or {}).get("client_project_brief") != project_brief:
                raise ValueError("Task must bind to the current client project brief")
            mandate = self.registry.client_mandate(project_id, issue_id)
            if mandate is not None and (
                brain_plan is None or brain_plan.get("client_mandate") != mandate
            ):
                raise ValueError("Task must bind to the current client mandate")
            if brain_plan is not None and brain_plan.get("client_mandate") != mandate:
                raise ValueError(
                    "Brain client mandate does not match the retained original intent"
                )
            if tool == "validate_proposal_readiness":
                if brain_plan is None or brain_plan.get(
                    "submission_id"
                ) != parameters.get("submission_id"):
                    raise ValueError(
                        "Proposal readiness tasks must be structured by Document AI"
                    )
                submission_id = parameters["submission_id"]
                submission = self.registry.get_record(project_id, submission_id)
                if submission["node_type"] != NodeType.SUBMISSION:
                    raise GraphIntegrityError(
                        "Proposal task requires a same-project submission record"
                    )
                current = self.registry.submission_context(project_id, submission_id)
                snapshot_digest = snapshot.get("submission_context_digests", {}).get(
                    submission_id
                )
                binding = brain_plan.get("proposal_readiness", {})
                attrs = submission["attributes"]
                if (
                    brain_plan.get("submission_digest") != current["submission_digest"]
                    or snapshot_digest != current["submission_digest"]
                    or brain_plan.get("submission_digest") != snapshot_digest
                    or attrs.get("issue_id") != issue_id
                    or binding.get("candidate_revision_id")
                    != attrs.get("candidate_revision_id")
                    or binding.get("template_revision_id")
                    != attrs.get("template_revision_id")
                    or binding.get("source_requirement_ids")
                    != attrs.get("source_requirement_ids")
                    or binding.get("source_evidence_ids")
                    != attrs.get("source_evidence_ids")
                ):
                    raise ValueError(
                        "Submission sources changed or task binding does not match; create a fresh task"
                    )
            elif brain_plan is not None and any(
                key in brain_plan
                for key in ("submission_id", "submission_digest", "proposal_readiness")
            ):
                raise ValueError(
                    "Proposal submission bindings require the proposal readiness tool"
                )
            if (
                expected_input_digest is not None
                and digest(snapshot) != expected_input_digest
            ):
                raise ValueError(
                    "Issue sources changed during analysis; plan a fresh task"
                )
            linked_evidence = {
                n["node_id"]
                for n in snapshot["records"]
                if n["node_type"] == "evidence"
            }
            if brain_plan is not None:
                if "source_control" in brain_plan and brain_plan[
                    "source_control"
                ] != self.registry.source_control(project_id, sorted(linked_evidence)):
                    raise ValueError(
                        "Brain source control must match the retained project sources"
                    )
                if "evidence_review" in brain_plan and brain_plan[
                    "evidence_review"
                ] != self.registry.evidence_review_context(
                    project_id, sorted(linked_evidence)
                ):
                    raise ValueError(
                        "Brain evidence review must match the retained project sources"
                    )
                if set(brain_plan["evidence_ids"]) != linked_evidence or set(
                    brain_plan["requirement_ids"]
                ) != set(
                    snapshot.get(
                        "source_context_requirement_ids",
                        snapshot["issue"]["requirement_ids"],
                    )
                ):
                    raise ValueError(
                        "Brain task source references must match its project input snapshot"
                    )
                for lesson_id in brain_plan["lesson_ids"]:
                    lesson = self.registry.get_record(project_id, lesson_id)
                    if lesson["node_type"] != NodeType.LESSON or not lesson[
                        "attributes"
                    ].get("approved_by"):
                        raise ValueError(
                            "Brain context requires approved same-project lessons"
                        )
                if "analysis" in brain_plan:
                    lessons = brain_plan["analysis"]["lesson_context"]
                    if sorted(n.get("node_id", "") for n in lessons) != sorted(
                        brain_plan["lesson_ids"]
                    ):
                        raise ValueError(
                            "Analysis lesson context must match selected lessons"
                        )
                    if any(
                        self.registry.get_record(project_id, node["node_id"]) != node
                        for node in lessons
                    ):
                        raise ValueError(
                            "Analysis lessons changed or are outside the source context"
                        )
                if "fem_binding" in brain_plan and brain_plan[
                    "fem_binding"
                ] != fem_task_binding(parameters["job"], snapshot):
                    raise ValueError("FEM binding does not match same-project evidence")
            if tool == "compare_revision" and not set(parameters.values()).issubset(
                linked_evidence
            ):
                raise GraphIntegrityError(
                    "Comparison sources must be linked evidence in the same issue"
                )
            if len(str(snapshot)) > 500_000 or len(snapshot["records"]) > 200:
                raise ValueError("Issue context exceeds bounded execution limit")
            definition = {
                "task_id": task_id,
                "project_id": project_id,
                "issue_id": issue_id,
                "tool": tool,
                "tool_version": TOOLS[tool],
                "parameters": copy.deepcopy(parameters),
                "input_snapshot": snapshot,
                "input_digest": digest(snapshot),
                **({"solver_binding": solver_binding} if solver_binding else {}),
                **({"fem_binding": fem_binding} if fem_binding else {}),
                **(
                    {"brain_plan": copy.deepcopy(brain_plan)}
                    if brain_plan is not None
                    else {}
                ),
            }
            task_digest = digest(definition)
            previous = self.store.get_record(project_id, "tasks", task_id)
            if previous:
                if previous["task_digest"] != task_digest:
                    raise ValueError(
                        "Task ID already has different inputs; use a new task ID"
                    )
                return previous
            task = {
                **definition,
                "task_digest": task_digest,
                "state": "proposed",
                "proposed_by": self.registry.principal.actor_id,
                "proposed_at": timestamp(),
                "attempts": [],
                "authorization": None,
                "result_id": None,
            }
            node_id = "task:" + task_id
            self.store.add_subgraph(
                [
                    GraphNode(
                        node_id,
                        project_id,
                        NodeType.TASK,
                        tool,
                        {"task_id": task_id, "task_digest": task_digest},
                    )
                ],
                [
                    GraphEdge(
                        node_id + ":issue",
                        project_id,
                        node_id,
                        RelationshipType.RELATES_TO,
                        issue_id,
                    )
                ],
            )
            self._save(task, "task_proposed")
        return task

    def _fresh(self, task: dict[str, Any]) -> None:
        if task["tool"] == "external_solver":
            if self.solver_catalog is None or self.solver_catalog.binding(
                task["parameters"]["solver_id"]
            ) != task.get("solver_binding"):
                raise ValueError(
                    "External solver definition changed or is unavailable; create a fresh task"
                )
        if task["tool"] == "fem_linear_static":
            current = fem_task_binding(
                task["parameters"]["job"],
                self._snapshot(
                    task["project_id"],
                    task["issue_id"],
                    exclude_task_id=task["task_id"],
                ),
            )
            if current != task.get("fem_binding") or current != task.get(
                "brain_plan", {}
            ).get("fem_binding"):
                raise ValueError(
                    "FEM model or source binding changed; create a fresh task"
                )
        project_brief = self.registry.client_project_brief(task["project_id"])
        if (task.get("brain_plan") or {}).get("client_project_brief") != project_brief:
            raise ValueError("Client project brief changed; create a fresh task")
        mandate = self.registry.client_mandate(
            task["project_id"], task["issue_id"], exclude_task_id=task["task_id"]
        )
        if task.get("brain_plan", {}).get("client_mandate") != mandate:
            raise ValueError("Client mandate changed; create a new task")
        if task["tool_version"] != TOOLS.get(task["tool"]):
            raise ValueError("Tool version changed; create a new task")
        if task["input_digest"] != digest(
            self._snapshot(
                task["project_id"], task["issue_id"], exclude_task_id=task["task_id"]
            )
        ):
            raise ValueError(
                "Issue inputs changed; create a new task for authorisation"
            )
        if task["tool"] == "validate_proposal_readiness":
            current = self.registry.submission_context(
                task["project_id"], task["parameters"]["submission_id"]
            )
            if current["submission_digest"] != task.get("brain_plan", {}).get(
                "submission_digest"
            ):
                raise ValueError("Proposal source context changed; create a fresh task")

    def validate_task_freshness(self, task: dict) -> None:
        """Trusted host capability for Registry's delegated decision gate."""
        self.registry.check_access(task["project_id"])
        retained = self.get_task(task["project_id"], task["task_id"])
        if retained != task:
            raise ValueError("Task changed before the delegated decision")
        self._fresh(retained)

    def authorize_task(
        self, project_id: str, task_id: str, task_digest: str, rationale: str
    ) -> dict[str, Any]:
        self.registry.check_access(project_id, write=True, human=True)
        rationale = text(rationale, "rationale")
        with self.store.transaction():
            task = self.get_task(project_id, task_id)
            if (task.get("brain_plan") or {}).get("execution_class") == "routine":
                raise ValueError(
                    "Routine execution uses the human-approved policy route"
                )
            if task_digest != task["task_digest"]:
                raise ValueError("Task digest does not match")
            if task["state"] != "proposed":
                previous = task["authorization"] or {}
                if (
                    previous.get("actor_id") == self.registry.principal.actor_id
                    and previous.get("rationale") == rationale
                ):
                    return task
                raise ValueError("Task already decided")
            self._fresh(task)
            from engineering_registry.project_runs import require_run_plan_gate

            require_run_plan_gate(self.registry, project_id, task)
            if (task.get("brain_plan") or {}).get("alignment_review_required"):
                from engineering_registry.alignment import current_review

                review = current_review(
                    self.registry, project_id, task_id, phase="plan"
                )
                if (
                    not review
                    or review["attributes"]["status"] != "aligned"
                    or review["attributes"]["unknowns"]
                ):
                    raise ValueError(
                        "Human authorization blocked: Gate 1 requires a current aligned plan review with no unknowns"
                    )
            task["authorization"] = {
                "actor_id": self.registry.principal.actor_id,
                "authorized_at": timestamp(),
                "rationale": rationale,
                "task_digest": task_digest,
            }
            task["state"] = "queued"
            self._save(
                task, "task_authorized", task_digest=task_digest, rationale=rationale
            )
        return task

    def authorize_routine_task(
        self, project_id: str, task_id: str, policy_id: str, policy_digest: str
    ) -> dict[str, Any]:
        """Queue a bounded Routine task under its retained human-approved policy."""
        self.registry.check_access(project_id, write=True)
        if self.registry.principal.role != "agent":
            raise PermissionError("Routine policy route requires the AI Lead principal")
        from engineering_registry.execution_policy import current_execution_policy
        from engineering_registry.project_runs import require_run_plan_gate

        with self.store.transaction():
            task = self.get_task(project_id, task_id)
            if (
                task["state"] != "proposed"
                or task.get("brain_plan", {}).get("execution_class") != "routine"
            ):
                raise ValueError("Only proposed Routine tasks use policy authorisation")
            self._fresh(task)
            require_run_plan_gate(self.registry, project_id, task)
            policy = current_execution_policy(self.registry, project_id, policy_id)
            if (
                policy["policy_digest"] != policy_digest
                or task["brain_plan"].get("execution_policy_id") != policy_id
                or task["brain_plan"].get("execution_policy_digest") != policy_digest
            ):
                raise ValueError("Routine policy binding changed")
            task["authorization"] = {
                "authority": "human_approved_routine_policy",
                "actor_id": self.registry.principal.actor_id,
                "approved_by": policy["approved_by"],
                "policy_id": policy_id,
                "policy_digest": policy_digest,
                "task_digest": task["task_digest"],
                "authorized_at": timestamp(),
            }
            task["state"] = "queued"
            self._save(task, "routine_task_authorized", policy_id=policy_id)
            return task

    def _run_tool(self, task: dict[str, Any]) -> dict[str, Any]:
        if task["tool"] == "external_solver":
            if self.solver_catalog is None:
                raise ValueError("External solver catalogue is unavailable")
            return self.solver_catalog.execute(
                task["parameters"], task["input_snapshot"]
            )
        if task["tool"] == "fem_linear_static":
            return run_linear_static_job_isolated(task["parameters"]["job"])
        return run_tool(task["tool"], task["input_snapshot"], task["parameters"])

    def execute(
        self,
        project_id: str,
        task_id: str,
        *,
        before_execute: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        self.registry.check_access(project_id, write=True)
        with self.store.transaction():
            task = self.get_task(project_id, task_id)
            if task["state"] == "succeeded":
                self._fresh(task)
                from engineering_registry.project_runs import require_run_plan_gate

                require_run_plan_gate(
                    self.registry,
                    project_id,
                    task,
                    require_dependency_binding=True,
                )
                return self.registry.get_record(project_id, task["result_id"])
            if task["state"] != "queued" or not task["authorization"]:
                raise ValueError(
                    "Task needs queued human authorisation; failed/interrupted tasks require explicit recovery or retry"
                )
            self._fresh(task)
            from engineering_registry.project_runs import require_run_plan_gate

            require_run_plan_gate(
                self.registry,
                project_id,
                task,
                require_dependency_binding=True,
            )
            if (task.get("brain_plan") or {}).get("execution_class") == "routine" and (
                task["authorization"].get("authority")
                != "human_approved_routine_policy"
                or task["authorization"].get("policy_digest")
                != task["brain_plan"].get("execution_policy_digest")
            ):
                raise ValueError(
                    "Routine execution requires the exact approved policy authorisation"
                )
            if (task.get("brain_plan") or {}).get("alignment_review_required"):
                from engineering_registry.alignment import current_review

                review = current_review(
                    self.registry, project_id, task_id, phase="plan"
                )
                if (
                    not review
                    or review["attributes"]["status"] != "aligned"
                    or review["attributes"]["unknowns"]
                ):
                    raise ValueError(
                        "V2 execution blocked: current aligned client-perspective plan review required"
                    )
            task["state"] = "running"
            attempt_id = uuid.uuid4().hex
            task["attempts"].append(
                {
                    "attempt_id": attempt_id,
                    "state": "running",
                    "started_at": timestamp(),
                    "actor_id": self.registry.principal.actor_id,
                }
            )
            self._save(task, "task_started", attempt=len(task["attempts"]))
        try:
            if before_execute is not None:
                before_execute(copy.deepcopy(task))
            outputs = self._run_tool(task)
        except Exception as exc:
            with self.store.transaction():
                current = self.get_task(project_id, task_id)
                if (
                    current["state"] == "running"
                    and current["attempts"][-1].get("attempt_id") == attempt_id
                ):
                    current["state"] = "failed"
                    current["attempts"][-1].update(
                        state="failed",
                        finished_at=timestamp(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._save(current, "task_failed", attempt=len(current["attempts"]))
                return current
        with self.store.transaction():
            current = self.get_task(project_id, task_id)
            if (
                current["state"] != "running"
                or current["attempts"][-1].get("attempt_id") != attempt_id
            ):
                return current
            try:
                self._fresh(current)
            except ValueError as exc:
                current["state"] = "failed"
                current["attempts"][-1].update(
                    state="failed", finished_at=timestamp(), error=str(exc)
                )
                self._save(current, "task_failed")
                return current
            result_id = "result:" + digest(
                [project_id, task_id, len(current["attempts"])]
            )
            inputs = {
                "task_digest": current["task_digest"],
                "snapshot_digest": current["input_digest"],
                "parameters": current["parameters"],
                "snapshot": current["input_snapshot"],
                **(
                    {"brain_plan": current["brain_plan"]}
                    if "brain_plan" in current
                    else {}
                ),
            }
            evidence_ids = [
                n["node_id"]
                for n in current["input_snapshot"]["records"]
                if n["node_type"] == "evidence"
            ]
            result = self.registry.record_result(
                project_id,
                result_id,
                current["issue_id"],
                task_id,
                inputs,
                current["tool"],
                current["tool_version"],
                outputs,
                evidence_ids,
            )
            current["state"] = "succeeded"
            current["result_id"] = result_id
            current["attempts"][-1].update(
                state="succeeded",
                finished_at=timestamp(),
                result_id=result_id,
                output_digest=digest(result),
            )
            self._save(current, "task_succeeded", result_id=result_id)
            return result

    def cancel(self, project_id: str, task_id: str, rationale: str) -> dict[str, Any]:
        self.registry.check_access(project_id, write=True)
        rationale = text(rationale, "rationale")
        with self.store.transaction():
            task = self.get_task(project_id, task_id)
            if task["state"] not in {"proposed", "queued", "running"}:
                raise ValueError("Task cannot be cancelled from this state")
            if task["state"] == "running":
                task["attempts"][-1].update(state="cancelled", finished_at=timestamp())
            task["state"] = "cancelled"
            self._save(task, "task_cancelled", rationale=rationale)
        return task

    def recover(self, project_id: str, task_id: str, rationale: str) -> dict[str, Any]:
        self.registry.check_access(project_id, write=True, human=True)
        rationale = text(rationale, "rationale")
        with self.store.transaction():
            task = self.get_task(project_id, task_id)
            if task["state"] != "running":
                raise ValueError("Only an interrupted running task can be recovered")
            task["state"] = "failed"
            task["attempts"][-1].update(
                state="failed",
                finished_at=timestamp(),
                error="Interrupted; human recovery: " + rationale,
            )
            self._save(task, "task_recovered", rationale=rationale)
        return task

    def retry(self, project_id: str, task_id: str, rationale: str) -> dict[str, Any]:
        self.registry.check_access(project_id, write=True, human=True)
        rationale = text(rationale, "rationale")
        with self.store.transaction():
            task = self.get_task(project_id, task_id)
            if task["state"] != "failed" or not task["authorization"]:
                raise ValueError("Only an authorised failed task can be retried")
            self._fresh(task)
            task["state"] = "queued"
            self._save(task, "task_retry_authorized", rationale=rationale)
        return task
