from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService, SolverCatalog
from engineering_registry.identity import IdentityResolver
from engineering_registry.knowledge_stores import OrganizationalKnowledgeService
from engineering_registry.paths import organization_registry_path, project_registry_path
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore

from .execution_mode import (
    ExecutionMode,
    require_demo_adapters,
    require_test_adapters,
    resolve_mode,
)
from .project_run import ProjectRunService

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EngineeringAISystem:
    """One entry point for Registry memory/control, Document AI and V2 execution.

    The facade deliberately reuses the Registry database and service. It does not
    create a parallel memory store or grant authority to AI adapters.
    """

    def __init__(
        self,
        registry: RegistryService,
        brain: DocumentAIWorkflow,
        *,
        owns_store: bool = False,
        mode: ExecutionMode | str = ExecutionMode.GOVERNED,
        run_integrator: Callable[[dict], dict] | None = None,
        knowledge_generalizer: Callable[[dict], dict] | None = None,
        specialist_analysis: Callable[[str, dict], dict] | None = None,
        specialist_skills_dir: str | Path | None = None,
        organization_registry: RegistryService | None = None,
    ) -> None:
        if registry.store is not brain.registry.store:
            raise ValueError(
                "Engineering AI System components must share one Registry store"
            )
        resolved_mode = resolve_mode(mode)
        if resolved_mode == ExecutionMode.DEMO:
            require_demo_adapters(brain.analysis, brain.alignment_reviewer)
            if specialist_analysis is not None:
                require_demo_adapters(specialist_analysis)
        if resolved_mode == ExecutionMode.TEST:
            require_test_adapters(brain.analysis, brain.alignment_reviewer)
            if specialist_analysis is not None:
                require_test_adapters(specialist_analysis)
        self.registry = registry
        self.brain = brain
        self.execution = brain.execution
        self.specialist_analysis = specialist_analysis or brain.analysis
        self.specialist_skills_dir = specialist_skills_dir
        self._dispatch_lock = threading.Lock()
        self.project_runs = ProjectRunService(
            registry,
            brain,
            integrator=run_integrator,
            generalizer=knowledge_generalizer,
            specialist_analysis=self.specialist_analysis,
            specialist_skills_dir=specialist_skills_dir,
            execution_mode=resolved_mode,
            organization_registry=organization_registry,
        )
        self._owns_store = owns_store
        self.execution_mode = resolved_mode
        self.organization_registry = organization_registry
        if organization_registry is not None:
            registry.organizational_registry = organization_registry
        self.organizational_knowledge = (
            OrganizationalKnowledgeService(registry, organization_registry)
            if organization_registry is not None
            and registry.principal.role == "reviewer"
            else None
        )
        self._owns_organization_store = organization_registry is not None

    @classmethod
    def open_project(
        cls,
        registry_database: str | Path,
        project_id: str,
        principal: Principal,
        *,
        create: bool = False,
        analysis: Callable[[str, dict], dict] | None = None,
        alignment_reviewer: Callable[[dict], dict] | None = None,
        mode: ExecutionMode | str = ExecutionMode.GOVERNED,
        decision_registry: RegistryService | None = None,
        delegation_policy_id: str | None = None,
        solver_catalog: SolverCatalog | None = None,
        run_integrator: Callable[[dict], dict] | None = None,
        knowledge_generalizer: Callable[[dict], dict] | None = None,
        specialist_analysis: Callable[[str, dict], dict] | None = None,
        specialist_skills_dir: str | Path | None = None,
        organization_registry_database: str | Path | None = None,
        organization_id: str | None = None,
    ) -> EngineeringAISystem:
        """Connect the exact project Registry path and assemble the brain/hand services.

        A missing database is an error unless ``create=True`` is explicit. This avoids
        silently creating a second empty Registry for a project already in use.
        """
        if not _PROJECT_ID.fullmatch(project_id):
            raise ValueError("Project ID contains unsafe path characters")
        if project_id not in principal.project_ids:
            raise PermissionError("Principal has no grant for this project")
        resolved_mode = resolve_mode(mode)
        if resolved_mode == ExecutionMode.DEMO:
            require_demo_adapters(analysis, alignment_reviewer)
        if resolved_mode == ExecutionMode.TEST:
            require_test_adapters(analysis, alignment_reviewer)
        database = Path(registry_database).expanduser().resolve()
        if not database.is_file() and not create:
            raise FileNotFoundError(
                "Project Registry database is missing; pass its exact existing path "
                "or set create=True for a new project"
            )
        store = SQLiteGraphStore(database)
        try:
            store.bind_scope(project_id)
        except Exception:
            store.close()
            raise
        registry = RegistryService(store, principal)
        organization_registry = None
        if organization_registry_database is not None:
            if not organization_id or organization_id not in principal.organization_ids:
                store.close()
                raise PermissionError(
                    "An explicit grant for the configured organisation is required"
                )
            organization_database = (
                Path(organization_registry_database).expanduser().resolve()
            )
            if not organization_database.is_file() and not create:
                store.close()
                raise FileNotFoundError(
                    "Organisation Registry is missing; pass create=True for a new organisation"
                )
            organization_store = SQLiteGraphStore(organization_database)
            try:
                organization_store.bind_scope("ORG:" + organization_id)
            except Exception:
                organization_store.close()
                store.close()
                raise
            organization_registry = RegistryService(organization_store, principal)
        brain = DocumentAIWorkflow(
            registry,
            analysis=analysis,
            alignment_reviewer=alignment_reviewer,
            decision_registry=decision_registry,
            delegation_policy_id=delegation_policy_id,
            solver_catalog=solver_catalog,
        )
        return cls(
            registry,
            brain,
            owns_store=True,
            mode=resolved_mode,
            run_integrator=run_integrator,
            knowledge_generalizer=knowledge_generalizer,
            specialist_analysis=specialist_analysis,
            specialist_skills_dir=specialist_skills_dir,
            organization_registry=organization_registry,
        )

    @classmethod
    def open_project_from_data_root(
        cls,
        data_root: str | Path,
        project_id: str,
        principal: Principal,
        *,
        organization_id: str | None = None,
        **kwargs: Any,
    ) -> EngineeringAISystem:
        """Open the canonical physical Registry path for one isolated project."""
        organization_database = (
            organization_registry_path(data_root, organization_id)
            if organization_id is not None
            else None
        )
        return cls.open_project(
            project_registry_path(data_root, project_id),
            project_id,
            principal,
            organization_registry_database=organization_database,
            organization_id=organization_id,
            **kwargs,
        )

    def structure_task(
        self,
        project_id: str,
        task_id: str,
        issue_id: str,
        tool: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        return self.brain.structure_task(
            project_id, task_id, issue_id, tool, parameters
        )

    def record_task_decision(
        self,
        project_id: str,
        issue_id: str,
        decision_id: str,
        statement: str,
        rationale: str,
        evidence_ids: list[str],
        **details: Any,
    ) -> dict[str, Any]:
        """Persist a proposed Document AI task decision in this project's Registry."""
        return self.brain.record_task_decision(
            project_id,
            issue_id,
            decision_id,
            statement,
            rationale,
            evidence_ids,
            **details,
        )

    def record_lead_step(
        self,
        project_id: str,
        issue_id: str,
        note_id: str,
        statement: str,
        rationale: str,
        impact_flags: dict[str, str],
        **details: Any,
    ) -> dict[str, Any]:
        return self.brain.record_lead_step(
            project_id, issue_id, note_id, statement, rationale, impact_flags, **details
        )

    def review_plan(self, project_id: str, task_id: str) -> dict[str, Any]:
        return self.brain.review_plan_alignment(project_id, task_id)

    def authorize_execution(
        self,
        principal: Principal,
        project_id: str,
        task_id: str,
        task_digest: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Authorize the exact task through a separately authenticated human principal."""
        human_registry = RegistryService(self.registry.store, principal)
        human_registry.check_access(project_id, write=True, human=True)
        self._require_task_reviewer(project_id, task_id)
        return ExecutionService(
            human_registry, solver_catalog=self.execution.solver_catalog
        ).authorize_task(project_id, task_id, task_digest, rationale)

    def decide_specialist_plan(
        self,
        principal: Principal,
        project_id: str,
        run_id: str,
        plan_digest: str,
        disposition: str,
        rationale: str,
        conditions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record a human-only decision for a large discipline-specialist plan."""
        return self.project_runs.decide_specialist_plan(
            principal,
            project_id,
            run_id,
            plan_digest,
            disposition,
            rationale,
            conditions,
        )

    def confirm_specialist_plan_conditions(
        self,
        principal: Principal,
        project_id: str,
        run_id: str,
        plan_digest: str,
        satisfied_condition_ids: list[str],
        rationale: str,
    ) -> dict[str, Any]:
        """Confirm human completion of all plan approval conditions."""
        return self.project_runs.confirm_specialist_plan_conditions(
            principal,
            project_id,
            run_id,
            plan_digest,
            satisfied_condition_ids,
            rationale,
        )

    def execute_and_assess(self, project_id: str, task_id: str) -> dict[str, Any]:
        """Run the already-authorized bounded task, then let Document AI assess it."""
        self._require_task_reviewer(project_id, task_id)
        return self.brain.execute_and_assess(project_id, task_id)

    def _require_task_reviewer(self, project_id: str, task_id: str) -> None:
        """Only policy-bound Routine runs may execute without a governed Reviewer."""
        if (
            self.execution_mode != ExecutionMode.GOVERNED
            or self.brain.alignment_reviewer is not None
        ):
            return
        task = self.execution.get_task(project_id, task_id)
        if task.get("brain_plan", {}).get("execution_class") == "routine":
            from engineering_registry.project_runs import require_run_plan_gate

            require_run_plan_gate(self.registry, project_id, task)
            return
        raise ValueError(
            "An alignment reviewer is required for non-Routine Gate 1 and Gate 2"
        )

    def dispatch_ready_tasks(
        self, project_id: str, run_id: str, *, max_workers: int = 4
    ) -> dict[str, Any]:
        """Run approved ready batches with one SQLite connection per worker."""
        with self._dispatch_lock:
            return self._dispatch_ready_task_batches(
                project_id, run_id, max_workers=max_workers
            )

    def _dispatch_ready_task_batches(
        self, project_id: str, run_id: str, *, max_workers: int
    ) -> dict[str, Any]:
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= 16
        ):
            raise ValueError("max_workers must be an integer from 1 to 16")
        self.registry.check_access(project_id, write=True)
        database = self.registry.store.path
        if database == ":memory:" and max_workers != 1:
            raise ValueError(
                "Concurrent dispatch requires a file-backed Registry; use max_workers=1"
            )
        if max_workers > 1:
            adapters = {
                "Document AI analysis": self.brain.analysis,
                "independent Reviewer": self.brain.alignment_reviewer,
                "discipline specialist analysis": self.specialist_analysis,
            }
            unsupported = [
                name
                for name, adapter in adapters.items()
                if adapter is not None
                and getattr(adapter, "supports_concurrent_calls", False) is not True
            ]
            if unsupported:
                raise ValueError(
                    "Parallel dispatch requires concurrency-capable adapters: "
                    + ", ".join(unsupported)
                    + "; use max_workers=1 or configure concurrency-safe adapters"
                )
        run = self.project_runs._run(project_id, run_id)
        attempted: set[str] = set()
        batches: list[dict[str, Any]] = []
        while True:
            schedule = self.project_runs.get_schedule(project_id, run_id)
            ready = [
                task_id
                for task_id in schedule["ready_task_ids"]
                if task_id not in attempted
            ][:max_workers]
            if not ready:
                break
            from engineering_registry.project_runs import require_run_plan_gate

            for task_id in ready:
                require_run_plan_gate(
                    self.registry,
                    project_id,
                    self.execution.get_task(project_id, task_id),
                )
            attempted.update(ready)
            results: dict[str, Any] = {}
            if database == ":memory:":
                for task_id in ready:
                    try:
                        results[task_id] = self.project_runs.execute_task(
                            project_id, run_id, task_id
                        )
                    except Exception as exc:
                        results[task_id] = {"error": f"{type(exc).__name__}: {exc}"}
            else:
                with ThreadPoolExecutor(
                    max_workers=min(max_workers, len(ready)),
                    thread_name_prefix="engineering-specialist",
                ) as pool:
                    futures = {
                        pool.submit(
                            self._execute_task_isolated,
                            project_id,
                            run_id,
                            task_id,
                        ): task_id
                        for task_id in ready
                    }
                    for future in as_completed(futures):
                        task_id = futures[future]
                        try:
                            results[task_id] = future.result()
                        except Exception as exc:
                            results[task_id] = {"error": f"{type(exc).__name__}: {exc}"}
            batches.append(
                {
                    "task_ids": ready,
                    "outcomes": [
                        self._dispatch_outcome_summary(
                            project_id, run_id, task_id, results[task_id]
                        )
                        for task_id in ready
                    ],
                }
            )
        return {
            "run_id": run_id,
            "plan_digest": run["plan_digest"],
            "batches": batches,
            "schedule": self.project_runs.get_schedule(project_id, run_id),
        }

    def _dispatch_outcome_summary(
        self, project_id: str, run_id: str, task_id: str, outcome: dict[str, Any]
    ) -> dict[str, Any]:
        run = self.project_runs._run(project_id, run_id)
        if task_id not in run["task_ids"]:
            raise ValueError("Task is not part of this project run")
        task = self.execution.get_task(project_id, task_id)
        result = outcome.get("result") if isinstance(outcome, dict) else None
        assessment = outcome.get("assessment") if isinstance(outcome, dict) else None
        summary = {
            "task_id": task_id,
            "state": task["state"],
            "task_digest": task["task_digest"],
            "result_id": result.get("node_id") if isinstance(result, dict) else None,
            "assessment_id": (
                assessment.get("node_id") if isinstance(assessment, dict) else None
            ),
        }
        if task.get("attempts") and task["state"] == "failed":
            summary["error"] = task["attempts"][-1].get("error", "Task failed")[:1000]
        if outcome.get("error"):
            summary["error"] = str(outcome["error"])[:1000]
        if outcome.get("specialist_proposal"):
            proposal = outcome["specialist_proposal"]
            summary["specialist_output_id"] = proposal["output_id"]
            summary["specialist_proposal_digest"] = proposal["proposal_digest"]
        return summary

    def _execute_task_isolated(
        self, project_id: str, run_id: str, task_id: str
    ) -> dict[str, Any]:
        """Rebuild worker services against a private connection to the same Registry."""
        store = SQLiteGraphStore(self.registry.store.path)
        try:
            store.bind_scope(project_id)
            registry = RegistryService(store, self.registry.principal)
            source_delegate = self.brain.decision_registry
            decision_registry = (
                RegistryService(store, source_delegate.principal)
                if source_delegate is not None
                else None
            )
            brain = DocumentAIWorkflow(
                registry,
                analysis=self.brain.analysis,
                alignment_reviewer=self.brain.alignment_reviewer,
                decision_registry=decision_registry,
                delegation_policy_id=self.brain.delegation_policy_id,
                solver_catalog=self.execution.solver_catalog,
            )
            worker_runs = ProjectRunService(
                registry,
                brain,
                integrator=self.project_runs.integrator,
                generalizer=self.project_runs.generalizer,
                specialist_analysis=self.specialist_analysis,
                specialist_skills_dir=self.specialist_skills_dir,
                execution_mode=self.execution_mode,
            )
            return worker_runs.execute_task(project_id, run_id, task_id)
        finally:
            store.close()

    def create_mcp_server(self, identity_resolver: IdentityResolver):
        """Compose the bounded MCP host with this system's trusted run services.

        The resolver must derive its principal from host-verified identity and grants.
        Human-only decisions remain on separate trusted routes.
        """
        from engineering_registry.mcp_server import create_server

        return create_server(
            self.registry,
            workflow_factory=lambda _registry: self.brain,
            project_run_service=self.project_runs,
            project_run_dispatcher=self.dispatch_ready_tasks,
            identity_resolver=identity_resolver,
        )

    def verify_result(
        self,
        principal: Principal,
        project_id: str,
        result_id: str,
        result_digest: str,
        evidence_ids: list[str],
        rationale: str,
        *,
        assessment_id: str | None = None,
        assessment_digest: str | None = None,
    ) -> dict[str, Any]:
        human_registry = RegistryService(self.registry.store, principal)
        return human_registry.verify_result(
            project_id,
            result_id,
            result_digest,
            evidence_ids,
            rationale,
            assessment_id=assessment_id,
            assessment_digest=assessment_digest,
        )

    def decide_result(
        self,
        principal: Principal,
        project_id: str,
        result_id: str,
        result_digest: str,
        assessment_id: str,
        assessment_digest: str,
        disposition: str,
        evidence_ids: list[str],
        rationale: str,
    ) -> dict[str, Any]:
        human_registry = RegistryService(self.registry.store, principal)
        return human_registry.decide_result(
            project_id,
            result_id,
            result_digest,
            assessment_id,
            assessment_digest,
            disposition,
            evidence_ids,
            rationale,
        )

    def close(self) -> None:
        if self._owns_store:
            self.registry.store.close()
        if self._owns_organization_store and self.organization_registry is not None:
            self.organization_registry.store.close()
