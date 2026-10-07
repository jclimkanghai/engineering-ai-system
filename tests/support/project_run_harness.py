"""Shared end-to-end fixtures for Brain, Reviewer and ProjectRun tests."""

from __future__ import annotations

import pytest

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.demo import seed_demo
from engineering_registry.models import EngineeringIssue
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore
from tests.support.delegation_harness import mandate_definition, policy_definition


def alignment_report(context, status="aligned", **changes):
    if context.get("phase") == "plan":
        status = "aligned"
        changes = {}
    if context.get("phase") == "outcome":
        assert "document_ai_assessment" in context
    else:
        assert "document_ai_assessment" not in context
    assert "technical_assessment" not in context
    assert (
        context["client_mandate"]["definition"]["objective"]
        == mandate_definition()["objective"]
    )
    return {
        "status": status,
        "summary": "The excerpt comparison matches the client's requested text comparison.",
        "checks": [
            {
                "criterion": criterion,
                "status": status,
                "detail": "Compared the original client objective, task direction and retained output.",
                "evidence_ids": ["evidence:EA", "evidence:EB"],
            }
            for criterion in (
                ("plan_alignment",)
                if context.get("phase") == "plan"
                else ("plan_alignment", "execution_direction", "outcome_alignment")
            )
        ],
        "unknowns": [],
        "evidence_ids": ["evidence:EA", "evidence:EB"],
        "method": "synthetic-alignment-fixture-v1",
        "model": "offline-fixture",
        "response_id": "synthetic-response",
        "request_digest": digest(context),
        **changes,
    }


@pytest.fixture
def configured(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("technical-lead", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("synthetic-human", frozenset({"DEMO"}), "reviewer")
        )
        for revision, evidence, status in (
            ("document_revision:BASIS-A%3AA", "evidence:EA", "superseded"),
            ("document_revision:BASIS-B%3AB", "evidence:EB", "current"),
        ):
            view = human.document_control("DEMO", revision)
            human.review_document_control(
                "DEMO",
                revision,
                view["control_digest"],
                status,
                [evidence],
                "Synthetic authority fixture",
            )
        from engineering_registry.extended_demo import synthetic_client_project_brief

        human.record_client_project_brief(
            "DEMO", "B1", synthetic_client_project_brief(["evidence:EA", "evidence:EB"])
        )
        human.record_client_mandate("DEMO", "M1", "issue:F-LOAD", mandate_definition())
        human.approve_delegation_policy("DEMO", "P1", policy_definition())
        yield database, agent, human


@pytest.fixture
def services(tmp_path):
    """Minimal Registry principal fixture for delegation contract tests."""
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("brain", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("human", frozenset({"DEMO"}), "reviewer")
        )
        yield database, agent, human


def workflow_for(configured, reviewer=alignment_report, **kwargs):
    _, agent, _ = configured
    delegate = RegistryService(
        agent.store,
        Principal("technical-lead-delegate", frozenset({"DEMO"}), "delegate"),
    )
    return DocumentAIWorkflow(
        agent,
        alignment_reviewer=reviewer,
        decision_registry=delegate,
        delegation_policy_id="P1",
        **kwargs,
    )


def prepare(configured, workflow, task_id="T"):
    _, _, human = configured
    task = workflow.structure_task(
        "DEMO",
        task_id,
        "issue:F-LOAD",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )
    ExecutionService(human).authorize_task(
        "DEMO", task_id, task["task_digest"], "Synthetic execution authorization"
    )
    return task


@pytest.fixture
def run_fixture(tmp_path, configured):
    _, agent, human = configured
    agent.store.save_issue(
        EngineeringIssue(
            "issue:RUN",
            "DEMO",
            "Low-risk text comparison",
            risk_level="low",
            confidence="high",
            evidence_ids=["evidence:EA", "evidence:EB"],
            requirement_ids=["requirement:R-LOAD"],
        )
    )
    human.record_client_mandate("DEMO", "M-RUN", "issue:RUN", mandate_definition())
    return configured


def integrate(context):
    requirements = sorted(
        {
            value
            for item in context["plan"]["tasks"]
            for value in item["brain_plan"]["requirement_ids"]
        }
    )
    acceptance = [
        (item["task_id"], criterion)
        for item in context["plan"]["tasks"]
        for criterion in item["brain_plan"]["acceptance_criteria"]
    ]
    complete = all(
        item["state"] == "succeeded" and item["assessment"]
        for item in context["task_outcomes"]
    )
    checks = [
        {
            "domain": domain,
            "status": "consistent" if complete else "unknown",
            "detail": "Synthetic integration fixture checked this dimension."
            if complete
            else "Task output is incomplete.",
            "task_ids": [item["task_id"] for item in context["task_outcomes"]],
            "evidence_ids": ["evidence:EA", "evidence:EB"],
        }
        for domain in (
            "design_basis",
            "shared_assumptions",
            "interfaces",
            "source_revisions",
            "dependencies",
            "contradictions",
            "residual_risks",
        )
    ]
    assessment = {
        "schema_version": 1,
        "status": "clear" if complete else "insufficient_information",
        "requirement_coverage": [
            {
                "requirement_id": rid,
                "status": "met" if complete else "unknown",
                "detail": "Synthetic integration fixture checked requirement coverage.",
                "evidence_ids": ["evidence:EA"],
            }
            for rid in requirements
        ],
        "acceptance_coverage": [
            {
                "task_id": task_id,
                "criterion": criterion,
                "status": "met" if complete else "unknown",
                "detail": "Synthetic integration fixture checked this criterion.",
                "evidence_ids": ["evidence:EA"],
            }
            for task_id, criterion in acceptance
        ],
        "cross_task_checks": checks,
        "assumptions": [],
        "limitations": [],
        "unknowns": [] if complete else ["At least one V2 output is incomplete."],
        "residual_risks": [],
    }
    return {
        "summary": "Both controlled comparisons are complete.",
        "recommendation": "Accept the combined result."
        if complete
        else "Hold pending completion of all V2 outputs.",
        "result_ids": [
            item["result_id"] for item in context["task_outcomes"] if item["result_id"]
        ],
        "evidence_ids": ["evidence:EA", "evidence:EB"],
        "unknowns": [],
        "conflicts": [],
        "integrated_assessment": assessment,
    }
