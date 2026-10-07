"""Document AI orchestration for the existing bounded document tools.

Default checks assess output contracts and source traceability. An explicitly
supplied callback adds proposed reasoning without granting human authority.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from engineering_execution import ExecutionService, SolverCatalog
from engineering_execution.tools import validate_parameters
from engineering_registry.service import Principal, RegistryService, digest

from .errors import AnalysisUnavailable

CRITERIA = [
    "source_provenance",
    "output_contract",
    "source_traceability",
    "review_limits",
]
OUTPUTS = {
    "fem_linear_static": [
        "model_id",
        "results",
        "solver",
        "model_digest",
        "source_bindings",
        "warnings",
        "human_engineering_approval_required",
        "production_approved",
        "output_digest",
    ],
    "external_solver": [
        "values",
        "solver",
        "input_quantities",
        "source_evidence_ids",
        "warnings",
        "solver_warnings",
    ],
    "compare_revision": [
        "base_evidence_id",
        "head_evidence_id",
        "base_revision",
        "head_revision",
        "removed_lines",
        "added_lines",
        "warnings",
    ],
    "validate_traceability": [
        "evidence_count",
        "missing_text",
        "missing_locators",
        "requirement_status",
        "warnings",
    ],
    "generate_review_pack": [
        "schema_version",
        "issue",
        "records",
        "human_review_required",
        "warnings",
    ],
    "validate_proposal_readiness": [
        "submission_id",
        "submission_digest",
        "candidate_revision_id",
        "template_revision_id",
        "source_checks",
        "protected_section_checks",
        "requirement_coverage",
        "unresolved_matrix_rows",
        "ready_for_human_review",
        "warnings",
    ],
}


def _knowledge_conflict(review: dict, lessons: list[dict], records: list[dict]) -> dict:
    lesson = next(item for item in lessons if item["node_id"] == review["lesson_id"])
    attrs = lesson["attributes"]
    source = attrs.get("source_organizational_lesson", lesson)
    source_attrs = source.get("attributes", {})
    knowledge_id = attrs.get("source_organizational_lesson_id", review["lesson_id"])
    knowledge_statement = source_attrs.get("validated_statement") or lesson["title"]
    requirements = [
        record
        for record in records
        if record["node_id"] in review["requirement_ids"]
        and record["node_type"] == "requirement"
    ]
    requirement_lines = [
        f"Current Project Requirement `{item['node_id']}`: "
        + str(item["attributes"].get("source_text") or item["title"])
        for item in requirements
    ]
    decision_required = review["resolution"] == "deviation_proposed"
    early_warning = review["conflict_interpretation"] == "possible_requirement_problem"
    message = "\n".join(
        [
            "KNOWLEDGE CONFLICT — Project Requirement",
            f"Validated knowledge `{knowledge_id}`: {knowledge_statement}",
            *requirement_lines,
            "The knowledge cannot be directly adopted: " + review["rationale"],
            *(
                [
                    "EARLY WARNING — Possible concern with the project requirement: "
                    + review["requirement_concern"],
                    "Current project requirement remains governing pending review.",
                ]
                if early_warning
                else [
                    "The current project requirement controls; this conflict does not make the knowledge wrong."
                ]
            ),
            "Lead action: " + review["lead_action"],
            "Human/client decision required: "
            + (
                "Yes — a deviation is proposed."
                if decision_required
                else "Only if a deviation from the project requirement is proposed."
            ),
        ]
    )
    return {
        "lesson_id": review["lesson_id"],
        "knowledge_id": knowledge_id,
        "requirement_ids": review["requirement_ids"],
        "evidence_ids": review["evidence_ids"],
        "resolution": review["resolution"],
        "conflict_interpretation": review["conflict_interpretation"],
        "requirement_concern": review["requirement_concern"],
        "early_warning": early_warning,
        "lead_action": review["lead_action"],
        "human_client_decision_required": decision_required,
        "message": message,
    }


class DocumentAIWorkflow:
    def __init__(
        self,
        registry: RegistryService,
        *,
        analysis: Callable[[str, dict], dict] | None = None,
        alignment_reviewer: Callable[[dict], dict] | None = None,
        decision_registry: RegistryService | None = None,
        delegation_policy_id: str | None = None,
        solver_catalog: SolverCatalog | None = None,
    ) -> None:
        if registry.principal.role in {"reviewer", "delegate", "ai_reviewer"}:
            registry = RegistryService(
                registry.store,
                Principal(
                    "document-ai:" + registry.principal.actor_id,
                    registry.principal.project_ids,
                ),
            )
        self.registry = registry
        self.execution = ExecutionService(registry, solver_catalog=solver_catalog)
        self.analysis = analysis
        self.alignment_reviewer = alignment_reviewer
        self.decision_registry = decision_registry
        self.delegation_policy_id = delegation_policy_id
        if decision_registry is not None and (
            decision_registry.store is not registry.store
            or decision_registry.principal.role != "delegate"
            or not registry.principal.project_ids.issubset(
                decision_registry.principal.project_ids
            )
        ):
            raise ValueError("Decision authority requires a same-store host delegate")
        if decision_registry is not None:
            # Registry remains independent of execution imports. The trusted host
            # workflow supplies the actual catalogue/version freshness capability.
            self.decision_registry = RegistryService(
                decision_registry.store,
                decision_registry.principal,
                execution_validator=self.execution.validate_task_freshness,
            )

    def _plan(self, project: str, issue: str, tool: str, parameters: dict) -> dict:
        validate_parameters(tool, parameters)
        if tool == "external_solver":
            if self.analysis is None:
                raise ValueError(
                    "External solver planning requires explicit Document AI reasoning analysis"
                )
            if self.execution.solver_catalog is None:
                raise ValueError("External solver requires a validated host catalogue")
        if tool == "fem_linear_static" and self.analysis is None:
            raise ValueError(
                "FEM task planning requires Document AI reasoning analysis"
            )
        context = self.registry.task_context(project, issue)
        evidence = context["evidence_ids"]
        requirements = context["requirement_ids"]
        unknowns = [
            "Engineering adequacy and governing source authority require human verification."
        ]
        if not requirements:
            unknowns.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: no requirements linked."
            )
        controls = context["source_control"]
        unknowns.extend(sorted({c["warning"] for c in controls}))
        evidence_reviews = context["evidence_review"]
        for review in evidence_reviews:
            if review["status"] == "unreviewed_text_missing":
                unknowns.append(
                    "UNKNOWN / INSUFFICIENT INFORMATION: evidence text is missing and has not been human-reviewed."
                )
            elif review["status"] in {"insufficient", "not_reviewable"}:
                unknowns.append(
                    "UNKNOWN / INSUFFICIENT INFORMATION: human evidence review recorded "
                    + review["status"]
                    + "."
                )
        if tool == "external_solver":
            assert self.execution.solver_catalog is not None
            binding = self.execution.solver_catalog.validate_inputs(
                parameters, context["execution_snapshot"]
            )
            scope = (
                binding["definition"]["method"]
                + " Limits: "
                + "; ".join(binding["definition"]["applicability_limits"])
            )
        elif tool == "fem_linear_static":
            from engineering_execution.fem import fem_task_binding

            binding = fem_task_binding(parameters["job"], context["execution_snapshot"])
            scope = "Run bounded linear-static OpenSeesPy candidate calculation in an isolated worker. Output requires independent structural-engineer verification and human decision; this is not Eurocode design verification or production approval."
        elif tool == "compare_revision":
            if parameters["base_evidence_id"] == parameters["head_evidence_id"]:
                raise ValueError("Revision comparison requires two distinct sources")
            scope = "Compare only the explicitly ordered source excerpts; retain revision identity and limitations."
        elif tool == "validate_traceability":
            scope = "Check recorded evidence text, source locators and requirement links; retain missing-information diagnostics."
        else:
            scope = "Assemble the exact issue/source snapshot for review; preserve facts, assumptions and unknowns."
        lessons = context["lesson_ids"]
        organisational_lessons = [
            lesson_id
            for lesson_id in lessons
            if self.registry.get_record(project, lesson_id)["attributes"].get("scope")
            == "imported_organizational"
        ]
        if lessons:
            unknowns.append(
                "Approved project lessons are context; applicability to this task requires review."
            )
        project_brief = self.registry.client_project_brief(project)
        if self.alignment_reviewer is not None and project_brief is None:
            raise ValueError(
                "Automated alignment review requires the client project brief"
            )
        mandate = self.registry.client_mandate(project, issue)
        if self.alignment_reviewer is not None and mandate is None:
            raise ValueError(
                "Automated alignment review requires the original client mandate"
            )
        if mandate is not None:
            definition = mandate["definition"]
            if tool not in definition["allowed_tools"]:
                raise ValueError("Requested tool is outside the client mandate")
            # General technical warnings remain output limitations. For an explicitly
            # administrative mandate, only actual uncertainty prevents delegation.
            if (
                not definition["consequence_domains"]
                and tool != "validate_proposal_readiness"
            ):
                unknowns = list(definition["unknowns"])
                unknowns.extend(
                    c["warning"] for c in controls if c["status"] == "unverified"
                )
                unknowns.extend(
                    "Evidence is insufficient: " + r["evidence_id"]
                    for r in evidence_reviews
                    if r["status"]
                    in {"unreviewed_text_missing", "insufficient", "not_reviewable"}
                )
                if lessons:
                    unknowns.append("Approved lesson applicability requires review.")
            else:
                unknowns.extend(definition["unknowns"])
        return {
            "schema_version": 1,
            "producer": "engineering_document_ai",
            "objective": mandate["definition"]["objective"]
            if mandate
            else context["issue"]["title"],
            "scope": mandate["definition"]["scope"] if mandate else scope,
            "expected_outputs": OUTPUTS[tool],
            "acceptance_criteria": [*CRITERIA],
            "evidence_ids": evidence,
            "requirement_ids": requirements,
            "lesson_ids": lessons,
            **(
                {"organizational_lesson_ids": organisational_lessons}
                if organisational_lessons
                else {}
            ),
            "unknowns": unknowns,
            "analysis_summary": f"Structure {tool} for the issue using {len(evidence)} evidence references and {len(requirements)} requirements. Technical conclusions remain subject to human review.",
            "source_control": controls,
            "evidence_review": evidence_reviews,
            **({"client_mandate": mandate} if mandate else {}),
            **({"client_project_brief": project_brief} if project_brief else {}),
            **({"alignment_review_required": True} if self.alignment_reviewer else {}),
            **({"solver_binding": binding} if tool == "external_solver" else {}),
            **({"fem_binding": binding} if tool == "fem_linear_static" else {}),
        }

    def structure_task(
        self,
        project: str,
        task_id: str,
        issue_id: str,
        tool: str,
        parameters: dict,
        *,
        run_id: str | None = None,
    ) -> dict:
        self.registry.check_access(project, write=True)
        run = (
            self.registry.store.get_record(project, "project_runs", run_id)
            if run_id
            else None
        )
        if run_id and (
            not run
            or run.get("state") != "preparing"
            or run.get("issue_id") != issue_id
            or task_id not in run.get("task_ids", [])
        ):
            raise ValueError("Task requires a retained preparing project run")
        routine = bool(run and run.get("execution_class") == "routine")
        if routine:
            if run is None:
                raise ValueError("Routine task requires its retained project run")
            from engineering_registry.execution_policy import (
                current_execution_policy,
                require_policy_tasks,
            )

            policy = current_execution_policy(self.registry, project, run["policy_id"])
            if policy["policy_digest"] != run["policy_digest"]:
                raise ValueError("Routine execution policy changed")
            require_policy_tasks(policy, [{"tool": tool, "parameters": parameters}])
        if self.analysis is not None:
            task = self._structure_with_analysis(
                project,
                task_id,
                issue_id,
                tool,
                parameters,
                run_id=run_id,
                routine_run=run if routine else None,
            )
        else:
            with self.registry.store.transaction():
                plan = self._plan(project, issue_id, tool, parameters)
                if routine:
                    if run is None:
                        raise ValueError(
                            "Routine task requires its retained project run"
                        )
                    plan.pop("alignment_review_required", None)
                    plan.update(
                        execution_class="routine",
                        run_id=run_id,
                        execution_policy_id=run["policy_id"],
                        execution_policy_digest=run["policy_digest"],
                    )
                task = self.execution.propose_task(
                    project, task_id, issue_id, tool, parameters, brain_plan=plan
                )
        if self.alignment_reviewer and not routine:
            self.review_plan_alignment(project, task_id)
        return task

    def record_task_decision(
        self,
        project: str,
        issue_id: str,
        decision_id: str,
        statement: str,
        rationale: str,
        evidence_ids: list[str],
        **details: Any,
    ) -> dict:
        """Record a material lead decision without elevating it to human approval."""
        return self.registry.record_task_decision(
            project,
            issue_id,
            decision_id,
            statement,
            rationale,
            evidence_ids,
            **details,
        )

    def record_lead_step(
        self,
        project: str,
        issue_id: str,
        note_id: str,
        statement: str,
        rationale: str,
        impact_flags: dict[str, str],
        **details: Any,
    ) -> dict:
        return self.registry.record_lead_step(
            project, issue_id, note_id, statement, rationale, impact_flags, **details
        )

    def structure_submission_task(
        self, project: str, task_id: str, issue_id: str, submission_id: str
    ) -> dict:
        """Bind the proposal review task to current Registry sources and controls."""
        self.registry.check_access(project, write=True)
        context = self.registry.submission_context(project, submission_id)
        submission = context["submission"]
        attributes = submission["attributes"]
        if attributes["issue_id"] != issue_id:
            raise ValueError("Submission belongs to a different proposal issue")
        parameters = {"submission_id": submission_id}
        tool = "validate_proposal_readiness"
        plan = self._plan(project, issue_id, tool, parameters)
        plan.update(
            objective="Check the controlled proposal revision, protected template wording, sources and requirement scope matrix.",
            scope="Perform deterministic structural and source-control checks only. Do not judge commercial acceptability, issue the proposal or record a human response.",
            expected_outputs=OUTPUTS[tool],
            acceptance_criteria=["proposal_readiness"],
            analysis_summary=(
                f"Validate submission {submission_id} against its controlled revisions, "
                "accepted requirements and bounded scope matrix; human review remains required."
            ),
            submission_id=submission_id,
            submission_digest=context["submission_digest"],
            proposal_readiness={
                "candidate_revision_id": attributes["candidate_revision_id"],
                "template_revision_id": attributes["template_revision_id"],
                "source_requirement_ids": attributes["source_requirement_ids"],
                "source_evidence_ids": attributes["source_evidence_ids"],
            },
        )
        if (
            "Contractual acceptance is unknown; it remains for competent human review."
            not in plan["unknowns"]
        ):
            plan["unknowns"].append(
                "Contractual acceptance is unknown; it remains for competent human review."
            )
        snapshot = self.execution.input_snapshot(project, issue_id)
        if (
            snapshot.get("submission_context_digests", {}).get(submission_id)
            != context["submission_digest"]
        ):
            raise ValueError(
                "Submission source context changed during task structuring; retry with a fresh task ID"
            )
        current = self.registry.submission_context(project, submission_id)
        if current["submission_digest"] != context["submission_digest"]:
            raise ValueError(
                "Submission sources changed during task structuring; retry with a fresh task ID"
            )
        task = self.execution.propose_task(
            project,
            task_id,
            issue_id,
            tool,
            parameters,
            brain_plan=plan,
            expected_input_digest=digest(snapshot),
        )
        if self.alignment_reviewer:
            self.review_plan_alignment(project, task_id)
        return task

    def _structure_with_analysis(
        self,
        project: str,
        task_id: str,
        issue_id: str,
        tool: str,
        parameters: dict,
        *,
        run_id: str | None = None,
        routine_run: dict | None = None,
    ) -> dict:
        from engineering_registry.analysis import validate_analysis

        with self.registry.store.transaction():
            existing = self.registry.store.get_record(project, "tasks", task_id)
            snapshot = self.execution.input_snapshot(
                project,
                issue_id,
                exclude_task_id=task_id if existing else None,
            )
            if existing:
                if (
                    existing["issue_id"] == issue_id
                    and existing["tool"] == tool
                    and existing["parameters"] == parameters
                    and existing["input_digest"] == digest(snapshot)
                    and "analysis" in existing.get("brain_plan", {})
                    and existing["brain_plan"].get("client_mandate")
                    == self.registry.client_mandate(
                        project, issue_id, exclude_task_id=task_id
                    )
                    and existing["brain_plan"].get("client_project_brief")
                    == self.registry.client_project_brief(project)
                    and bool(existing["brain_plan"].get("alignment_review_required"))
                    == bool(self.alignment_reviewer)
                    and (
                        tool != "external_solver"
                        or self.execution.solver_catalog is not None
                        and existing.get("solver_binding")
                        == self.execution.solver_catalog.binding(
                            parameters["solver_id"]
                        )
                    )
                    and (
                        tool != "fem_linear_static"
                        or existing.get("fem_binding")
                        == self._plan(project, issue_id, tool, parameters).get(
                            "fem_binding"
                        )
                    )
                ):
                    return existing
                raise ValueError(
                    "Task ID has different or changed analysis inputs; use a new ID"
                )
            plan = self._plan(project, issue_id, tool, parameters)
            lessons = [
                self.registry.get_record(project, lid) for lid in plan["lesson_ids"]
            ]
            lessons.sort(
                key=lambda lesson: (
                    lesson["attributes"].get("scope") != "imported_organizational",
                    lesson["node_id"],
                )
            )
        context = {
            "project_id": project,
            "issue_id": issue_id,
            "tool": tool,
            "parameters": deepcopy(parameters),
            "source_snapshot": snapshot,
            "brain_plan": deepcopy(plan),
            **(
                {"client_project_brief": deepcopy(plan["client_project_brief"])}
                if "client_project_brief" in plan
                else {}
            ),
            **(
                {"fem_binding": deepcopy(plan["fem_binding"])}
                if tool == "fem_linear_static"
                else {}
            ),
            **(
                {
                    "solver_binding": self.execution.solver_catalog.binding(
                        parameters["solver_id"]
                    )
                }
                if tool == "external_solver" and self.execution.solver_catalog
                else {}
            ),
            "approved_lessons": lessons,
        }
        assert self.analysis is not None
        reasoning = self.analysis("execution_planning", deepcopy(context))
        validate_analysis(
            reasoning,
            "execution_planning",
            plan["evidence_ids"],
            plan["requirement_ids"],
            [record["node_id"] for record in snapshot["records"]],
        )
        if reasoning["lesson_context"] != lessons:
            raise ValueError("Planning analysis must retain the current lesson context")
        plan["unknowns"] = [
            item
            for item in plan["unknowns"]
            if item
            not in {
                "Approved project lessons are context; applicability to this task requires review.",
                "Approved lesson applicability requires review.",
            }
        ]
        plan["analysis"] = deepcopy(reasoning)
        plan["acceptance_criteria"] = [
            *plan["acceptance_criteria"],
            "engineering_review",
        ]
        statements = [item["finding"] for item in reasoning["findings"]]
        plan["analysis_summary"] = (
            "\n".join(statements)
            or "No material finding returned; source limitations and human review remain explicit."
        )
        recommendations = [
            item["recommendation"]
            for item in reasoning["findings"]
            if item.get("recommendation")
        ]
        if recommendations:
            plan["scope"] += " Document AI recommendations: " + "; ".join(
                recommendations
            )
        for item in reasoning["findings"]:
            for unknown in item.get("uncertainties", []):
                if unknown not in plan["unknowns"]:
                    plan["unknowns"].append(unknown)
        for review in reasoning.get("lesson_reviews", []):
            if review["status"] == "conflict":
                conflict = _knowledge_conflict(review, lessons, snapshot["records"])
                plan.setdefault("knowledge_conflicts", []).append(conflict)
                if review["resolution"] != "compliant_alternative":
                    plan["unknowns"].append(
                        f"Knowledge conflict {review['lesson_id']}: "
                        + (
                            "proposed deviation requires human/client decision."
                            if review["resolution"] == "deviation_proposed"
                            else "compliant path unresolved."
                        )
                    )
            elif review["status"] == "uncertain":
                plan["unknowns"].append(
                    f"Organisational lesson {review['lesson_id']}: "
                    f"{review['status']} — {review['rationale']}"
                )
            if review["status"] == "partial":
                plan.setdefault("lesson_limitations", []).extend(
                    f"{review['lesson_id']}: {limit}" for limit in review["limitations"]
                )
        if routine_run:
            plan.pop("alignment_review_required", None)
            plan.update(
                execution_class="routine",
                run_id=run_id,
                execution_policy_id=routine_run["policy_id"],
                execution_policy_digest=routine_run["policy_digest"],
            )
        with self.registry.store.transaction():
            task = self.execution.propose_task(
                project,
                task_id,
                issue_id,
                tool,
                parameters,
                brain_plan=plan,
                expected_input_digest=digest(snapshot),
            )
            for item in reasoning.get("task_decisions", []):
                decision_id = "decision:task:" + task_id + ":" + digest(item)[:16]
                self.registry.record_task_decision(
                    project,
                    issue_id,
                    decision_id,
                    item["statement"],
                    item["rationale"],
                    item["evidence_ids"],
                    alternatives=item["alternatives"],
                    assumptions=item["assumptions"],
                    requirement_ids=item["requirement_ids"],
                    related_record_ids=item["related_record_ids"],
                    task_id=task_id,
                    impact=item["impact"],
                    decision_level=item["decision_level"],
                )
            return task

    def execute_and_assess(
        self,
        project: str,
        task_id: str,
        *,
        before_execute: Callable[[dict], None] | None = None,
    ) -> dict:
        task = self.execution.get_task(project, task_id)
        if (task.get("brain_plan") or {}).get("alignment_review_required") and task[
            "state"
        ] != "succeeded":
            self.review_plan_alignment(project, task_id)
        result = self.execution.execute(project, task_id, before_execute=before_execute)
        if result.get("node_type") != "result":
            return {"result": result, "assessment": None}
        assessment = self.assess_result(project, task_id, result["node_id"])
        review = None
        if (task.get("brain_plan") or {}).get("alignment_review_required"):
            from engineering_registry.alignment import current_review

            review = current_review(self.registry, project, task_id)
            if not review:
                raise AnalysisUnavailable(
                    "AI project-assurance review is unavailable; the retained assessment remains unverified"
                )
        outcome: dict = {
            "result": result,
            "assessment": assessment,
        }
        if self.alignment_reviewer and (task.get("brain_plan") or {}).get(
            "alignment_review_required"
        ):
            outcome["alignment_review"] = review
            outcome.update(
                self.decide_automatically(project, task_id, assessment, review)
            )
        return outcome

    def review_plan_alignment(self, project: str, task_id: str) -> dict:
        """Independent client-perspective review of the exact plan before V2 runs."""
        from engineering_registry.alignment import alignment_context, current_review

        from .errors import AnalysisUnavailable

        task = self.execution.get_task(project, task_id)
        self.execution._fresh(task)
        if task["state"] not in {"proposed", "queued"}:
            raise ValueError("Plan review requires an unexecuted task")
        retained = current_review(self.registry, project, task_id, phase="plan")
        if retained:
            return retained
        if self.alignment_reviewer is None:
            raise AnalysisUnavailable(
                "Plan alignment reviewer is unavailable; V2 execution is blocked"
            )
        context = alignment_context(self.registry, project, task_id, phase="plan")
        try:
            report = self.alignment_reviewer(deepcopy(context))
        except Exception as exc:
            raise AnalysisUnavailable(
                "Plan alignment reviewer is unavailable; V2 execution is blocked"
            ) from exc
        reviewer = RegistryService(
            self.registry.store,
            Principal(
                "alignment-reviewer:" + self.registry.principal.actor_id,
                self.registry.principal.project_ids,
                "ai_reviewer",
            ),
        )
        return reviewer.record_alignment_review(project, task_id, None, report)

    def review_alignment(self, project: str, task_id: str) -> dict:
        from engineering_registry.alignment import alignment_context, current_review

        from .errors import AnalysisUnavailable

        self.execution._fresh(self.execution.get_task(project, task_id))
        retained = current_review(self.registry, project, task_id)
        if retained:
            return retained
        if self.alignment_reviewer is None:
            raise AnalysisUnavailable(
                "AI alignment reviewer is not configured; output remains unverified"
            )
        context = alignment_context(self.registry, project, task_id)
        try:
            report = self.alignment_reviewer(deepcopy(context))
        except Exception as exc:
            raise AnalysisUnavailable(
                "AI alignment reviewer is unavailable; retry review of the retained output"
            ) from exc
        reviewer = RegistryService(
            self.registry.store,
            Principal(
                "alignment-reviewer:" + self.registry.principal.actor_id,
                self.registry.principal.project_ids,
                "ai_reviewer",
            ),
        )
        return reviewer.record_alignment_review(
            project, task_id, context["execution_result"]["node_id"], report
        )

    def decide_automatically(
        self, project: str, task_id: str, assessment: dict, review: dict | None
    ) -> dict:
        if self.decision_registry is None or self.delegation_policy_id is None:
            return {
                "decision": None,
                "escalation": "No delegated decision policy/authority configured; human decision required.",
            }
        if review is None:
            return {"decision": None, "escalation": "Alignment review is required."}
        task = self.execution.get_task(project, task_id)
        if task["tool"] in {"external_solver", "fem_linear_static"}:
            self.execution._fresh(task)
        if task["tool"] == "fem_linear_static":
            return {
                "decision": None,
                "escalation": "FEM outcomes cannot use delegated automatic disposition; independent structural engineer and human decision required.",
            }
        status = review["attributes"]["status"]
        attrs = assessment["attributes"]
        disposition = (
            "rework"
            if status == "misaligned"
            else "hold"
            if status == "insufficient_information" or review["attributes"]["unknowns"]
            else "accept"
            if all(c["passed"] for c in attrs["checks"])
            else "rework"
        )
        reason = (
            "Document AI reconciliation: "
            + attrs["summary"]
            + " Alignment review: "
            + review["attributes"]["summary"]
            + "; disposition="
            + disposition
            + ". Evidence: "
            + ", ".join(review["attributes"]["evidence_ids"])
        )
        try:
            self.execution._fresh(task)
            decision = self.decision_registry.decide_delegated_result(
                project,
                {
                    "task_id": task_id,
                    "policy_id": self.delegation_policy_id,
                    "assessment_id": assessment["node_id"],
                    "assessment_digest": digest(assessment),
                    "alignment_review_id": review["node_id"],
                    "alignment_review_digest": digest(review),
                    "disposition": disposition,
                    "rationale": reason,
                    "evidence_ids": review["attributes"]["evidence_ids"],
                },
            )
        except (ValueError, PermissionError) as exc:
            return {"decision": None, "escalation": str(exc)}
        return {"decision": decision, "escalation": None}

    def assess_output(self, project: str, task_id: str) -> dict:
        task = self.execution.get_task(project, task_id)
        if task["state"] != "succeeded" or not task["result_id"]:
            raise ValueError("Assessment requires a completed V2 output")
        return self.assess_result(project, task_id, task["result_id"])

    def assess_result(self, project: str, task_id: str, result_id: str) -> dict:
        from engineering_registry.analysis import validate_analysis

        self.registry.check_access(project, write=True)
        task = self.execution.get_task(project, task_id)
        assessment: dict[str, Any] | None = None
        if task["tool"] in {"external_solver", "fem_linear_static"}:
            self.execution._fresh(task)
        if task["tool"] == "validate_proposal_readiness":
            assessment = self._assess_submission_result(project, task, result_id)
            if task.get("brain_plan", {}).get("alignment_review_required"):
                self.review_alignment(project, task_id)
            return assessment
        result = self.registry.get_record(project, result_id)
        plan = task.get("brain_plan", {})
        needs_reasoning = "analysis" in plan
        if needs_reasoning:
            retained = [
                n
                for n in self.registry.list_records(project, "assessment")
                if n["attributes"].get("result_id") == result_id
                and n["attributes"].get("result_digest") == digest(result)
                and n["attributes"].get("task_digest") == task["task_digest"]
                and n["attributes"].get("analysis")
            ]
            assessment = (
                sorted(retained, key=lambda n: n["attributes"]["produced_at"])[-1]
                if retained
                else None
            )
        else:
            assessment = None
        if assessment is None:
            assessment = self._assessment_payload(project, task_id, result_id)
        if needs_reasoning and not assessment.get("attributes", {}).get("analysis"):
            reasoning = None
            ready = False
            detail = "Document AI reasoning adapter is required; offline checks cannot satisfy this criterion."
            if self.analysis is not None:
                context = {
                    "project_id": project,
                    "issue_id": task["issue_id"],
                    "tool": task["tool"],
                    "parameters": task["parameters"],
                    "source_snapshot": task["input_snapshot"],
                    "brain_plan": plan,
                    **(
                        {"client_project_brief": plan["client_project_brief"]}
                        if "client_project_brief" in plan
                        else {}
                    ),
                    "execution_result": result,
                    "approved_lessons": plan["analysis"]["lesson_context"],
                    **(
                        {"solver_binding": task["solver_binding"]}
                        if "solver_binding" in task
                        else {}
                    ),
                    **(
                        {"fem_binding": task["fem_binding"]}
                        if "fem_binding" in task
                        else {}
                    ),
                }
                reasoning = self.analysis("execution_assessment", deepcopy(context))
                validate_analysis(
                    reasoning,
                    "execution_assessment",
                    plan["evidence_ids"],
                    plan["requirement_ids"],
                )
                assessment["analysis"] = deepcopy(reasoning)
                ready = not any(
                    item["status"] in {"unverified", "disputed", "potential"}
                    or item.get("risk_level") in {"high", "critical"}
                    for item in reasoning["findings"]
                ) and not any(
                    review["status"] == "uncertain"
                    or review["status"] == "conflict"
                    and review["resolution"] != "compliant_alternative"
                    for review in reasoning.get("lesson_reviews", [])
                )
                detail = "Evidence-linked proposed engineering review completed; unresolved/high-risk findings require hold."
                if task["tool"] == "fem_linear_static":
                    ready = False
                    detail = "FEM result remains calculation data only; independent structural-engineer verification and a human decision are mandatory."
                    assessment["unknowns"].append(detail)
                for item in reasoning["findings"]:
                    for unknown in item.get("uncertainties", []):
                        if unknown not in assessment["unknowns"]:
                            assessment["unknowns"].append(unknown)
                for review in reasoning.get("lesson_reviews", []):
                    if review["status"] == "conflict":
                        conflict = _knowledge_conflict(
                            review,
                            plan["analysis"]["lesson_context"],
                            task["input_snapshot"]["records"],
                        )
                        assessment["summary"] += " " + conflict["message"]
                        if review["resolution"] != "compliant_alternative":
                            assessment["unknowns"].append(
                                "Knowledge conflict requires a compliant resolution or deviation decision: "
                                + review["lesson_id"]
                            )
                    elif review["status"] == "uncertain":
                        assessment["unknowns"].append(
                            f"Organisational lesson {review['lesson_id']}: "
                            f"{review['status']} — {review['rationale']}"
                        )
                    if review["status"] == "partial":
                        extra_limits = [
                            f"{review['lesson_id']}: {limit}"
                            for limit in review["limitations"]
                            if f"{review['lesson_id']}: {limit}"
                            not in plan.get("lesson_limitations", [])
                        ]
                        if extra_limits:
                            assessment["summary"] += (
                                " Lesson limitations: " + "; ".join(extra_limits)
                            )
                assessment["method"] = "engineering_document_ai.model_output_review.v1"
                proposed = "\n".join(item["finding"] for item in reasoning["findings"])
                if proposed:
                    assessment["summary"] += " Proposed Document AI review: " + proposed
            if not ready:
                assessment["recommendation"] = "hold"
                assessment["unknowns"].append(detail)
            assessment["checks"].append(
                {"criterion": "engineering_review", "passed": ready, "detail": detail}
            )
        if not assessment.get("node_type"):
            assessment = self.registry.record_assessment(
                project, result_id, digest(result), assessment
            )
        if plan.get("alignment_review_required"):
            self.review_alignment(project, task_id)
        return assessment

    def assess_submission_output(self, project: str, task_id: str) -> dict:
        task = self.execution.get_task(project, task_id)
        if task["tool"] != "validate_proposal_readiness":
            raise ValueError("Task is not a proposal-readiness task")
        if task["state"] != "succeeded" or not task["result_id"]:
            raise ValueError(
                "Assessment requires a completed proposal-readiness output"
            )
        assessment = self._assess_submission_result(project, task, task["result_id"])
        if task.get("brain_plan", {}).get("alignment_review_required"):
            self.review_alignment(project, task_id)
        return assessment

    def _assess_submission_result(
        self, project: str, task: dict, result_id: str
    ) -> dict:
        from engineering_execution.tools import run_tool

        plan = task.get("brain_plan", {})
        if task["tool"] != "validate_proposal_readiness" or plan.get(
            "submission_id"
        ) != task["parameters"].get("submission_id"):
            raise ValueError(
                "Proposal assessment requires a Document AI structured submission task"
            )
        context = self.registry.submission_context(project, plan["submission_id"])
        if context["submission_digest"] != plan.get("submission_digest"):
            raise ValueError(
                "Proposal sources changed after execution; the result is stale"
            )
        result = self.registry.get_record(project, result_id)
        attrs = result.get("attributes", {})
        if (
            result.get("node_type") != "result"
            or attrs.get("task") != task["task_id"]
            or attrs.get("inputs", {}).get("task_digest") != task["task_digest"]
        ):
            raise ValueError(
                "Proposal output does not match this exact structured task"
            )
        outputs = attrs.get("outputs", {})
        expected = run_tool(task["tool"], task["input_snapshot"], task["parameters"])
        output_matches = outputs == expected
        readiness_passed = (
            output_matches
            and outputs.get("submission_digest") == plan.get("submission_digest")
            and outputs.get("ready_for_human_review") is True
        )
        detail = "Deterministic V2 proposal-readiness output: " + json.dumps(
            outputs, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        unknowns = list(plan.get("unknowns", []))
        for warning in outputs.get("warnings", []) if isinstance(outputs, dict) else []:
            if warning not in unknowns:
                unknowns.append(warning)
        if not output_matches:
            unknowns.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: retained result differs from the deterministic V2 output for the exact task snapshot."
            )
        if (
            not readiness_passed
            and "Proposal is not ready for human review; resolve listed blockers first."
            not in unknowns
        ):
            unknowns.append(
                "Proposal is not ready for human review; resolve listed blockers first."
            )
        assessment = {
            "task_digest": task["task_digest"],
            "summary": "Proposal checks passed; ready for human review only."
            if readiness_passed
            else "Proposal readiness checks found blockers; hold for correction and review.",
            "checks": [
                {
                    "criterion": "proposal_readiness",
                    "passed": readiness_passed,
                    "detail": detail,
                }
            ],
            "unknowns": list(dict.fromkeys(unknowns)),
            "recommendation": "human_review" if readiness_passed else "hold",
            "evidence_ids": sorted(set(plan.get("evidence_ids", []))),
            "method": "engineering_document_ai.proposal_readiness_assessment.v1",
        }
        return self.registry.record_assessment(
            project, result_id, digest(result), assessment
        )

    def _assessment_payload(self, project: str, task_id: str, result_id: str) -> dict:
        self.registry.check_access(project, write=True)
        task = self.execution.get_task(project, task_id)
        result = self.registry.get_record(project, result_id)
        attrs = result["attributes"]
        if (
            result["node_type"] != "result"
            or attrs.get("task") != task_id
            or attrs.get("inputs", {}).get("task_digest") != task["task_digest"]
        ):
            raise ValueError("Output does not match this exact task")
        snapshot = task["input_snapshot"]
        plan = task.get("brain_plan")
        unknowns = (
            list(plan["unknowns"])
            if plan
            else [
                "Legacy execution predates Document AI task structuring; this is a post-execution assessment only.",
                "Engineering adequacy and source authority require human verification.",
            ]
        )
        outputs = attrs.get("outputs", {})
        evidence = [n for n in snapshot["records"] if n["node_type"] == "evidence"]
        by_id = {n["node_id"]: n for n in evidence}
        contract_ok = isinstance(outputs, dict) and set(OUTPUTS[task["tool"]]).issubset(
            outputs
        )
        if contract_ok:
            try:
                contract_ok = self._check_output(task, outputs, by_id)
            except (KeyError, TypeError, AttributeError):
                contract_ok = False
        missing_text = [
            n["node_id"] for n in evidence if not n["attributes"].get("text")
        ]
        missing_locators = [
            n["node_id"]
            for n in evidence
            if not any(n["attributes"].get(k) for k in ("page", "section", "locator"))
        ]
        if missing_text:
            unknowns.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source text missing for "
                + ", ".join(missing_text)
            )
        if missing_locators:
            unknowns.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source locators missing for "
                + ", ".join(missing_locators)
            )
        provenance_ok = (
            attrs.get("inputs", {}).get("snapshot") == snapshot
            and attrs.get("method") == task["tool"]
            and attrs.get("tool_version") == task["tool_version"]
            and set(by_id).issubset(attrs.get("evidence_ids", []))
            and (not plan or attrs["inputs"].get("brain_plan") == plan)
        )
        warnings = outputs.get("warnings") if isinstance(outputs, dict) else None
        limits_ok = (
            isinstance(warnings, list)
            and bool(warnings)
            and all(isinstance(w, str) and bool(w.strip()) for w in warnings)
        )
        checks = [
            {
                "criterion": "source_provenance",
                "passed": provenance_ok,
                "detail": "Compare retained inputs, tool version and evidence references with the structured task.",
            },
            {
                "criterion": "output_contract",
                "passed": contract_ok,
                "detail": "Check the requested output fields, types, selected source identity and document-tool content.",
            },
            {
                "criterion": "source_traceability",
                "passed": bool(evidence) and not missing_text and not missing_locators,
                "detail": "Check source text and physical/section locators; requirement applicability remains explicit.",
            },
            {
                "criterion": "review_limits",
                "passed": limits_ok,
                "detail": "Retain output warnings and the requirement for final human engineering review.",
            },
        ]
        passed = all(c["passed"] for c in checks)
        assessment = {
            "task_digest": task["task_digest"],
            "summary": "Document-tool output checks passed; human verification required."
            if passed
            else "Output assessment found failed checks; rework or hold required.",
            "checks": checks,
            "unknowns": unknowns,
            "recommendation": "human_review" if passed else "rework",
            "evidence_ids": sorted(set(attrs["evidence_ids"]) & set(by_id)),
            "method": "engineering_document_ai.offline_output_contract.v1",
        }
        if plan and plan.get("lesson_limitations"):
            assessment["summary"] += " Lesson limitations: " + "; ".join(
                plan["lesson_limitations"]
            )
        return assessment

    def _check_output(self, task: dict, output: dict, evidence: dict) -> bool:
        tool, snapshot = task["tool"], task["input_snapshot"]
        if tool == "external_solver":
            return bool(
                self.execution.solver_catalog
                and self.execution.solver_catalog.check_retained_output(task, output)
            )
        if tool == "fem_linear_static":
            import hashlib

            from engineering_execution.fem import validate_fem_job

            job = task["parameters"]["job"]
            validate_fem_job(job)
            candidate = dict(output)
            retained_digest = candidate.pop("output_digest", None)
            computed_digest = hashlib.sha256(
                json.dumps(
                    candidate, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            ).hexdigest()
            model_digest = hashlib.sha256(
                json.dumps(
                    job, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            ).hexdigest()
            solver = output.get("solver", {})
            runtime = task.get("fem_binding", {}).get("runtime", {})
            output_runtime = {
                key: solver.get(key)
                for key in (
                    "name",
                    "package_version",
                    "platform_package",
                    "platform_package_version",
                    "native_binary_sha256",
                    "adapter_source_sha256",
                )
            }
            return bool(
                output.get("model_id") == job["model_id"]
                and output.get("model_digest") == model_digest
                and output.get("source_bindings") == job["source_bindings"]
                and output.get("production_approved") is False
                and output.get("human_engineering_approval_required") is True
                and output.get("solver", {}).get("status") == 0
                and output_runtime == runtime
                and output.get("solver", {}).get("diagnostics_capture")
                == "isolated_subprocess"
                and retained_digest == computed_digest
            )
        if tool == "compare_revision":
            base = evidence[task["parameters"]["base_evidence_id"]]
            head = evidence[task["parameters"]["head_evidence_id"]]
            lines_valid = all(
                isinstance(output[k], list)
                and all(isinstance(line, str) for line in output[k])
                for k in ("removed_lines", "added_lines")
            )
            if not lines_valid:
                return False
            retained = Counter(base["attributes"].get("text", "").splitlines())
            retained.subtract(output["removed_lines"])
            retained.update(output["added_lines"])
            return (
                output["base_evidence_id"] == base["node_id"]
                and output["head_evidence_id"] == head["node_id"]
                and output["base_revision"] == base["attributes"].get("revision")
                and output["head_revision"] == head["attributes"].get("revision")
                and all(
                    isinstance(output[k], list)
                    and all(isinstance(line, str) for line in output[k])
                    for k in ("removed_lines", "added_lines")
                )
                and all(
                    line in base["attributes"].get("text", "").splitlines()
                    for line in output["removed_lines"]
                )
                and all(
                    line in head["attributes"].get("text", "").splitlines()
                    for line in output["added_lines"]
                )
                and retained == Counter(head["attributes"].get("text", "").splitlines())
            )
        if tool == "validate_traceability":
            missing_text = sorted(
                n["node_id"]
                for n in evidence.values()
                if not n["attributes"].get("text")
            )
            missing_locators = sorted(
                n["node_id"]
                for n in evidence.values()
                if not any(
                    n["attributes"].get(k) for k in ("page", "section", "locator")
                )
            )
            return (
                type(output["evidence_count"]) is int
                and output["evidence_count"] == len(evidence)
                and isinstance(output["missing_text"], list)
                and sorted(output["missing_text"]) == missing_text
                and isinstance(output["missing_locators"], list)
                and sorted(output["missing_locators"]) == missing_locators
                and output["requirement_status"]
                == (
                    "linked"
                    if snapshot.get(
                        "source_context_requirement_ids",
                        snapshot["issue"]["requirement_ids"],
                    )
                    else "UNKNOWN / INSUFFICIENT INFORMATION: no requirements linked"
                )
            )
        return (
            output["schema_version"] == 1
            and output["issue"] == snapshot["issue"]
            and output["records"] == snapshot["records"]
            and output["human_review_required"] is True
        )
