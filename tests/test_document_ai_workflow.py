"""Catch missing brain handoffs, assessment bypass and lost human outcome history."""

import copy

import pytest

from engineering_execution import ExecutionService
from engineering_registry.demo import seed_demo
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore


@pytest.fixture
def flow(tmp_path):
    from engineering_document_ai_brain import DocumentAIWorkflow

    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        brain = RegistryService(store, Principal("document-ai", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        yield database, brain, human, DocumentAIWorkflow(brain)


def structured(workflow, task_id="T", tool="compare_revision", parameters=None):
    return workflow.structure_task(
        "DEMO",
        task_id,
        "issue:F-LOAD",
        tool,
        parameters
        if parameters is not None
        else {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )


def executed(flow, task_id="T"):
    _, brain, human, workflow = flow
    task = structured(workflow, task_id)
    ExecutionService(human).authorize_task(
        "DEMO", task_id, task["task_digest"], "Scope checked"
    )
    result = ExecutionService(brain).execute("DEMO", task_id)
    return task, result


def decide(
    human,
    result,
    assessment,
    disposition="accept",
    rationale="Sources and output checked",
):
    return human.decide_result(
        "DEMO",
        result["node_id"],
        digest(result),
        assessment["node_id"],
        digest(assessment),
        disposition,
        ["evidence:EB"],
        rationale,
    )


def test_brain_structures_source_grounded_task_and_retains_criteria(flow):
    _, _, _, workflow = flow
    task = structured(workflow)
    plan = task["brain_plan"]
    assert plan["producer"] == "engineering_document_ai"
    assert plan["objective"] and plan["scope"] and plan["acceptance_criteria"]
    assert plan["evidence_ids"] == ["evidence:EA", "evidence:EB"]
    assert plan["requirement_ids"] == ["requirement:R-LOAD"]
    assert plan["unknowns"]  # Source labels never establish engineering adequacy.
    assert structured(workflow) == task
    changed = copy.deepcopy(plan)
    changed["objective"] = "A different objective"
    with pytest.raises(ValueError, match="different"):
        ExecutionService(workflow.registry).propose_task(
            "DEMO",
            "T",
            "issue:F-LOAD",
            "compare_revision",
            task["parameters"],
            brain_plan=changed,
        )


def test_new_brain_result_cannot_be_verified_before_assessment(flow):
    _, _, human, _ = flow
    _, result = executed(flow)
    with pytest.raises(ValueError, match="assessment"):
        human.verify_result(
            "DEMO", result["node_id"], digest(result), ["evidence:EB"], "Bypass"
        )
    assert human.list_records("DEMO", "verification") == []
    assert human.list_records("DEMO", "decision") == []


def test_complete_loop_survives_reopen_and_learning_keeps_assessment_and_decision(flow):
    database, brain, human, workflow = flow
    task, result = executed(flow)
    assert result["attributes"]["outputs"]["removed_lines"] == ["Design load: 10 kN"]
    assert result["attributes"]["outputs"]["added_lines"] == ["Design load: 20 kN"]
    assessment = workflow.assess_output("DEMO", "T")
    assert assessment["attributes"]["result_digest"] == digest(result)
    assert all(c["passed"] for c in assessment["attributes"]["checks"])
    assert assessment["attributes"]["unknowns"]
    assert workflow.assess_output("DEMO", "T") == assessment
    assert human.list_records("DEMO", "verification") == []
    with pytest.raises(PermissionError):
        decide(brain, result, assessment)
    decision = decide(human, result, assessment)
    assert decide(human, result, assessment) == decision
    assert decision["attributes"]["reviewed_by"] == "engineer"
    assert (
        decision["attributes"]["reasoning_evidence_status"]
        == "verified_human_reasoning"
    )
    assert decision["attributes"]["reasoning_evidence_ids"] == ["evidence:EB"]
    assert decision["attributes"]["assessment_digest"] == digest(assessment)
    assert decision["attributes"]["result_digest"] == digest(result)
    verification = human.list_records("DEMO", "verification")[0]
    assert verification["attributes"]["assessment_id"] == assessment["node_id"]
    lesson = human.promote_lesson(
        "DEMO",
        "lesson:review",
        "Review changed load wording",
        ["issue:F-LOAD"],
        ["evidence:EB"],
        "Approved synthetic learning",
    )
    assert lesson["attributes"]["source_assessment_ids"] == [assessment["node_id"]]
    assert decision["node_id"] in lesson["attributes"]["source_decision_ids"]
    with SQLiteGraphStore(database) as reopened:
        assert (
            reopened.get_node("DEMO", decision["node_id"]).attributes["disposition"]
            == "accept"
        )
        assert (
            reopened.get_node("DEMO", assessment["node_id"]).attributes["task_digest"]
            == task["task_digest"]
        )
        retained = reopened.get_node("DEMO", "lesson:review").attributes[
            "source_snapshot"
        ]
        assert retained["assessments"][0]["node_id"] == assessment["node_id"]
    later = structured(workflow, "LATER")
    assert "lesson:review" in later["brain_plan"]["lesson_ids"]


@pytest.mark.parametrize("disposition", ["reject", "rework", "hold"])
def test_nonacceptance_is_recorded_without_verification(flow, disposition):
    _, _, human, workflow = flow
    _, result = executed(flow)
    assessment = workflow.assess_output("DEMO", "T")
    decision = decide(human, result, assessment, disposition)
    assert decision["attributes"]["disposition"] == disposition
    assert human.list_records("DEMO", "verification") == []
    assert human.get_record("DEMO", result["node_id"]) == result


def test_stale_foreign_or_wrong_result_assessment_rolls_back(flow):
    _, brain, human, workflow = flow
    _, result = executed(flow)
    assessment = workflow.assess_output("DEMO", "T")
    with pytest.raises(ValueError, match="assessment"):
        human.decide_result(
            "DEMO",
            result["node_id"],
            digest(result),
            assessment["node_id"],
            "stale",
            "accept",
            ["evidence:EB"],
            "Checked",
        )
    _, other = executed(flow, "OTHER")
    with pytest.raises(ValueError, match="assessment"):
        decide(human, other, assessment)
    brain.store.add_node(
        GraphNode("foreign-assessment", "OTHER", NodeType.ASSESSMENT, "Foreign")
    )
    with pytest.raises(ValueError):
        human.decide_result(
            "DEMO",
            result["node_id"],
            digest(result),
            "foreign-assessment",
            "anything",
            "accept",
            ["evidence:EB"],
            "Checked",
        )
    assert human.list_records("DEMO", "verification") == []
    assert human.list_records("DEMO", "decision") == []


def test_failed_output_checks_remain_visible_to_human(flow):
    _, brain, human, workflow = flow
    task, _ = executed(flow)
    bad = brain.record_result(
        "DEMO",
        "bad-result",
        "issue:F-LOAD",
        "T",
        {
            "task_digest": task["task_digest"],
            "brain_plan": task["brain_plan"],
            "snapshot": task["input_snapshot"],
        },
        "compare_revision",
        "1",
        {"added_lines": "not a list"},
        ["evidence:EA", "evidence:EB"],
    )
    assessment = workflow.assess_result("DEMO", "T", bad["node_id"])
    assert any(not c["passed"] for c in assessment["attributes"]["checks"])
    assert assessment["attributes"]["recommendation"] == "rework"
    assert human.list_records("DEMO", "verification") == []
    assert (
        decide(human, bad, assessment, "rework")["attributes"]["disposition"]
        == "rework"
    )


def test_desk_uses_brain_and_requires_assessment_before_final_decision(flow):
    from engineering_registry.desk import apply_action, render_page

    _, _, human, _ = flow
    current = human.issue_context("DEMO", "issue:F-LOAD")
    apply_action(
        human,
        {
            "operation": "prepare_task",
            "project_id": "DEMO",
            "record_id": "issue:F-LOAD",
            "rationale": "Compare",
            "digest": current["output_digest"],
            "tool": "compare_revision",
            "base": "evidence:EA",
            "head": "evidence:EB",
        },
    )
    task = ExecutionService(human).list_tasks("DEMO")[0]
    assert task["brain_plan"]["producer"] == "engineering_document_ai"
    ExecutionService(human).authorize_task(
        "DEMO", task["task_id"], task["task_digest"], "Checked"
    )
    apply_action(
        human,
        {
            "operation": "execute",
            "project_id": "DEMO",
            "record_id": task["task_id"],
            "rationale": "Run",
        },
    )
    assessments = human.list_records("DEMO", "assessment")
    assert len(assessments) == 1
    page = render_page(human, demo=True)
    assert "Document AI assessment" in page and "Request rework" in page
    result = human.issue_context("DEMO", "issue:F-LOAD")["results"][0]
    apply_action(
        human,
        {
            "operation": "decide_result",
            "project_id": "DEMO",
            "record_id": result["node_id"],
            "digest": digest(result),
            "assessment_id": assessments[0]["node_id"],
            "assessment_digest": digest(assessments[0]),
            "disposition": "accept",
            "rationale": "Verified",
        },
    )
    assert (
        human.list_records("DEMO", "decision")[0]["attributes"]["disposition"]
        == "accept"
    )


@pytest.mark.parametrize("tool", ["validate_traceability", "generate_review_pack"])
def test_other_document_tools_have_separate_output_assessments(flow, tool):
    _, _, human, workflow = flow
    task = structured(workflow, tool, tool, {})
    ExecutionService(human).authorize_task(
        "DEMO", tool, task["task_digest"], "Scope checked"
    )
    outcome = workflow.execute_and_assess("DEMO", tool)
    assert outcome["result"]["attributes"]["verification_state"] == "unverified"
    assert all(c["passed"] for c in outcome["assessment"]["attributes"]["checks"])
    assert human.list_records("DEMO", "decision") == []


def test_malformed_diagnostic_fields_produce_failed_assessment_not_exception(flow):
    _, brain, _, workflow = flow
    task = structured(workflow, "trace", "validate_traceability", {})
    bad = brain.record_result(
        "DEMO",
        "bad-trace",
        "issue:F-LOAD",
        "trace",
        {
            "task_digest": task["task_digest"],
            "brain_plan": task["brain_plan"],
            "snapshot": task["input_snapshot"],
        },
        "validate_traceability",
        "1",
        {
            "evidence_count": 2,
            "missing_text": [None, "E"],
            "missing_locators": [],
            "requirement_status": "linked",
            "warnings": ["Human review"],
        },
        ["evidence:EA", "evidence:EB"],
    )
    assessment = workflow.assess_result("DEMO", "trace", bad["node_id"])
    assert assessment["attributes"]["recommendation"] == "rework"


def test_later_rework_blocks_closure_despite_retained_old_verification(flow):
    _, brain, human, workflow = flow
    for status in ("open", "accepted"):
        proposal = brain.propose_transition("DEMO", "issue:F-LOAD", status, "Review")
        human.review_proposal(
            "DEMO",
            proposal["proposal_id"],
            proposal["proposal_digest"],
            accept=True,
            rationale="Reviewed",
        )
    _, result = executed(flow)
    assessment = workflow.assess_output("DEMO", "T")
    decide(human, result, assessment)
    decide(human, result, assessment, "rework", "Further checking required")
    closure = brain.propose_transition(
        "DEMO", "issue:F-LOAD", "closed", "Close", ["evidence:EB"]
    )
    with pytest.raises(ValueError, match="acceptance"):
        human.review_proposal(
            "DEMO",
            closure["proposal_id"],
            closure["proposal_digest"],
            accept=True,
            rationale="Close",
        )
    assert human.store.get_issue("DEMO", "issue:F-LOAD").status.value == "accepted"
    assert len(human.list_records("DEMO", "verification")) == 1


def test_invalid_plan_source_references_fail_without_persisting_task(flow):
    _, _, _, workflow = flow
    task = structured(workflow)
    plan = copy.deepcopy(task["brain_plan"])
    plan["evidence_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="source"):
        ExecutionService(workflow.registry).propose_task(
            "DEMO",
            "FOREIGN",
            "issue:F-LOAD",
            "compare_revision",
            task["parameters"],
            brain_plan=plan,
        )
    assert workflow.registry.store.get_record("DEMO", "tasks", "FOREIGN") is None


def test_assessment_detects_omitted_changed_lines_and_blocks_acceptance(flow):
    _, brain, human, workflow = flow
    task, result = executed(flow)
    attrs = result["attributes"]
    outputs = copy.deepcopy(attrs["outputs"])
    outputs.update(removed_lines=[], added_lines=[])
    bad = brain.record_result(
        "DEMO",
        "omitted-change",
        "issue:F-LOAD",
        "T",
        attrs["inputs"],
        "compare_revision",
        "1",
        outputs,
        attrs["evidence_ids"],
    )
    assessment = workflow.assess_result("DEMO", "T", bad["node_id"])
    assert assessment["attributes"]["recommendation"] == "rework"
    with pytest.raises(ValueError, match="Failed assessment"):
        decide(human, bad, assessment)
    assert human.list_records("DEMO", "verification") == []


def test_assessment_cannot_be_inserted_through_uncontrolled_registration(flow):
    _, brain, _, _ = flow
    with pytest.raises(ValueError, match="controlled"):
        brain.register(GraphNode("forged", "DEMO", NodeType.ASSESSMENT, "Passed"))
    assert brain.store.get_node("DEMO", "forged") is None
