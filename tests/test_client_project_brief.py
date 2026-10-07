"""Project intent survives task boundaries and invalidates stale authority."""

from copy import deepcopy

import pytest

from engineering_registry.extended_demo import synthetic_client_project_brief
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore
from tests.support.delegation_harness import mandate_definition
from tests.support.project_run_harness import (
    alignment_report,
    prepare,
    workflow_for,
)


def test_only_human_can_record_project_intent(services):
    _, agent, human = services
    definition = synthetic_client_project_brief(["evidence:EA"])
    with pytest.raises(PermissionError):
        agent.record_client_project_brief("DEMO", "B1", definition)
    brief = human.record_client_project_brief("DEMO", "B1", definition)
    assert brief["approved_by"] == "human"
    assert agent.client_project_brief("DEMO") == brief


def test_brief_immutable_restart_and_old_replay_cannot_reactivate(services):
    database, _, human = services
    first = synthetic_client_project_brief(["evidence:EA"])
    b1 = human.record_client_project_brief("DEMO", "B1", first)
    second = deepcopy(first)
    second["title"] = "Revised client project context"
    b2 = human.record_client_project_brief("DEMO", "B2", second)
    assert human.record_client_project_brief("DEMO", "B1", first) == b1
    assert human.client_project_brief("DEMO") == b2
    with pytest.raises(ValueError, match="immutable"):
        human.record_client_project_brief("DEMO", "B1", second)
    with SQLiteGraphStore(database) as store:
        reader = RegistryService(
            store, Principal("reader", frozenset({"DEMO"}), "reader")
        )
        assert reader.client_project_brief("DEMO") == b2


@pytest.mark.parametrize(
    "case",
    [
        "missing_section",
        "empty_section",
        "duplicate",
        "no_explicit_evidence",
        "foreign",
        "bad_basis",
    ],
)
def test_invalid_project_briefs_fail_closed(services, case):
    _, _, human = services
    definition = synthetic_client_project_brief(["evidence:EA"])
    entry = definition["sections"]["purpose"][0]
    if case == "missing_section":
        definition["sections"].pop("scope")
    elif case == "empty_section":
        definition["sections"]["scope"] = []
    elif case == "duplicate":
        definition["sections"]["scope"][0]["statement_id"] = entry["statement_id"]
    elif case == "no_explicit_evidence":
        entry["evidence_ids"] = []
    elif case == "foreign":
        entry["evidence_ids"] = ["evidence:other-project"]
    else:
        entry["basis"] = "AI-confirmed"
    with pytest.raises(ValueError):
        human.record_client_project_brief("DEMO", "B1", definition)
    assert human.client_project_brief("DEMO") is None


def test_inferred_assumed_and_unknown_needs_stay_unresolved(services):
    _, _, human = services
    definition = synthetic_client_project_brief(["evidence:EA"])
    for section, basis in (
        ("purpose", "inferred"),
        ("constraints", "assumed"),
        ("priorities", "unknown"),
    ):
        definition["sections"][section][0].update(basis=basis, evidence_ids=[])
    brief = human.record_client_project_brief("DEMO", "B1", definition)
    assert all(
        any(basis in item for item in brief["unknowns"])
        for basis in ("inferred", "assumed", "unknown")
    )
    assert brief["definition"] == definition


def test_changed_source_control_invalidates_project_brief(services):
    _, _, human = services
    human.record_client_project_brief(
        "DEMO", "B1", synthetic_client_project_brief(["evidence:EA"])
    )
    view = human.document_control("DEMO", "document_revision:BASIS-A%3AA")
    human.review_document_control(
        "DEMO",
        "document_revision:BASIS-A%3AA",
        view["control_digest"],
        "current",
        ["evidence:EA"],
        "Changed client source authority",
    )
    with pytest.raises(ValueError, match="brief is stale"):
        human.client_project_brief("DEMO")


def test_project_change_requires_new_mandate_plan_and_review(configured):
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    review = workflow.review_plan_alignment("DEMO", "T")
    revised = synthetic_client_project_brief(["evidence:EA", "evidence:EB"])
    revised["sections"]["priorities"][0]["text"] = "Changed client project priority"
    brief = configured[2].record_client_project_brief("DEMO", "B2", revised)
    with pytest.raises(ValueError, match="brief changed"):
        workflow.execution.execute("DEMO", "T")
    assert workflow.execution.get_task("DEMO", "T")["attempts"] == []
    configured[2].record_client_mandate(
        "DEMO", "M2", "issue:F-LOAD", mandate_definition()
    )
    new_task = prepare(configured, workflow, "T2")
    new_review = workflow.review_plan_alignment("DEMO", "T2")
    assert new_task["brain_plan"]["client_project_brief"] == brief
    assert new_review["node_id"] != review["node_id"]
    assert (
        workflow.execute_and_assess("DEMO", "T2")["decision"]["attributes"][
            "client_project_brief_digest"
        ]
        == brief["brief_digest"]
    )


def test_both_reviews_receive_and_can_cite_project_evidence_outside_task(configured):
    _, agent, human = configured
    agent.register(
        GraphNode(
            "evidence:CLIENT",
            "DEMO",
            NodeType.EVIDENCE,
            "Synthetic client project brief",
            {
                "text": "Client objective: improve maintainability. Preserve interface access in all work packages.",
                "locator": "synthetic-client-brief section 1",
            },
        )
    )
    definition = synthetic_client_project_brief(["evidence:CLIENT"])
    definition["sections"]["purpose"][0]["text"] = "Improve maintainability"
    human.record_client_project_brief("DEMO", "B2", definition)
    human.record_client_mandate("DEMO", "M2", "issue:F-LOAD", mandate_definition())
    phases = []

    def reviewer(context):
        phases.append(context.get("phase", "outcome"))
        assert (
            context["client_project_brief"]["definition"]["sections"]["purpose"][0][
                "text"
            ]
            == "Improve maintainability"
        )
        assert "evidence:CLIENT" not in {
            n["node_id"] for n in context["source_snapshot"]["records"]
        }
        report = alignment_report(context)
        report["evidence_ids"].append("evidence:CLIENT")
        report["checks"][0]["evidence_ids"] = ["evidence:CLIENT"]
        report["request_digest"] = digest(context)
        return report

    workflow = workflow_for(configured, reviewer)
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert phases == ["plan", "outcome"]
    assert (
        "evidence:CLIENT" in outcome["alignment_review"]["attributes"]["evidence_ids"]
    )


def test_live_adapter_receives_project_evidence_and_big_picture_instructions(
    configured,
):
    from engineering_registry.alignment import alignment_context
    from pipelines.llm.models import LLMResponse
    from pipelines.review.alignment_workflow import build_alignment_reviewer
    from tests.support.reasoning_harness import finding

    agent, human = configured[1:]
    agent.register(
        GraphNode(
            "evidence:CLIENT",
            "DEMO",
            NodeType.EVIDENCE,
            "Synthetic wider client source",
            {
                "text": "Client requires maintenance access",
                "locator": "Client section 1",
            },
        )
    )
    human.record_client_project_brief(
        "DEMO", "B2", synthetic_client_project_brief(["evidence:CLIENT"])
    )
    human.record_client_mandate("DEMO", "M2", "issue:F-LOAD", mandate_definition())
    workflow = workflow_for(configured)
    prepare(configured, workflow)

    class Adapter:
        def analyse(self, request):
            assert "overall project" in request.instructions
            assert request.context["client_project_brief"]["brief_id"] == "B2"
            assert "evidence:CLIENT" in {e["evidence_id"] for e in request.evidence}
            item = finding(status="confirmed")
            item.update(
                finding_id="plan_alignment",
                risk_level="low",
                uncertainties=[],
                risk_dimensions=["alignment:aligned"],
            )
            return LLMResponse("offline", "review", [item])

    context = alignment_context(configured[1], "DEMO", "T", phase="plan")
    assert build_alignment_reviewer(Adapter())(context)["status"] == "aligned"


def test_missing_project_brief_blocks_review_required_planning(services):
    _, agent, human = services
    human.record_client_mandate("DEMO", "M1", "issue:F-LOAD", mandate_definition())
    from engineering_document_ai_brain import DocumentAIWorkflow

    workflow = DocumentAIWorkflow(agent, alignment_reviewer=alignment_report)
    with pytest.raises(ValueError, match="requires the client project brief"):
        workflow.structure_task(
            "DEMO",
            "T",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )


def test_brief_read_views_expose_staleness_without_hiding_history(services):
    _, agent, human = services
    assert agent.client_project_brief_view("DEMO")["status"] == "missing"
    brief = human.record_client_project_brief(
        "DEMO", "B1", synthetic_client_project_brief(["evidence:EA"])
    )
    assert agent.client_project_brief_view("DEMO")["status"] == "current"
    evidence = human.evidence_review("DEMO", "evidence:EA")
    human.review_evidence(
        "DEMO",
        "evidence:EA",
        evidence["review_digest"],
        "insufficient",
        "Synthetic changed evidence review",
    )
    view = agent.client_project_brief_view("DEMO")
    assert view["status"] == "stale" and view["brief"] == brief
    with pytest.raises(PermissionError):
        agent.client_project_brief_view("OTHER")


def test_unknown_project_context_cannot_be_autoaccepted(configured):
    _, _, human = configured
    definition = synthetic_client_project_brief(["evidence:EA", "evidence:EB"])
    definition["sections"]["priorities"][0]["basis"] = "inferred"
    human.record_client_project_brief("DEMO", "B2", definition)
    human.record_client_mandate("DEMO", "M2", "issue:F-LOAD", mandate_definition())
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert outcome["decision"] is None
    assert (
        "unknown" in outcome["escalation"].lower()
        or "accept" in outcome["escalation"].lower()
    )


def test_broad_client_evidence_decision_does_not_expand_task_inputs(configured):
    _, agent, human = configured
    agent.register(
        GraphNode(
            "evidence:CLIENT",
            "DEMO",
            NodeType.EVIDENCE,
            "Synthetic client interface requirement",
            {"text": "Preserve maintenance access", "locator": "Client brief 1"},
        )
    )
    human.record_client_project_brief(
        "DEMO", "B2", synthetic_client_project_brief(["evidence:CLIENT"])
    )
    human.record_client_mandate("DEMO", "M2", "issue:F-LOAD", mandate_definition())

    def reviewer(context):
        report = alignment_report(context)
        if context.get("phase") != "plan":
            report["status"] = "misaligned"
            for check in report["checks"]:
                check["status"] = "misaligned"
                check["evidence_ids"] = ["evidence:CLIENT"]
            report["evidence_ids"] = ["evidence:CLIENT"]
        return report

    workflow = workflow_for(configured, reviewer)
    task = prepare(configured, workflow)
    snapshot = agent.task_context("DEMO", "issue:F-LOAD")["execution_snapshot"]
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert outcome["decision"]["attributes"]["disposition"] == "rework"
    assert outcome["decision"]["attributes"]["evidence_ids"] == ["evidence:CLIENT"]
    assert agent.task_context("DEMO", "issue:F-LOAD")["execution_snapshot"] == snapshot
    assert (
        workflow.execution.get_task("DEMO", "T")["input_digest"] == task["input_digest"]
    )
    assert workflow.execute_and_assess("DEMO", "T")["decision"] == outcome["decision"]


def test_brief_change_during_reviewer_call_discards_stale_report(configured):
    def reviewer(context):
        revised = synthetic_client_project_brief(["evidence:EA", "evidence:EB"])
        revised["title"] = "Client changed project direction during review"
        configured[2].record_client_project_brief("DEMO", "B2", revised)
        return alignment_report(context)

    workflow = workflow_for(configured, reviewer)
    with pytest.raises(ValueError, match="brief changed or is missing"):
        workflow.structure_task(
            "DEMO",
            "T",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )
    assert workflow.execution.get_task("DEMO", "T")["attempts"] == []
    assert configured[1].list_records("DEMO", "alignment_review") == []
