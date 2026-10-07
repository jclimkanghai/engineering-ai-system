"""Runnable synthetic fixtures; no provider, live project or professional approval."""

from __future__ import annotations

import json
from pathlib import Path

from .demo import seed_demo
from .models import GraphNode, NodeType
from .service import Principal, RegistryService, digest
from .store import SQLiteGraphStore


def synthetic_client_project_brief(evidence_ids: list[str]) -> dict:
    """Offline control fixture only; never an actual client project definition."""
    from .client_brief import SECTIONS

    return {
        "title": "Synthetic client project context fixture",
        "sections": {
            section: [
                {
                    "statement_id": section + ":synthetic",
                    "text": "Synthetic "
                    + section
                    + " fixture; evaluate supplied evidence only.",
                    "basis": "explicit",
                    "evidence_ids": list(evidence_ids),
                }
            ]
            for section in sorted(SECTIONS)
        },
        "evidence_ids": list(evidence_ids),
        "unknowns": [],
    }


def _alignment_fixture(context: dict) -> dict:
    return {
        "status": "aligned",
        "summary": "Synthetic excerpt-comparison alignment fixture.",
        "checks": [
            {
                "criterion": criterion,
                "status": "aligned",
                "detail": "Synthetic client brief requests literal excerpt changes only.",
                "evidence_ids": ["evidence:EA", "evidence:EB"],
            }
            for criterion in (
                ("plan_alignment",)
                if context.get("phase") == "plan"
                else (
                    "plan_alignment",
                    "execution_direction",
                    "outcome_alignment",
                )
            )
        ],
        "unknowns": [],
        "evidence_ids": ["evidence:EA", "evidence:EB"],
        "method": "synthetic-fixture-v1",
        "model": "offline-fixture",
        "response_id": None,
        "request_digest": digest(context),
    }


def _analysis_fixture(phase: str, context: dict) -> dict:
    return {
        "producer": "engineering_document_ai",
        "phase": phase,
        "model": "offline-fixture",
        "response_id": None,
        "request_digest": digest(context),
        "findings": [],
        "usage": {},
        "lesson_context": context["approved_lessons"],
        "human_review_required": True,
    }


def _stress_fixture(quantities: dict, _context: dict) -> dict:
    return {
        "values": {
            "stress": {
                "value": quantities["force"]["value"] / quantities["area"]["value"],
                "unit": "Pa",
            }
        },
        "warnings": [],
    }


def synthetic_solver_catalog():
    """Demonstrate adapter registration; this is not a released engineering method."""
    from engineering_execution import SolverCatalog

    catalog = SolverCatalog()
    catalog.register(
        {
            "solver_id": "synthetic-axial-stress",
            "version": "1",
            "skill": {
                "name": "synthetic-stress-fixture",
                "version": "1",
                "artifact_digest": "a" * 64,
            },
            "method": "Axial stress = force / area; synthetic adapter demonstration only.",
            "applicability_limits": [
                "Uniform direct load; excludes bending, buckling and design capacity."
            ],
            "assumptions": ["Consistent SI units and positive area."],
            "inputs": {
                "force": {"unit": "N", "minimum": 0, "maximum": 1e9},
                "area": {"unit": "m2", "minimum": 0.000001, "maximum": 100},
            },
            "outputs": {"stress": {"unit": "Pa", "minimum": 0, "maximum": 1e15}},
            "validation_cases": [
                {
                    "inputs": {
                        "force": {"value": 1000, "unit": "N"},
                        "area": {"value": 0.01, "unit": "m2"},
                    },
                    "expected_outputs": {"stress": {"value": 100000, "unit": "Pa"}},
                    "relative_tolerance": 1e-9,
                    "absolute_tolerance": 0.001,
                },
                {
                    "inputs": {
                        "force": {"value": 0, "unit": "N"},
                        "area": {"value": 0.1, "unit": "m2"},
                    },
                    "expected_outputs": {"stress": {"value": 0, "unit": "Pa"}},
                    "relative_tolerance": 0,
                    "absolute_tolerance": 0,
                },
            ],
        },
        _stress_fixture,
    )
    return catalog


def run_extended_smoke(destination: str | Path) -> dict:
    from engineering_document_ai_brain import DocumentAIWorkflow
    from engineering_execution import ExecutionService

    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "extended-synthetic.sqlite"
    if database.exists():
        raise ValueError(
            "Use a fresh directory for the synthetic workflow; existing databases are preserved"
        )
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(
            store, Principal("synthetic-brain", frozenset({"DEMO"}))
        )
        human = RegistryService(
            store, Principal("synthetic-test-reviewer", frozenset({"DEMO"}), "reviewer")
        )
        delegate = RegistryService(
            store, Principal("synthetic-ai-delegate", frozenset({"DEMO"}), "delegate")
        )
        for revision, evidence, status in (
            ("document_revision:BASIS-A%3AA", "evidence:EA", "superseded"),
            ("document_revision:BASIS-B%3AB", "evidence:EB", "current"),
        ):
            source = human.document_control("DEMO", revision)
            human.review_document_control(
                "DEMO",
                revision,
                source["control_digest"],
                status,
                [evidence],
                "Synthetic source authority fixture",
            )
        human.record_client_project_brief(
            "DEMO",
            "synthetic-project-brief",
            synthetic_client_project_brief(["evidence:EA", "evidence:EB"]),
        )
        human.record_client_mandate(
            "DEMO",
            "synthetic-mandate",
            "issue:F-LOAD",
            {
                "objective": "Identify literal changes between the supplied excerpts.",
                "scope": "Excerpt comparison only; no design acceptance or contractual conclusion.",
                "acceptance_criteria": [
                    "Retain removed/added wording and both source identities."
                ],
                "evidence_ids": ["evidence:EA", "evidence:EB"],
                "allowed_tools": ["compare_revision"],
                "risk_level": "low",
                "importance_level": "low",
                "simple": True,
                "reversible": True,
                "consequence_domains": [],
                "unknowns": [],
            },
        )
        human.approve_delegation_policy(
            "DEMO",
            "synthetic-policy",
            {
                "allowed_tools": ["compare_revision"],
                "allowed_dispositions": ["accept", "rework", "hold"],
                "allowed_solver_ids": [],
                "rationale": "Synthetic routine comparison delegation fixture.",
            },
        )
        workflow = DocumentAIWorkflow(
            agent,
            alignment_reviewer=_alignment_fixture,
            decision_registry=delegate,
            delegation_policy_id="synthetic-policy",
        )
        task = workflow.structure_task(
            "DEMO",
            "synthetic-comparison",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )
        ExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Synthetic execution fixture"
        )
        outcome = workflow.execute_and_assess("DEMO", task["task_id"])
        if (
            not outcome.get("decision")
            or outcome["decision"]["attributes"]["disposition"] != "accept"
        ):
            raise RuntimeError("Synthetic delegated decision loop failed")
        agent.register(
            GraphNode(
                "evidence:solver-inputs",
                "DEMO",
                NodeType.EVIDENCE,
                "Synthetic quantities",
                {
                    "text": "Force is 20000 N; area is 0.01 m2.",
                    "locator": "synthetic-case-1",
                    "quantities": {
                        "force": {"value": 20000, "unit": "N"},
                        "area": {"value": 0.01, "unit": "m2"},
                    },
                },
            )
        )
        agent.propose_issue(
            "DEMO",
            "issue:synthetic-solver",
            "Synthetic stress adapter example",
            ["evidence:solver-inputs"],
        )
        catalog = synthetic_solver_catalog()
        solver_workflow = DocumentAIWorkflow(
            agent, analysis=_analysis_fixture, solver_catalog=catalog
        )
        solver_task = solver_workflow.structure_task(
            "DEMO",
            "synthetic-solver",
            "issue:synthetic-solver",
            "external_solver",
            {
                "solver_id": "synthetic-axial-stress",
                "quantities": {
                    "force": {"value": 20000, "unit": "N"},
                    "area": {"value": 0.01, "unit": "m2"},
                },
                "source_evidence_ids": {
                    "force": "evidence:solver-inputs",
                    "area": "evidence:solver-inputs",
                },
            },
        )
        ExecutionService(human, solver_catalog=catalog).authorize_task(
            "DEMO",
            solver_task["task_id"],
            solver_task["task_digest"],
            "Synthetic method fixture",
        )
        calculated = solver_workflow.execute_and_assess("DEMO", solver_task["task_id"])
        outputs = calculated["result"]["attributes"]["outputs"]
        if outputs["values"]["stress"] != {"value": 2000000, "unit": "Pa"}:
            raise RuntimeError("Synthetic external solver fixture failed")
        report = {
            "synthetic_only": True,
            "models_called": False,
            "delegated_disposition": outcome["decision"]["attributes"]["disposition"],
            "authority": outcome["decision"]["attributes"]["authority"],
            "human_verification_count": len(agent.list_records("DEMO", "verification")),
            "alignment_status": outcome["alignment_review"]["attributes"]["status"],
            "stress": outputs["values"]["stress"],
            "solver_benchmark_count": outputs["solver"]["validation"]["case_count"],
            "delegated_decision": outcome["decision"],
            "alignment_review": outcome["alignment_review"],
            "solver_result": calculated["result"],
            "solver_assessment": calculated["assessment"],
        }
    (directory / "extended-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report
