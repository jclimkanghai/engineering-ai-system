"""Project-wide execution and human-controlled knowledge lifecycle."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import ExitStack
from copy import deepcopy

from engineering_registry.alignment import (
    current_review,
    review_criteria,
    validate_materiality,
)
from engineering_registry.delegation import string_list
from engineering_registry.knowledge_stores import OrganizationalKnowledgeService
from engineering_registry.project_runs import (
    DEPENDENCY_BINDINGS,
    dependency_binding_id,
    require_run_plan_gate,
)
from engineering_registry.service import (
    Principal,
    RegistryService,
    digest,
    text,
    timestamp,
)

from .dependencies import validate_dependencies
from .execution_mode import (
    ExecutionMode,
    require_demo_adapters,
    require_test_adapters,
    resolve_mode,
)
from .integration_assessment import (
    assessment_digest_payload,
    validate_integrated_assessment,
)
from .specialists import SpecialistSkillCatalog, validate_specialist_proposal


class ProjectRunService:
    def __init__(
        self,
        registry: RegistryService,
        brain,
        *,
        reviewer: Callable[[dict], dict] | None = None,
        integrator: Callable[[dict], dict] | None = None,
        generalizer: Callable[[dict], dict] | None = None,
        specialist_analysis: Callable[[str, dict], dict] | None = None,
        specialist_skills_dir=None,
        execution_mode: ExecutionMode | str = ExecutionMode.GOVERNED,
        organization_registry: RegistryService | None = None,
    ):
        self.registry = registry
        self.organization_registry = organization_registry
        self.brain = brain
        self.reviewer = reviewer or brain.alignment_reviewer
        self.integrator = integrator
        self.generalizer = generalizer
        self.specialist_analysis = specialist_analysis or brain.analysis
        self.specialist_skills = SpecialistSkillCatalog(specialist_skills_dir)
        self.execution_mode = resolve_mode(execution_mode)
        if self.execution_mode == ExecutionMode.DEMO:
            require_demo_adapters(brain.analysis, self.reviewer)
        if self.execution_mode == ExecutionMode.TEST:
            require_test_adapters(brain.analysis, self.reviewer)
        self.reviewer_registry = RegistryService(
            registry.store,
            Principal(
                "alignment-reviewer:" + registry.principal.actor_id,
                registry.principal.project_ids,
                "ai_reviewer",
            ),
        )

    def _run(self, project: str, run_id: str) -> dict:
        self.registry.check_access(project)
        run = self.registry.get_control_record(project, "project_runs", run_id)
        if not run:
            raise ValueError("Unknown project run")
        if run.get("execution_mode", "GOVERNED") != self.execution_mode.value:
            raise ValueError(
                "Project run execution mode changed or does not match host mode"
            )
        if digest(run["plan"]) != run["plan_digest"]:
            raise ValueError("Project-run plan changed")
        return run

    def get_status(self, project: str, run_id: str) -> dict:
        """Return the current run and its retained gate, integration and outcome records."""
        run = self._run(project, run_id)
        specialist_request = self._specialist_approval_request(run)
        specialist_decision = self._specialist_plan_decision(project, run)
        return {
            "run": deepcopy(run),
            "specialist_approval_request": specialist_request,
            "specialist_plan_decision": specialist_decision,
            "gate1": deepcopy(
                self.registry.get_control_record(
                    project, "project_run_reviews", run.get("gate1_review_id", "")
                )
            ),
            "integration": deepcopy(
                self.registry.get_control_record(
                    project, "project_run_integrations", run.get("integration_id", "")
                )
            ),
            "gate2": deepcopy(
                self.registry.get_control_record(
                    project, "project_run_reviews", run.get("gate2_review_id", "")
                )
            ),
            "specialist_outputs": self._specialist_output_summaries(project, run),
            "outcome": deepcopy(
                self.registry.get_control_record(
                    project, "project_run_outcomes", run.get("outcome_id", "")
                )
            ),
        }

    def get_gate_status(self, project: str, run_id: str, phase: str) -> dict:
        """Read Reviewer status against the current canonical context; never review."""
        if phase not in {"plan", "outcome"}:
            raise ValueError("Unknown run gate phase")
        run = self._run(project, run_id)
        if run.get("execution_class") == "routine":
            return {
                "status": "policy-bound",
                "current": False,
                "review": None,
                "reason": "Routine gate exemption requires a current human-approved policy",
            }
        pointer = "gate1_review_id" if phase == "plan" else "gate2_review_id"
        review = self.registry.get_control_record(
            project, "project_run_reviews", run.get(pointer) or ""
        )
        if not review:
            return {
                "status": "pending",
                "current": False,
                "review": None,
                "reason": "No retained review",
            }
        try:
            current = review.get("request_digest") == digest(
                self._context(project, run, phase)
            )
        except (ValueError, KeyError) as exc:
            return {
                "status": "stale",
                "current": False,
                "review": deepcopy(review),
                "reason": str(exc),
            }
        status = review.get("report", {}).get("status", "UNKNOWN")
        return {
            "status": status if current else "stale",
            "current": current,
            "review": deepcopy(review),
            "reason": "Current review context" if current else "Review context changed",
        }

    @staticmethod
    def _specialist_approval_request(run: dict) -> dict:
        specialists = deepcopy(run["plan"].get("discipline_specialists", []))
        return {
            "required": len(specialists) > 3,
            "specialist_count": len(specialists),
            "threshold": 3,
            "plan_digest": run["plan_digest"],
            "approval_route": "critical_plan_approval"
            if run.get("execution_class") == "critical"
            else "human_specialist_plan_decision",
            "specialists": specialists,
        }

    def _specialist_plan_decision(self, project: str, run: dict) -> dict | None:
        if len(run["plan"].get("discipline_specialists", [])) <= 3:
            return None
        if run.get("execution_class") == "critical":
            approval_id = run.get("human_plan_approval_id")
            approval = (
                self.registry.get_control_record(
                    project, "project_run_approvals", approval_id
                )
                if approval_id
                else None
            )
            if approval and approval.get("specialist_assignment_digest"):
                return deepcopy(approval)
            return None
        decision_id = run.get("specialist_plan_decision_id")
        return deepcopy(
            self.registry.get_control_record(
                project, "project_run_specialist_decisions", decision_id
            )
            if decision_id
            else None
        )

    def _current_task_artifacts(
        self, project: str, task: dict
    ) -> tuple[dict | None, dict | None]:
        result = (
            self.registry.get_record(project, task["result_id"])
            if task.get("result_id")
            else None
        )
        if not result:
            return None, None
        result_digest = digest(result)
        assessments = [
            item
            for item in self.registry.list_records(project, "assessment")
            if item["attributes"].get("result_id") == result["node_id"]
            and item["attributes"].get("result_digest") == result_digest
            and item["attributes"].get("task_digest") == task["task_digest"]
        ]
        assessments.sort(key=lambda item: item["attributes"].get("produced_at", ""))
        return result, assessments[-1] if assessments else None

    def bind_task_dependencies(
        self, project: str, run_id: str, task_id: str
    ) -> dict | None:
        """Freeze current predecessor result/assessment digests before execution."""
        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        bound = next(
            (item for item in run["plan"]["tasks"] if item["task_id"] == task_id),
            None,
        )
        if not bound:
            raise ValueError("Task is not part of this project run")
        predecessor_ids = sorted(bound.get("depends_on", []))
        if not predecessor_ids:
            return None

        scheduled = next(
            item
            for item in self.get_schedule(project, run_id)["tasks"]
            if item["task_id"] == task_id
        )
        if scheduled["state"] != "ready":
            reasons = ", ".join(scheduled.get("block_reasons", [])) or "not ready"
            raise ValueError("Project-run dependency task is not ready: " + reasons)

        dependencies = []
        for predecessor_id in predecessor_ids:
            predecessor = self.brain.execution.get_task(project, predecessor_id)
            result, assessment = self._current_task_artifacts(project, predecessor)
            if predecessor["state"] != "succeeded" or not result or not assessment:
                raise ValueError(
                    "Project-run dependency predecessor is incomplete: "
                    + predecessor_id
                )
            dependencies.append(
                {
                    "task_id": predecessor_id,
                    "task_digest": predecessor["task_digest"],
                    "result_id": result["node_id"],
                    "result_digest": digest(result),
                    "assessment_id": assessment["node_id"],
                    "assessment_digest": digest(assessment),
                }
            )

        binding_id = dependency_binding_id(run_id, task_id)
        payload = {
            "schema_version": 1,
            "binding_id": binding_id,
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "task_id": task_id,
            "task_digest": bound["task_digest"],
            "dependencies": dependencies,
        }
        binding = {**payload, "binding_digest": digest(payload)}
        with self.registry.store.transaction():
            existing = self.registry.get_control_record(
                project, DEPENDENCY_BINDINGS, binding_id
            )
            if existing:
                if existing != binding:
                    raise ValueError(
                        "Project-run dependency binding changed; create a fresh task and run"
                    )
                return deepcopy(existing)
            self.registry.store.put_record(
                project, DEPENDENCY_BINDINGS, binding_id, binding
            )
        return deepcopy(binding)

    def get_schedule(self, project: str, run_id: str) -> dict:
        """Project the current execution schedule without changing retained state."""
        run = self._run(project, run_id)
        plan_tasks = run["plan"]["tasks"]
        dependencies = {
            item["task_id"]: item.get("depends_on", []) for item in plan_tasks
        }
        projections: dict[str, dict] = {}

        def latest_decision_disposition(result_id: str | None) -> str | None:
            if not result_id:
                return None
            decisions = [
                item
                for item in self.registry.list_records(project, "decision")
                if item["attributes"].get("result_id") == result_id
                and item["attributes"].get("disposition")
                in {"accept", "hold", "rework", "reject"}
            ]
            decisions.sort(key=lambda item: item["attributes"].get("proposed_at", ""))
            return decisions[-1]["attributes"]["disposition"] if decisions else None

        def project_task(task_id: str) -> dict:
            if task_id in projections:
                return projections[task_id]
            task = self.brain.execution.get_task(project, task_id)
            base = {
                "task_id": task_id,
                "depends_on": sorted(dependencies.get(task_id, [])),
                "blocking_task_ids": [],
                "block_reasons": [],
                "task_digest": task["task_digest"],
            }
            state = task["state"]
            freshness_error = None
            if state == "succeeded":
                try:
                    self.brain.execution.validate_task_freshness(task)
                except ValueError as error:
                    freshness_error = str(error)
            if freshness_error:
                projection = {
                    **base,
                    "state": "stale",
                    "block_reasons": [freshness_error],
                }
            elif state in {"failed", "cancelled"}:
                projection = {**base, "state": "failed", "block_reasons": [state]}
            elif state == "running":
                projection = {**base, "state": "running"}
            elif state == "succeeded":
                result, assessment = self._current_task_artifacts(project, task)
                if not result or not assessment:
                    projection = {
                        **base,
                        "state": "stale",
                        "block_reasons": ["current_result_or_assessment_missing"],
                    }
                else:
                    disposition = latest_decision_disposition(result["node_id"])
                    blockers = [
                        predecessor
                        for predecessor in dependencies.get(task_id, [])
                        if project_task(predecessor)["state"] != "completed"
                    ]
                    if disposition in {"hold", "rework", "reject"}:
                        projection = {
                            **base,
                            "state": "failed",
                            "block_reasons": ["predecessor_" + disposition],
                        }
                    elif blockers:
                        projection = {
                            **base,
                            "state": "stale",
                            "blocking_task_ids": sorted(blockers),
                            "block_reasons": ["dependency_no_longer_complete"],
                        }
                    else:
                        projection = {
                            **base,
                            "state": "completed",
                            "result_id": result["node_id"],
                            "result_digest": digest(result),
                            "assessment_id": assessment["node_id"],
                            "assessment_digest": digest(assessment),
                        }
            else:
                blockers = [
                    predecessor
                    for predecessor in dependencies.get(task_id, [])
                    if project_task(predecessor)["state"] != "completed"
                ]
                if blockers:
                    projection = {
                        **base,
                        "state": "blocked",
                        "blocking_task_ids": sorted(blockers),
                        "block_reasons": ["dependency_incomplete"],
                    }
                elif state != "queued":
                    projection = {
                        **base,
                        "state": "blocked",
                        "block_reasons": ["task_authorization_required"],
                    }
                else:
                    projection = {**base, "state": "ready"}
            projections[task_id] = projection
            return projection

        for task_id in sorted(dependencies):
            project_task(task_id)
        tasks = [projections[item["task_id"]] for item in plan_tasks]
        return {
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "tasks": deepcopy(tasks),
            "ready_task_ids": sorted(
                item["task_id"] for item in tasks if item["state"] == "ready"
            ),
            "blocked_task_ids": sorted(
                item["task_id"] for item in tasks if item["state"] == "blocked"
            ),
            "completed_task_ids": sorted(
                item["task_id"] for item in tasks if item["state"] == "completed"
            ),
            "failed_task_ids": sorted(
                item["task_id"] for item in tasks if item["state"] == "failed"
            ),
            "stale_task_ids": sorted(
                item["task_id"] for item in tasks if item["state"] == "stale"
            ),
        }

    def execute_task(self, project: str, run_id: str, task_id: str) -> dict:
        """Execute only a run member after all class-specific pre-execution controls."""
        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        if task_id not in run["task_ids"]:
            raise ValueError("Task is not part of this project run")
        from engineering_registry.project_runs import require_run_plan_gate

        require_run_plan_gate(
            self.registry,
            project,
            self.brain.execution.get_task(project, task_id),
        )
        dependency_binding = self.bind_task_dependencies(project, run_id, task_id)
        task = self.brain.execution.get_task(project, task_id)
        assignment = next(
            item.get("specialist_assignment")
            for item in run["plan"]["tasks"]
            if item["task_id"] == task_id
        )
        if assignment is not None:
            self._assert_specialist_skill_binding(assignment, task)
        specialist_outputs = []

        def run_specialist(current_task):
            specialist_outputs.append(
                self._run_specialist(
                    project, run, current_task, assignment, dependency_binding
                )
            )

        before_execute = run_specialist if assignment is not None else None
        outcome = self.brain.execute_and_assess(
            project, task_id, before_execute=before_execute
        )
        specialist_proposal = (
            specialist_outputs[-1]
            if specialist_outputs
            else self._current_specialist_proposal(project, run, task)
            if assignment is not None
            else None
        )
        if specialist_proposal is not None:
            outcome = {**outcome, "specialist_proposal": specialist_proposal}
        return outcome

    def _run_specialist(
        self,
        project: str,
        run: dict,
        task: dict,
        assignment: dict,
        dependency_binding: dict | None,
    ) -> dict:
        if self.specialist_analysis is None:
            raise ValueError("Discipline specialist analysis adapter is unavailable")
        skill = self._assert_specialist_skill_binding(assignment, task)
        current = self._current_specialist_proposal(project, run, task)
        if current:
            return current
        allowed_source_ids = {
            record["node_id"]
            for record in task["input_snapshot"].get("records", [])
            if record.get("node_type") == "evidence"
        }
        context = {
            "schema_version": 1,
            "run_id": run["run_id"],
            "plan_digest": run["plan_digest"],
            "task_id": task["task_id"],
            "task_digest": task["task_digest"],
            "input_digest": task["input_digest"],
            "assignment": deepcopy(assignment),
            "skill": skill,
            "tool": {
                "name": task["tool"],
                "version": task["tool_version"],
                "parameters": deepcopy(task["parameters"]),
            },
            "source_snapshot": deepcopy(task["input_snapshot"]),
            "dependency_binding": deepcopy(dependency_binding),
            "limits": [
                "Return a proposal only; do not claim engineering acceptance.",
                "Cite only source IDs in this input snapshot.",
                "The host executes only the exact bound V2 tool and parameters.",
                "State assumptions and unknowns explicitly.",
            ],
        }
        proposal = validate_specialist_proposal(
            self.specialist_analysis("discipline_specialist", context),
            allowed_source_ids,
        )
        payload = {
            "schema_version": 1,
            "output_id": "specialist_output:" + uuid.uuid4().hex,
            "run_id": run["run_id"],
            "plan_digest": run["plan_digest"],
            "task_id": task["task_id"],
            "task_digest": task["task_digest"],
            "input_digest": task["input_digest"],
            "specialist_id": assignment["specialist_id"],
            "discipline": assignment["discipline"],
            "skill_id": skill["skill_id"],
            "skill_digest": skill["skill_digest"],
            "tool": {"name": task["tool"], "version": task["tool_version"]},
            "proposal": proposal,
            "proposal_digest": digest(proposal),
            "produced_at": timestamp(),
        }
        payload["record_digest"] = digest(payload)
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project,
                "project_run_specialist_outputs",
                payload["output_id"],
                payload,
            )
        return deepcopy(payload)

    def _current_specialist_proposal(
        self, project: str, run: dict, task: dict
    ) -> dict | None:
        bound = next(
            item for item in run["plan"]["tasks"] if item["task_id"] == task["task_id"]
        )
        assignment = bound.get("specialist_assignment")
        current_skill_digest = (
            self.specialist_skills.load_for_assignment(
                assignment["skill"], assignment["discipline"], bound["tool"]
            )["skill_digest"]
            if assignment
            else None
        )
        outputs = [
            item
            for item in self.registry.list_control_records(
                project, "project_run_specialist_outputs"
            )
            if item.get("run_id") == run["run_id"]
            and item.get("task_id") == task["task_id"]
            and item.get("plan_digest") == run["plan_digest"]
            and item.get("task_digest") == task["task_digest"]
            and item.get("input_digest") == task["input_digest"]
            and item.get("skill_digest") == current_skill_digest
            and item.get("proposal_digest") == digest(item.get("proposal"))
            and item.get("record_digest")
            == digest(
                {key: value for key, value in item.items() if key != "record_digest"}
            )
        ]
        outputs.sort(key=lambda item: item.get("produced_at", ""))
        return deepcopy(outputs[-1]) if outputs else None

    def _assert_specialist_skill_binding(self, assignment: dict, task: dict) -> dict:
        skill = self.specialist_skills.load_for_assignment(
            assignment["skill"], assignment["discipline"], task["tool"]
        )
        if skill["skill_digest"] != assignment.get("skill_digest"):
            raise ValueError(
                "Discipline specialist skill changed after the plan was reviewed; prepare a new plan"
            )
        if (
            self.execution_mode != ExecutionMode.TEST
            and skill["validation_status"] != "validated"
        ):
            raise ValueError(
                "Discipline specialist skill requires discipline-owner validation"
            )
        return skill

    def _specialist_output_summaries(self, project: str, run: dict) -> list[dict]:
        latest = {}
        for item in self.registry.list_control_records(
            project, "project_run_specialist_outputs"
        ):
            if (
                item.get("run_id") == run["run_id"]
                and item.get("plan_digest") == run["plan_digest"]
                and item.get("task_id") in run["task_ids"]
            ):
                previous = latest.get(item["task_id"])
                if previous is None or item.get("produced_at", "") > previous.get(
                    "produced_at", ""
                ):
                    latest[item["task_id"]] = item
        return [
            {
                key: deepcopy(item[key])
                for key in (
                    "output_id",
                    "run_id",
                    "task_id",
                    "specialist_id",
                    "discipline",
                    "skill_id",
                    "skill_digest",
                    "tool",
                    "proposal_digest",
                    "produced_at",
                )
            }
            for item in (latest[key] for key in sorted(latest))
        ]

    def get_specialist_output(self, project: str, output_id: str) -> dict:
        """Read one retained advisory proposal with its source and skill provenance."""
        record = self.registry.get_control_record(
            project, "project_run_specialist_outputs", output_id
        )
        if not record:
            raise ValueError("Unknown specialist output")
        if record.get("record_digest") != digest(
            {key: value for key, value in record.items() if key != "record_digest"}
        ):
            raise ValueError("Specialist output integrity check failed")
        return deepcopy(record)

    def propose(
        self,
        project: str,
        run_id: str,
        issue_id: str,
        tasks: list[dict],
        *,
        execution_class: str = "engineering",
        policy_id: str | None = None,
    ) -> dict:
        from .run_classification import classify_run

        self.registry.check_access(project, write=True)
        text(run_id, "run_id")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
            raise ValueError("Project run requires 1 to 20 bounded V2 tasks")
        ids = [item.get("task_id") for item in tasks if isinstance(item, dict)]
        if len(ids) != len(tasks) or len(set(ids)) != len(ids):
            raise ValueError("Project-run task IDs must be unique")
        specialist_skill_bindings = {}
        for item in tasks:
            required = {"task_id", "tool", "parameters"}
            optional = {"depends_on", "specialist_assignment"}
            if not required.issubset(item) or not set(item).issubset(
                required | optional
            ):
                raise ValueError("Invalid bounded V2 task specification")
            if (
                "specialist_assignment" in item
                and item["specialist_assignment"] is not None
            ):
                self._validate_specialist_assignment(item["specialist_assignment"])
                skill = self.specialist_skills.load_for_assignment(
                    item["specialist_assignment"]["skill"],
                    item["specialist_assignment"]["discipline"],
                    item["tool"],
                )
                if (
                    self.execution_mode != ExecutionMode.TEST
                    and skill["validation_status"] != "validated"
                ):
                    raise ValueError(
                        "Discipline specialist skill requires discipline-owner validation"
                    )
                specialist_skill_bindings[item["task_id"]] = skill
        dependencies = validate_dependencies(tasks)
        with self.registry.store.transaction():
            if self.registry.get_control_record(project, "project_runs", run_id):
                raise ValueError("Project run already exists")
            if any(
                set(ids) & set(run["task_ids"])
                for run in self.registry.list_control_records(project, "project_runs")
            ):
                raise ValueError("A V2 task already belongs to a project run")
            if any(
                self.registry.get_control_record(project, "tasks", task_id)
                for task_id in ids
            ):
                raise ValueError("Project run requires new, unexecuted V2 task IDs")
            classification = classify_run(
                self.registry, project, issue_id, execution_class, tasks, policy_id
            )
            preparing = {
                "run_id": run_id,
                "issue_id": issue_id,
                "task_ids": ids,
                "state": "preparing",
                "execution_class": classification["execution_class"],
                "policy_id": classification["policy_id"],
                "policy_digest": classification["policy_digest"],
            }
            self.registry.store.put_record(project, "project_runs", run_id, preparing)
            retained = []
            for item in tasks:
                task = self.brain.structure_task(
                    project,
                    item["task_id"],
                    issue_id,
                    item["tool"],
                    item["parameters"],
                    run_id=run_id,
                )
                specialist_assignment = self._normalise_specialist_assignment(
                    item.get("specialist_assignment")
                )
                if specialist_assignment is not None:
                    skill = specialist_skill_bindings[item["task_id"]]
                    manifest = skill["manifest"]
                    specialist_assignment.update(
                        {
                            "skill_digest": skill["skill_digest"],
                            "validation_status": skill["validation_status"],
                            "validated_by": manifest["validated_by"],
                            "validation_reference": manifest["validation_reference"],
                            "allowed_tools": deepcopy(manifest["allowed_tools"]),
                        }
                    )
                retained.append(
                    {
                        "task_id": task["task_id"],
                        "task_digest": task["task_digest"],
                        "brain_plan": task["brain_plan"],
                        "input_digest": task["input_digest"],
                        "depends_on": dependencies[item["task_id"]],
                        "tool": task["tool"],
                        "tool_version": task["tool_version"],
                        "tool_parameters": deepcopy(task["parameters"]),
                        "specialist_assignment": specialist_assignment,
                    }
                )
            post_classification = classify_run(
                self.registry,
                project,
                issue_id,
                execution_class,
                tasks,
                policy_id,
                plans=[item["brain_plan"] for item in retained],
            )
            if (
                post_classification["execution_class"]
                != classification["execution_class"]
            ):
                raise ValueError(
                    "Routine class changed after Lead planning; prepare a new classified run"
                )
            classification = post_classification
            first = retained[0]["brain_plan"]
            if any(
                t["input_digest"] != retained[0]["input_digest"]
                or t["brain_plan"]["client_mandate"] != first["client_mandate"]
                or t["brain_plan"]["client_project_brief"]
                != first["client_project_brief"]
                for t in retained
            ):
                raise ValueError(
                    "Project-run tasks must share one current project context"
                )
            plan = {
                "run_id": run_id,
                "issue_id": issue_id,
                "tasks": retained,
                "discipline_specialists": self._discipline_specialists(retained),
                "client_mandate": first["client_mandate"],
                "client_project_brief": first["client_project_brief"],
                "input_digest": retained[0]["input_digest"],
                "organizational_lesson_ids": sorted(
                    {
                        x
                        for t in retained
                        for x in t["brain_plan"].get("organizational_lesson_ids", [])
                    }
                ),
                "knowledge_conflicts": [
                    x
                    for t in retained
                    for x in t["brain_plan"].get("knowledge_conflicts", [])
                ],
            }
            run = {
                "run_id": run_id,
                "issue_id": issue_id,
                "task_ids": ids,
                "plan": plan,
                "plan_digest": digest(plan),
                "gate1_review_id": None,
                "human_plan_approval_id": None,
                "integration_id": None,
                "gate2_review_id": None,
                "outcome_id": None,
                "created_by": self.registry.principal.actor_id,
                "created_at": timestamp(),
                "execution_class": classification["execution_class"],
                "classification_status": classification["status"],
                "classification_reasons": classification["reasons"],
                "classification_source_digest": classification["source_digest"],
                "policy_id": classification["policy_id"],
                "policy_digest": classification["policy_digest"],
                "schema_version": 2,
                "execution_mode": self.execution_mode.value,
            }
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
            return deepcopy(run)

    @staticmethod
    def _validate_specialist_assignment(assignment: dict) -> None:
        fields = {
            "specialist_id",
            "discipline",
            "skill",
            "task_scope",
            "deliverable",
        }
        if not isinstance(assignment, dict) or set(assignment) != fields:
            raise ValueError("Invalid discipline specialist assignment")
        try:
            for key, limit in (
                ("specialist_id", 128),
                ("discipline", 120),
                ("skill", 200),
                ("task_scope", 2000),
                ("deliverable", 2000),
            ):
                value = text(assignment[key], key)
                if len(value) > limit:
                    raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid discipline specialist assignment") from exc

    @classmethod
    def _normalise_specialist_assignment(cls, assignment: dict | None) -> dict | None:
        if assignment is None:
            return None
        cls._validate_specialist_assignment(assignment)
        return {key: text(value, key) for key, value in assignment.items()}

    @staticmethod
    def _discipline_specialists(tasks: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for task in tasks:
            assignment = task.get("specialist_assignment")
            if not assignment:
                continue
            specialist_id = assignment["specialist_id"]
            specialist = grouped.setdefault(
                specialist_id,
                {
                    "specialist_id": specialist_id,
                    "discipline": assignment["discipline"],
                    "assignments": [],
                },
            )
            if specialist["discipline"] != assignment["discipline"]:
                raise ValueError(
                    "A discipline specialist ID cannot be assigned multiple disciplines"
                )
            specialist["assignments"].append(
                {
                    "task_id": task["task_id"],
                    "scope": assignment["task_scope"],
                    "skill": assignment["skill"],
                    "skill_digest": assignment["skill_digest"],
                    "skill_validation_status": assignment["validation_status"],
                    "skill_validated_by": assignment["validated_by"],
                    "skill_validation_reference": assignment["validation_reference"],
                    "allowed_skill_tools": deepcopy(assignment["allowed_tools"]),
                    "tools": [{"name": task["tool"], "version": task["tool_version"]}],
                    "source_access": {
                        "evidence_ids": sorted(
                            task["brain_plan"].get("evidence_ids", [])
                        ),
                        "requirement_ids": sorted(
                            task["brain_plan"].get("requirement_ids", [])
                        ),
                    },
                    "depends_on": task["depends_on"],
                    "deliverable": assignment["deliverable"],
                }
            )
        return [grouped[key] for key in sorted(grouped)]

    def _context(self, project: str, run: dict, phase: str) -> dict:
        if phase not in {"plan", "outcome"}:
            raise ValueError("Unknown run review phase")
        plan = run["plan"]
        if (
            self.registry.client_project_brief(project) != plan["client_project_brief"]
            or self.registry.client_mandate(project, run["issue_id"])
            != plan["client_mandate"]
        ):
            raise ValueError("Client brief or mandate changed; prepare a fresh run")
        for bound in plan["tasks"]:
            task = self.brain.execution.get_task(project, bound["task_id"])
            self.brain.execution._fresh(task)
            if task["task_digest"] != bound["task_digest"]:
                raise ValueError("Project-run task changed")
        context = {
            "phase": phase,
            "project_id": project,
            "client_mandate": plan["client_mandate"],
            "client_project_brief": plan["client_project_brief"],
            "task": {
                "task_id": run["run_id"],
                "task_digest": run["plan_digest"],
                "issue_id": run["issue_id"],
            },
            "brain_plan": plan,
            "source_snapshot": self.registry.task_context(project, run["issue_id"])[
                "execution_snapshot"
            ],
        }
        if digest(context["source_snapshot"]) != plan["input_digest"]:
            raise ValueError("Project-run source context changed")
        if phase == "outcome":
            integration = self.registry.get_control_record(
                project, "project_run_integrations", run["integration_id"]
            )
            if not integration:
                raise ValueError("Run Gate 2 requires Lead integration")
            if integration.get("integration_digest") != digest(
                {
                    key: value
                    for key, value in integration.items()
                    if key != "integration_digest"
                }
            ) or integration.get("integration_digest") != run.get("integration_digest"):
                raise ValueError("Run integration changed; prepare a fresh integration")
            current_inputs = self._consolidation_context(project, run)
            if digest(current_inputs) != integration.get("consolidation_input_digest"):
                raise ValueError(
                    "V2 output, assessment or source changed after integration"
                )
            assessment = self.registry.get_control_record(
                project, "project_run_assessments", integration.get("assessment_id", "")
            )
            if (
                not assessment
                or assessment.get("assessment_digest")
                != integration.get("assessment_digest")
                or digest(assessment_digest_payload(assessment))
                != integration.get("assessment_digest")
                or assessment.get("source_digest")
                != integration.get("consolidation_input_digest")
                or run.get("assessment_id") != integration.get("assessment_id")
                or run.get("assessment_digest") != integration.get("assessment_digest")
            ):
                raise ValueError("Integrated run assessment is missing or stale")
            context.update(
                execution_result=integration,
                document_ai_assessment=assessment,
                integrated_run_assessment=assessment,
            )
        return context

    def review(self, project: str, run_id: str, phase: str) -> dict:
        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        if run.get("execution_class") == "routine":
            raise ValueError("Routine runs do not use AI Reviewer gates")
        pointer = "gate1_review_id" if phase == "plan" else "gate2_review_id"
        if phase == "plan" and run["integration_id"]:
            raise ValueError("Run Gate 1 must precede execution")
        if phase == "plan" and any(
            self.brain.execution.get_task(project, task_id)["state"] != "proposed"
            for task_id in run["task_ids"]
        ):
            raise ValueError("Run Gate 1 must precede task authorisation")
        context = self._context(project, run, phase)
        if run[pointer]:
            previous = self.registry.get_control_record(
                project, "project_run_reviews", run[pointer]
            )
            if previous and previous["request_digest"] == digest(context):
                return previous
        if self.reviewer is None:
            raise ValueError("Run reviewer unavailable")
        report = self.reviewer(deepcopy(context))
        self._validate_review(project, run, context, report)
        record = {
            "review_id": "project_run_review:"
            + digest({"context": context, "report": report}),
            "run_id": run_id,
            "phase": phase,
            "plan_digest": run["plan_digest"],
            "integration_id": run["integration_id"] if phase == "outcome" else None,
            "integrated_assessment_id": (
                context["integrated_run_assessment"]["assessment_id"]
                if phase == "outcome"
                else None
            ),
            "integrated_assessment_digest": (
                context["integrated_run_assessment"]["assessment_digest"]
                if phase == "outcome"
                else None
            ),
            "request_digest": digest(context),
            "report": deepcopy(report),
            "reviewed_by": "alignment-reviewer:" + self.registry.principal.actor_id,
            "reviewed_at": timestamp(),
        }
        with self.registry.store.transaction():
            self.reviewer_registry.check_access(project, write=True)
            self.reviewer_registry.store.put_record(
                project, "project_run_reviews", record["review_id"], record
            )
            run[pointer] = record["review_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return record

    def approve_critical_plan(
        self,
        principal: Principal,
        project: str,
        run_id: str,
        plan_digest: str,
        rationale: str,
    ) -> dict:
        human = RegistryService(self.registry.store, principal)
        human.check_access(project, write=True, human=True)
        run = self._run(project, run_id)
        if (
            run.get("execution_class") != "critical"
            or run.get("classification_status") == "hold"
        ):
            raise ValueError("Only a ready Critical run permits human plan approval")
        if run["plan_digest"] != plan_digest or run.get("integration_id"):
            raise ValueError("Critical plan changed or execution already started")
        context = self._context(project, run, "plan")
        review_id = run.get("gate1_review_id")
        review = (
            self.registry.get_control_record(project, "project_run_reviews", review_id)
            if review_id
            else None
        )
        if (
            not review
            or review["request_digest"] != digest(context)
            or review["report"]["status"] != "aligned"
            or review["report"]["unknowns"]
        ):
            raise ValueError("Critical plan needs a current aligned Gate 1")
        if any(
            self.brain.execution.get_task(project, task_id)["state"] != "proposed"
            for task_id in run["task_ids"]
        ):
            raise ValueError("Critical human plan approval must precede execution")
        text(rationale, "rationale")
        approval = {
            "approval_id": "project_run_approval:"
            + digest(
                {
                    "run": run_id,
                    "plan": plan_digest,
                    "review": review_id,
                    "actor": principal.actor_id,
                }
            ),
            "run_id": run_id,
            "plan_digest": plan_digest,
            "gate1_review_id": review_id,
            "approved_by": principal.actor_id,
            "approved_at": timestamp(),
            "rationale": rationale,
            "specialist_assignment_digest": digest(
                run["plan"].get("discipline_specialists", [])
            ),
        }
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project, "project_run_approvals", approval["approval_id"], approval
            )
            run["human_plan_approval_id"] = approval["approval_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return approval

    def decide_specialist_plan(
        self,
        principal: Principal,
        project: str,
        run_id: str,
        plan_digest: str,
        disposition: str,
        rationale: str,
        conditions: list[str] | None = None,
    ) -> dict:
        """Record a human decision for a plan using more than three discipline specialists."""
        human = RegistryService(self.registry.store, principal)
        human.check_access(project, write=True, human=True)
        run = self._run(project, run_id)
        request = self._specialist_approval_request(run)
        if not request["required"]:
            raise ValueError(
                "This plan does not exceed the specialist approval threshold"
            )
        if run.get("execution_class") == "critical":
            raise ValueError("Critical plans use the Critical human plan approval gate")
        if run["plan_digest"] != plan_digest:
            raise ValueError("Specialist plan digest does not match the current plan")
        if run.get("integration_id") or any(
            self.brain.execution.get_task(project, task_id)["state"] != "proposed"
            for task_id in run["task_ids"]
        ):
            raise ValueError("Specialist plan decision must precede task authorization")
        if disposition not in {
            "approve",
            "approve_with_conditions",
            "hold",
            "revise_and_resubmit",
        }:
            raise ValueError("Invalid specialist plan decision")
        rationale = text(rationale, "rationale")
        conditions = conditions or []
        if disposition == "approve_with_conditions":
            if not isinstance(conditions, list) or not conditions:
                raise ValueError("Conditional approval requires explicit conditions")
            conditions = [text(item, "condition") for item in conditions]
            if len(set(conditions)) != len(conditions) or any(
                len(item) > 1000 for item in conditions
            ):
                raise ValueError("Invalid conditional approval conditions")
        elif conditions:
            raise ValueError("Conditions are only valid with conditional approval")
        previous = self._specialist_plan_decision(project, run)
        if (
            previous
            and previous.get("disposition") == "approve_with_conditions"
            and disposition in {"approve", "approve_with_conditions"}
        ):
            raise ValueError(
                "Conditional approval must be followed by explicit condition confirmation"
            )
        review_id = run.get("gate1_review_id")
        review = (
            self.registry.get_control_record(project, "project_run_reviews", review_id)
            if review_id
            else None
        )
        if disposition == "approve" and run.get("execution_class") != "routine":
            context = self._context(project, run, "plan")
            if (
                not review
                or review.get("request_digest") != digest(context)
                or review.get("report", {}).get("status") != "aligned"
                or review.get("report", {}).get("unknowns")
            ):
                raise ValueError(
                    "Specialist plan approval requires current aligned Gate 1"
                )
        decision = {
            "decision_id": "project_run_specialist_decision:"
            + digest(
                {
                    "run_id": run_id,
                    "plan_digest": plan_digest,
                    "specialist_assignment_digest": digest(request["specialists"]),
                    "disposition": disposition,
                    "actor": principal.actor_id,
                    "at": timestamp(),
                }
            ),
            "run_id": run_id,
            "plan_digest": plan_digest,
            "specialist_assignment_digest": digest(request["specialists"]),
            "gate1_review_id": review_id,
            "disposition": disposition,
            "conditions": conditions,
            "rationale": rationale,
            "decided_by": principal.actor_id,
            "decided_at": timestamp(),
        }
        if conditions:
            decision["condition_ids"] = [
                "specialist_condition:" + digest([decision["decision_id"], index, item])
                for index, item in enumerate(conditions)
            ]
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project,
                "project_run_specialist_decisions",
                decision["decision_id"],
                decision,
            )
            run["specialist_plan_decision_id"] = decision["decision_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return decision

    def confirm_specialist_plan_conditions(
        self,
        principal: Principal,
        project: str,
        run_id: str,
        plan_digest: str,
        satisfied_condition_ids: list[str],
        rationale: str,
    ) -> dict:
        """Human-confirm all retained conditions before specialist dispatch."""
        human = RegistryService(self.registry.store, principal)
        human.check_access(project, write=True, human=True)
        run = self._run(project, run_id)
        request = self._specialist_approval_request(run)
        if not request["required"] or run.get("execution_class") == "critical":
            raise ValueError("This plan does not use conditional specialist approval")
        if run["plan_digest"] != plan_digest:
            raise ValueError("Specialist plan digest does not match the current plan")
        if run.get("integration_id") or any(
            self.brain.execution.get_task(project, task_id)["state"] != "proposed"
            for task_id in run["task_ids"]
        ):
            raise ValueError("Condition confirmation must precede task authorization")
        conditional = self._specialist_plan_decision(project, run)
        if (
            not conditional
            or conditional.get("disposition") != "approve_with_conditions"
            or conditional.get("plan_digest") != plan_digest
        ):
            raise ValueError("Current conditional specialist approval is required")
        expected_ids = conditional.get("condition_ids", [])
        if (
            not isinstance(satisfied_condition_ids, list)
            or any(not isinstance(item, str) for item in satisfied_condition_ids)
            or len(set(satisfied_condition_ids)) != len(satisfied_condition_ids)
            or sorted(satisfied_condition_ids) != sorted(expected_ids)
        ):
            raise ValueError("Every approval condition must be explicitly confirmed")
        decision = {
            "decision_id": "project_run_specialist_decision:"
            + digest(
                {
                    "conditional_decision_id": conditional["decision_id"],
                    "actor": principal.actor_id,
                    "at": timestamp(),
                }
            ),
            "run_id": run_id,
            "plan_digest": plan_digest,
            "specialist_assignment_digest": digest(request["specialists"]),
            "gate1_review_id": conditional.get("gate1_review_id"),
            "disposition": "approve",
            "conditions": deepcopy(conditional["conditions"]),
            "condition_decision_id": conditional["decision_id"],
            "satisfied_condition_ids": sorted(expected_ids),
            "rationale": text(rationale, "rationale"),
            "decided_by": principal.actor_id,
            "decided_at": timestamp(),
        }
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project,
                "project_run_specialist_decisions",
                decision["decision_id"],
                decision,
            )
            run["specialist_plan_decision_id"] = decision["decision_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return decision

    def _validate_review(
        self, project: str, run: dict, context: dict, report: dict
    ) -> None:
        required = {
            "status",
            "summary",
            "checks",
            "unknowns",
            "evidence_ids",
            "method",
            "model",
            "response_id",
            "request_digest",
        }
        if (
            not isinstance(report, dict)
            or not required.issubset(report)
            or report["request_digest"] != digest(context)
        ):
            raise ValueError("Run review contract or request digest invalid")
        if report["status"] not in {
            "aligned",
            "misaligned",
            "insufficient_information",
        }:
            raise ValueError("Invalid run review status")
        for key in ("summary", "method", "model"):
            text(report[key], key)
        string_list(report["unknowns"], "unknowns")
        string_list(report["evidence_ids"], "evidence_ids", nonempty=True)
        criteria = review_criteria(run["plan"], context["phase"])
        checks = report["checks"]
        if (
            not isinstance(checks, list)
            or len(checks) != len(criteria)
            or {c.get("criterion") for c in checks if isinstance(c, dict)} != criteria
        ):
            raise ValueError("Run review criteria incomplete")
        for check in checks:
            if set(check) != {"criterion", "status", "detail", "evidence_ids"} or check[
                "status"
            ] not in {"aligned", "misaligned", "insufficient_information"}:
                raise ValueError("Invalid run review check")
            text(check["detail"], "detail")
            string_list(check["evidence_ids"], "evidence_ids", nonempty=True)
            if not set(check["evidence_ids"]).issubset(report["evidence_ids"]):
                raise ValueError("Run review check cites unlisted evidence")
        statuses = {c["status"] for c in checks}
        expected = (
            "misaligned"
            if "misaligned" in statuses
            else "insufficient_information"
            if "insufficient_information" in statuses
            else "aligned"
        )
        if report["status"] != expected:
            raise ValueError("Run review verdict contradicts checks")
        validate_materiality(context, report)
        if (
            context.get("phase") == "outcome"
            and context["integrated_run_assessment"]["assessment"]["status"] != "clear"
            and report["status"] == "aligned"
        ):
            raise ValueError(
                "Gate 2 cannot align a run with unresolved integrated-assessment findings"
            )
        allowed = {
            n["node_id"]
            for n in context["source_snapshot"]["records"]
            if n["node_type"] == "evidence"
        }
        allowed.update(context["client_project_brief"]["definition"]["evidence_ids"])
        if not set(report["evidence_ids"]).issubset(allowed):
            raise ValueError("Run review cites unknown evidence")
        self.registry._evidence(project, report["evidence_ids"])
        if run["plan"]["knowledge_conflicts"] and not report.get("review_comments"):
            raise ValueError("Knowledge conflict requires Reviewer comment")

    def _consolidation_context(self, project: str, run: dict) -> dict:
        """Rebuild the complete current task artifact set for Lead integration."""
        for bound in run["plan"]["tasks"]:
            if bound.get("depends_on"):
                task = self.brain.execution.get_task(project, bound["task_id"])
                require_run_plan_gate(
                    self.registry,
                    project,
                    task,
                    require_dependency_binding=True,
                )
        items = []
        for bound in run["plan"]["tasks"]:
            task = self.brain.execution.get_task(project, bound["task_id"])
            self.brain.execution._fresh(task)
            dependency_binding = None
            if bound.get("depends_on"):
                require_run_plan_gate(
                    self.registry,
                    project,
                    task,
                    require_dependency_binding=True,
                )
                dependency_binding = self.registry.get_control_record(
                    project,
                    DEPENDENCY_BINDINGS,
                    dependency_binding_id(run["run_id"], task["task_id"]),
                )
            result = (
                self.registry.get_record(project, task["result_id"])
                if task.get("result_id")
                else None
            )
            assessments = [
                assessment
                for assessment in self.registry.list_records(project, "assessment")
                if result
                and assessment["attributes"].get("result_id") == result["node_id"]
                and assessment["attributes"].get("result_digest") == digest(result)
                and assessment["attributes"].get("task_digest") == task["task_digest"]
            ]
            assessments.sort(key=lambda item: item["attributes"].get("produced_at", ""))
            assessment = assessments[-1] if assessments else None
            review = (
                current_review(self.registry, project, task["task_id"])
                if task["state"] == "succeeded"
                and assessment
                and run.get("execution_class") != "routine"
                else None
            )
            specialist_proposal = self._current_specialist_proposal(project, run, task)
            if bound.get("specialist_assignment") and not specialist_proposal:
                raise ValueError(
                    "Current specialist proposal is required before run integration"
                )
            decisions = [
                record
                for record in self.registry.list_records(project, "decision")
                if result
                and record["attributes"].get("result_id") == result["node_id"]
                and record["attributes"].get("disposition")
                in {"accept", "hold", "rework", "reject"}
            ]
            evidence_ids = sorted(
                set(task["input_snapshot"].get("issue", {}).get("evidence_ids", []))
            )
            evidence_ids.extend(
                record["node_id"]
                for record in task["input_snapshot"].get("records", [])
                if record.get("node_type") == "evidence"
            )
            evidence_ids = sorted(set(evidence_ids))
            evidence_records = [
                self.registry.get_record(project, evidence_id)
                for evidence_id in evidence_ids
                if self.registry.get_node(project, evidence_id)
            ]
            provenance = {
                "input_digest": task["input_digest"],
                "task_digest": task["task_digest"],
                "result_digest": digest(result) if result else None,
                "assessment_digest": digest(assessment) if assessment else None,
                "review_digest": digest(review) if review else None,
                "decision_digest": digest(decisions[-1]) if decisions else None,
                "dependency_binding_digest": (
                    dependency_binding.get("binding_digest")
                    if dependency_binding
                    else None
                ),
                "specialist_proposal_digest": (
                    specialist_proposal.get("proposal_digest")
                    if specialist_proposal
                    else None
                ),
                "evidence_ids": evidence_ids,
                "source_revisions": [
                    {
                        "evidence_id": evidence["node_id"],
                        "document_id": evidence["attributes"].get("document_id"),
                        "revision": evidence["attributes"].get("revision"),
                        "page": evidence["attributes"].get("page"),
                        "section": evidence["attributes"].get("section"),
                        "locator": evidence["attributes"].get("locator"),
                    }
                    for evidence in evidence_records
                ],
            }
            items.append(
                {
                    "task_id": task["task_id"],
                    "task_digest": task["task_digest"],
                    "state": task["state"],
                    "result_id": task.get("result_id"),
                    "result_digest": digest(result) if result else None,
                    "assessment_id": assessment["node_id"] if assessment else None,
                    "assessment_digest": digest(assessment) if assessment else None,
                    "result": deepcopy(result),
                    "assessment": deepcopy(assessment),
                    "gate2_review_id": review["node_id"] if review else None,
                    "gate2_status": review["attributes"]["status"] if review else None,
                    "decision_id": decisions[-1]["node_id"] if decisions else None,
                    "decision_disposition": decisions[-1]["attributes"]["disposition"]
                    if decisions
                    else None,
                    "tool": task["tool"],
                    "parameters": deepcopy(task["parameters"]),
                    "brain_plan": deepcopy(task["brain_plan"]),
                    "source_snapshot": deepcopy(task["input_snapshot"]),
                    "dependency_binding": deepcopy(dependency_binding),
                    "specialist_proposal": deepcopy(specialist_proposal),
                    "task_gate2": deepcopy(review),
                    "decision": deepcopy(decisions[-1]) if decisions else None,
                    "source_evidence": evidence_records,
                    "provenance": provenance,
                }
            )
        return {
            "run_id": run["run_id"],
            "plan": deepcopy(run["plan"]),
            "task_outcomes": items,
        }

    def integrate(self, project: str, run_id: str) -> dict:
        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        gate = (
            self.registry.get_control_record(
                project, "project_run_reviews", run["gate1_review_id"]
            )
            if run["gate1_review_id"]
            else None
        )
        if run.get("execution_class") != "routine" and (
            not gate
            or gate["report"]["status"] != "aligned"
            or gate["report"]["unknowns"]
        ):
            raise ValueError("Aligned run Gate 1 required")
        if run["integration_id"]:
            raise ValueError("Run already integrated")
        context = self._consolidation_context(project, run)
        items = context["task_outcomes"]
        dependency_provenance = [
            {
                "task_id": item["task_id"],
                "task_digest": item["task_digest"],
                "binding_digest": item["dependency_binding"]["binding_digest"],
                "dependencies": deepcopy(item["dependency_binding"]["dependencies"]),
            }
            for item in items
            if item.get("dependency_binding")
        ]
        if self.integrator is None:
            raise ValueError("Lead integrator unavailable")
        proposal = self.integrator(deepcopy(context))
        if not isinstance(proposal, dict) or set(proposal) != {
            "summary",
            "recommendation",
            "result_ids",
            "evidence_ids",
            "unknowns",
            "conflicts",
            "integrated_assessment",
        }:
            raise ValueError("Invalid Lead integration contract")
        text(proposal["summary"], "summary")
        text(proposal["recommendation"], "recommendation")
        for key in ("result_ids", "evidence_ids", "unknowns", "conflicts"):
            string_list(proposal[key], key)
        if set(proposal["result_ids"]) != {
            i["result_id"] for i in items if i["result_id"]
        }:
            raise ValueError("Lead integration omitted or invented V2 results")
        self.registry._evidence(project, proposal["evidence_ids"])
        assessment = validate_integrated_assessment(
            proposal["integrated_assessment"], run["plan"], items
        )
        if assessment["status"] != "clear" and proposal[
            "recommendation"
        ].casefold().startswith(("accept", "approve", "proceed", "ready")):
            raise ValueError(
                "Lead recommendation must hold or qualify unresolved integrated-assessment findings"
            )
        if (
            any(
                i["state"] != "succeeded"
                or not i["assessment_id"]
                or (
                    run.get("execution_class") != "routine" and not i["gate2_review_id"]
                )
                or (
                    run.get("execution_class") != "routine"
                    and i["decision_disposition"] != "accept"
                )
                for i in items
            )
            and not proposal["unknowns"]
        ):
            raise ValueError("Incomplete V2 execution must be stated as unknown")
        consolidation_input_digest = digest(context)
        assessment_record = {
            "assessment_id": "project_run_assessment:"
            + digest(
                {
                    "run_id": run_id,
                    "source_digest": consolidation_input_digest,
                    "assessment": assessment,
                }
            ),
            "schema_version": assessment["schema_version"],
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "source_digest": consolidation_input_digest,
            "dependency_provenance": deepcopy(dependency_provenance),
            "assessment": assessment,
        }
        assessment_record["assessment_digest"] = digest(assessment_record)
        integration = {
            "integration_id": "project_run_integration:"
            + digest({"context": context, "proposal": proposal}),
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "task_outcomes": items,
            "proposal": deepcopy(proposal),
            "assessment": assessment,
            "assessment_id": assessment_record["assessment_id"],
            "assessment_digest": assessment_record["assessment_digest"],
            "dependency_provenance": deepcopy(dependency_provenance),
            "consolidation_input_digest": consolidation_input_digest,
            "integrated_by": self.registry.principal.actor_id,
            "integrated_at": timestamp(),
        }
        integration["integration_digest"] = digest(integration)
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project,
                "project_run_assessments",
                assessment_record["assessment_id"],
                assessment_record,
            )
            self.registry.store.put_record(
                project,
                "project_run_integrations",
                integration["integration_id"],
                integration,
            )
            run["integration_id"] = integration["integration_id"]
            run["integration_digest"] = integration["integration_digest"]
            run["assessment_id"] = assessment_record["assessment_id"]
            run["assessment_digest"] = assessment_record["assessment_digest"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return integration

    def complete_routine(self, project: str, run_id: str) -> dict:
        """Record process completion without granting engineering acceptance."""
        from engineering_registry.project_runs import (
            require_no_new_material_decisions,
            require_run_plan_gate,
        )

        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        if run.get("execution_class") != "routine" or run.get("outcome_id"):
            raise ValueError("Only an undecided Routine run can complete here")
        require_no_new_material_decisions(self.registry, project, run)
        context = self._context(project, run, "outcome")
        integration = context["execution_result"]
        if (
            integration["proposal"]["unknowns"]
            or integration["proposal"]["conflicts"]
            or integration["assessment"]["status"] != "clear"
        ):
            raise ValueError("Routine completion has unresolved concerns")
        for item in integration["task_outcomes"]:
            task = self.brain.execution.get_task(project, item["task_id"])
            require_run_plan_gate(self.registry, project, task)
            if task["state"] != "succeeded" or not item["assessment_id"]:
                raise ValueError("Routine completion requires every assessed V2 output")
            assessment = self.registry.get_record(project, item["assessment_id"])
            attrs = assessment["attributes"]
            if (
                digest(assessment) != item["assessment_digest"]
                or attrs["unknowns"]
                or not all(check["passed"] for check in attrs["checks"])
                or attrs["recommendation"] in {"hold", "rework"}
            ):
                raise ValueError("Routine assessment has unresolved concerns")
        outcome = {
            "outcome_id": "project_run_outcome:"
            + digest(
                {
                    "run": run_id,
                    "integration": run["integration_id"],
                    "kind": "process_complete",
                }
            ),
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "integration_id": run["integration_id"],
            "disposition": "process_complete",
            "authority": "ai_lead_process_only",
            "recorded_by": self.registry.principal.actor_id,
            "recorded_at": timestamp(),
            "project_lesson_id": None,
        }
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project, "project_run_outcomes", outcome["outcome_id"], outcome
            )
            run["outcome_id"] = outcome["outcome_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return outcome

    def complete_engineering(self, project: str, run_id: str, policy_id: str) -> dict:
        """Close bounded Engineering work only under accepted delegated result decisions."""
        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        if run.get("execution_class") != "engineering" or run.get("outcome_id"):
            raise ValueError("Engineering delegated completion is unavailable")
        context = self._context(project, run, "outcome")
        review_id = run.get("gate2_review_id")
        review = (
            self.registry.get_control_record(project, "project_run_reviews", review_id)
            if review_id
            else None
        )
        if (
            not review
            or review["request_digest"] != digest(context)
            or review["report"]["status"] != "aligned"
            or review["report"]["unknowns"]
        ):
            raise ValueError("Engineering completion requires current aligned Gate 2")
        policy = self.registry.delegation_policy(project, policy_id)
        if not policy["active"]:
            raise ValueError("Delegated completion policy is revoked")
        integration = context["execution_result"]
        if (
            integration["proposal"]["unknowns"]
            or integration["proposal"]["conflicts"]
            or integration["assessment"]["status"] != "clear"
        ):
            raise ValueError("Engineering integration has unresolved concerns")
        for item in integration["task_outcomes"]:
            decision = (
                self.registry.get_record(project, item["decision_id"])
                if item["decision_id"]
                else None
            )
            if (
                not decision
                or item["state"] != "succeeded"
                or item["gate2_status"] != "aligned"
                or decision["attributes"].get("authority") != "ai_delegated"
                or decision["attributes"].get("disposition") != "accept"
                or decision["attributes"].get("policy_digest")
                != policy["policy_digest"]
            ):
                raise ValueError(
                    "Engineering completion requires accepted delegated V2 decisions"
                )
        outcome = {
            "outcome_id": "project_run_outcome:"
            + digest(
                {"run": run_id, "review": review_id, "policy": policy["policy_digest"]}
            ),
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "integration_id": run["integration_id"],
            "gate2_review_id": review_id,
            "disposition": "process_complete",
            "authority": "human_approved_result_delegation",
            "policy_id": policy_id,
            "policy_digest": policy["policy_digest"],
            "recorded_by": self.registry.principal.actor_id,
            "recorded_at": timestamp(),
            "project_lesson_id": None,
        }
        with self.registry.store.transaction():
            self.registry.store.put_record(
                project, "project_run_outcomes", outcome["outcome_id"], outcome
            )
            run["outcome_id"] = outcome["outcome_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return outcome

    def decide(
        self,
        principal: Principal,
        project: str,
        run_id: str,
        disposition: str,
        evidence_ids: list[str],
        rationale: str,
        *,
        lesson_id: str | None = None,
        lesson_title: str | None = None,
    ) -> dict:
        human = RegistryService(self.registry.store, principal)
        human.check_access(project, write=True, human=True)
        run = self._run(project, run_id)
        if disposition not in {"accept", "hold", "rework"} or run["outcome_id"]:
            raise ValueError("Invalid or already decided run outcome")
        context = self._context(project, run, "outcome")
        review = (
            self.registry.get_control_record(
                project, "project_run_reviews", run["gate2_review_id"]
            )
            if run["gate2_review_id"]
            else None
        )
        if not review or review["request_digest"] != digest(context):
            raise ValueError("Current run Gate 2 required")
        integration = context["execution_result"]
        items = integration["task_outcomes"]
        if disposition == "accept" and (
            review["report"]["status"] != "aligned"
            or review["report"]["unknowns"]
            or integration["proposal"]["unknowns"]
            or integration["proposal"]["conflicts"]
            or integration["assessment"]["status"] != "clear"
            or any(
                i["state"] != "succeeded"
                or i["gate2_status"] != "aligned"
                or not i["assessment_id"]
                or i["decision_disposition"] != "accept"
                for i in items
            )
        ):
            raise ValueError("Unresolved run issues cannot be accepted")
        if (lesson_id is None) != (lesson_title is None) or (
            lesson_id and disposition != "accept"
        ):
            raise ValueError("Project lesson requires an accepted run and title")
        string_list(evidence_ids, "evidence_ids", nonempty=True)
        human._evidence(project, evidence_ids)
        text(rationale, "rationale")
        with self.registry.store.transaction():
            lesson = (
                human.promote_lesson(
                    project,
                    lesson_id,
                    lesson_title,
                    [run["issue_id"]],
                    evidence_ids,
                    rationale,
                )
                if lesson_id
                else None
            )
            outcome = {
                "outcome_id": "project_run_outcome:"
                + digest(
                    {
                        "run": run_id,
                        "review": review["review_id"],
                        "disposition": disposition,
                        "rationale": rationale,
                        "actor": principal.actor_id,
                    }
                ),
                "run_id": run_id,
                "plan_digest": run["plan_digest"],
                "integration_id": run["integration_id"],
                "integrated_assessment_id": run.get("assessment_id"),
                "integrated_assessment_digest": run.get("assessment_digest"),
                "gate2_review_id": review["review_id"],
                "disposition": disposition,
                "evidence_ids": evidence_ids,
                "rationale": rationale,
                "decided_by": principal.actor_id,
                "decided_at": timestamp(),
                "project_lesson_id": lesson_id if lesson else None,
            }
            self.registry.store.put_record(
                project, "project_run_outcomes", outcome["outcome_id"], outcome
            )
            run["outcome_id"] = outcome["outcome_id"]
            self.registry.store.put_record(
                project, "project_runs", run_id, run, replace=True
            )
        return outcome

    def propose_candidate(self, project: str, run_id: str, candidate_id: str) -> dict:
        self.registry.check_access(project, write=True)
        run = self._run(project, run_id)
        outcome = (
            self.registry.get_control_record(
                project, "project_run_outcomes", run["outcome_id"]
            )
            if run["outcome_id"]
            else None
        )
        if (
            not outcome
            or outcome["disposition"] != "accept"
            or not outcome["project_lesson_id"]
        ):
            raise ValueError(
                "Candidate requires accepted run and human-approved project lesson"
            )
        lesson = self.registry.get_record(project, outcome["project_lesson_id"])
        candidate = {
            "candidate_id": text(candidate_id, "candidate_id"),
            "run_id": run_id,
            "outcome_id": outcome["outcome_id"],
            "project_lesson_id": outcome["project_lesson_id"],
            "project_lesson_digest": digest(lesson),
            "status": "candidate",
            "source_digest": digest(
                {"run": run, "outcome": outcome, "project_lesson": lesson}
            ),
            "proposed_by": self.registry.principal.actor_id,
            "proposed_at": timestamp(),
            "proposal": None,
            "candidate_digest": None,
            "validation": None,
        }
        self.registry.store.put_record(
            project, "knowledge_candidates", candidate_id, candidate
        )
        return candidate

    def generalize(self, project: str, candidate_id: str) -> dict:
        self.registry.check_access(project, write=True)
        candidate = self.registry.get_control_record(
            project, "knowledge_candidates", candidate_id
        )
        if not candidate or candidate["status"] != "candidate":
            raise ValueError("Generalisation requires a retained candidate")
        run = self._run(project, candidate["run_id"])
        outcome = self.registry.get_control_record(
            project, "project_run_outcomes", candidate["outcome_id"]
        )
        lesson = self.registry.get_record(project, candidate["project_lesson_id"])
        context = {"run": run, "outcome": outcome, "project_lesson": lesson}
        if (
            digest(context) != candidate["source_digest"]
            or digest(lesson) != candidate["project_lesson_digest"]
        ):
            raise ValueError("Candidate source changed")
        if self.generalizer is None:
            raise ValueError("Knowledge generalizer unavailable")
        proposal = self.generalizer(deepcopy(context))
        fields = {
            "title",
            "observation",
            "result",
            "interpretation",
            "validated_statement",
            "applicability",
            "limitations",
            "relevant_standards",
            "review_due",
        }
        if not isinstance(proposal, dict) or set(proposal) != fields:
            raise ValueError("Invalid candidate knowledge contract")
        for key in fields - {"relevant_standards"}:
            text(proposal[key], key)
        string_list(proposal["relevant_standards"], "relevant_standards")
        candidate["proposal"] = proposal
        candidate["status"] = "proposed"
        candidate["generalized_by"] = self.registry.principal.actor_id
        candidate["generalized_at"] = timestamp()
        candidate["candidate_digest"] = digest(candidate)
        self.registry.store.put_record(
            project, "knowledge_candidates", candidate_id, candidate, replace=True
        )
        return candidate

    def validate_candidate(
        self,
        principal: Principal,
        project: str,
        candidate_id: str,
        candidate_digest: str,
        approve: bool,
        rationale: str,
        *,
        organization_id: str | None = None,
        organization_lesson_id: str | None = None,
    ) -> dict:
        human = RegistryService(self.registry.store, principal)
        human.check_access(project, write=True, human=True)
        if type(approve) is not bool:
            raise ValueError("Validation decision must be boolean")
        candidate = self.registry.get_control_record(
            project, "knowledge_candidates", candidate_id
        )
        if (
            not candidate
            or candidate["status"] != "proposed"
            or candidate["candidate_digest"] != candidate_digest
        ):
            raise ValueError("Candidate missing, changed or already validated")
        lesson = human.get_record(project, candidate["project_lesson_id"])
        if digest(lesson) != candidate["project_lesson_digest"]:
            raise ValueError("Source project lesson changed")
        text(rationale, "rationale")
        if approve and (not organization_id or not organization_lesson_id):
            raise ValueError("Organizational promotion requires explicit target IDs")
        with ExitStack() as transactions:
            transactions.enter_context(self.registry.store.transaction())
            promoted = None
            if approve:
                if self.organization_registry is not None:
                    organization_human = RegistryService(
                        self.organization_registry.store, principal
                    )
                    transactions.enter_context(organization_human.store.transaction())
                    promote = OrganizationalKnowledgeService(
                        human, organization_human
                    ).promote
                else:
                    if self.registry.store.bound_scope is not None:
                        raise ValueError(
                            "Candidate promotion requires a configured organisational Registry"
                        )
                    promote = human.promote_organizational_lesson
                promoted = promote(
                    organization_id,
                    organization_lesson_id,
                    candidate["proposal"]["title"],
                    [(project, candidate["project_lesson_id"])],
                    **{k: v for k, v in candidate["proposal"].items() if k != "title"},
                    rationale=rationale,
                )
                if "node" in promoted:
                    promoted = promoted["node"]
            candidate["status"] = "validated" if approve else "rejected"
            candidate["validation"] = {
                "validated_by": principal.actor_id,
                "validated_at": timestamp(),
                "rationale": rationale,
                "organization_id": organization_id if approve else None,
                "organizational_lesson_id": promoted["node_id"] if promoted else None,
            }
            self.registry.store.put_record(
                project, "knowledge_candidates", candidate_id, candidate, replace=True
            )
        return candidate
