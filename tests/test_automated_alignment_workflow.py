"""Exercise automated review/decisions through the real controlled workflow."""

from copy import deepcopy

import pytest

from engineering_execution import ExecutionService
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore
from tests.support.delegation_harness import mandate_definition
from tests.support.project_run_harness import (
    alignment_report,
    prepare,
    workflow_for,
)


def test_aligned_low_risk_loop_records_ai_authority_without_human_verification(
    configured,
):
    workflow = workflow_for(configured)
    task = prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert task["brain_plan"]["client_mandate"]["mandate_id"] == "M1"
    assert outcome["alignment_review"]["attributes"]["status"] == "aligned"
    decision = outcome["decision"]["attributes"]
    assert decision["authority"] == "ai_delegated"
    assert decision["disposition"] == "accept"
    assert decision["human_verified"] is False
    assert decision["policy_snapshot"]["approved_by"] == "synthetic-human"
    assert decision["rationale"] and decision["evidence_ids"]
    assert configured[1].list_records("DEMO", "verification") == []
    again = workflow.execute_and_assess("DEMO", "T")
    assert again["decision"] == outcome["decision"]
    with SQLiteGraphStore(configured[0]) as reopened:
        reader = RegistryService(
            reopened, Principal("read", frozenset({"DEMO"}), "reader")
        )
        assert (
            reader.get_record("DEMO", outcome["decision"]["node_id"])["attributes"][
                "authority"
            ]
            == "ai_delegated"
        )


@pytest.mark.parametrize(
    "status, disposition",
    [("misaligned", "rework"), ("insufficient_information", "hold")],
)
def test_reviewer_deviation_cannot_be_accepted(configured, status, disposition):
    workflow = workflow_for(configured, lambda c: alignment_report(c, status))
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert outcome["decision"]["attributes"]["disposition"] == disposition
    assert outcome["decision"]["attributes"]["authority"] == "ai_delegated"


@pytest.mark.parametrize(
    "changes",
    [
        {"risk_level": "medium"},
        {"importance_level": "high"},
        {"risk_level": "unknown"},
        {"simple": False},
        {"reversible": False},
        {"consequence_domains": ["safety"]},
        {"unknowns": ["Client acceptance criterion is unclear"]},
    ],
)
def test_consequential_or_unclear_work_escalates(configured, changes):
    _, _, human = configured
    human.record_client_mandate(
        "DEMO", "M2", "issue:F-LOAD", mandate_definition(**changes)
    )
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert outcome["decision"] is None
    assert outcome["escalation"]


def test_revoked_policy_stops_decision_after_review(configured):
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    configured[2].revoke_delegation_policy("DEMO", "P1", "Pause delegated decisions")
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert outcome["decision"] is None
    assert "revoked" in outcome["escalation"]


def test_reviewer_failure_preserves_result_and_retry_does_not_rerun_v2(configured):
    def unavailable(_context):
        raise RuntimeError("Synthetic reviewer unavailable")

    broken = workflow_for(configured)
    prepare(configured, broken)
    broken.review_plan_alignment("DEMO", "T")
    broken.alignment_reviewer = unavailable
    with pytest.raises(Exception, match="alignment|reviewer"):
        broken.execute_and_assess("DEMO", "T")
    task = broken.execution.get_task("DEMO", "T")
    assert task["state"] == "succeeded" and len(task["attempts"]) == 1
    assert len(configured[1].list_records("DEMO", "alignment_review")) == 1
    recovered = workflow_for(configured).execute_and_assess("DEMO", "T")
    assert recovered["decision"]["attributes"]["disposition"] == "accept"
    assert len(broken.execution.get_task("DEMO", "T")["attempts"]) == 1


@pytest.mark.parametrize(
    "case", ["foreign", "unknowns", "false_verdict", "digest", "missing_check"]
)
def test_invalid_or_uncertain_review_never_creates_acceptance(configured, case):
    def reviewer(context):
        report = alignment_report(context)
        if context.get("phase") == "plan":
            return report
        if case == "foreign":
            report["checks"][0]["evidence_ids"] = ["evidence:foreign"]
        elif case == "unknowns":
            report["unknowns"] = ["Missing client instruction"]
        elif case == "false_verdict":
            report["checks"][0]["status"] = "misaligned"
        elif case == "digest":
            report["request_digest"] = "wrong"
        else:
            report["checks"].pop()
        return report

    workflow = workflow_for(configured, reviewer)
    prepare(configured, workflow)
    if case == "unknowns":
        outcome = workflow.execute_and_assess("DEMO", "T")
        assert (
            outcome["decision"] is None
            or outcome["decision"]["attributes"]["disposition"] != "accept"
        )
    else:
        with pytest.raises(ValueError):
            workflow.execute_and_assess("DEMO", "T")
    assert not [
        d
        for d in configured[1].list_records("DEMO", "decision")
        if d["attributes"].get("authority") == "ai_delegated"
        and d["attributes"]["disposition"] == "accept"
    ]


def test_changing_mandate_during_reviewer_call_invalidates_review(configured):
    def reviewer(context):
        configured[2].record_client_mandate(
            "DEMO", "M2", "issue:F-LOAD", mandate_definition(scope="A changed scope")
        )
        return alignment_report(context)

    workflow = workflow_for(configured, reviewer)
    with pytest.raises(ValueError, match="mandate|inputs changed|fresh task"):
        workflow.structure_task(
            "DEMO",
            "T",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )
    assert configured[1].list_records("DEMO", "alignment_review") == []


def test_agent_cannot_forge_review_or_delegated_decision(configured):
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    workflow.review_plan_alignment("DEMO", "T")
    result = workflow.execution.execute("DEMO", "T")
    with pytest.raises(PermissionError):
        configured[1].record_alignment_review("DEMO", "T", result["node_id"], {})
    with pytest.raises(PermissionError):
        configured[1].decide_delegated_result("DEMO", {})


def test_bound_task_rejects_old_or_forged_client_intent(configured):
    workflow = workflow_for(configured)
    task = prepare(configured, workflow)
    plan = deepcopy(task["brain_plan"])
    plan["client_mandate"]["definition"]["objective"] = "AI-invented intent"
    with pytest.raises(ValueError, match="mandate"):
        workflow.execution.propose_task(
            "DEMO",
            "forged",
            "issue:F-LOAD",
            task["tool"],
            task["parameters"],
            brain_plan=plan,
        )
    configured[2].record_client_mandate(
        "DEMO", "M2", "issue:F-LOAD", mandate_definition(scope="Revised client scope")
    )
    with pytest.raises(ValueError, match="mandate"):
        workflow.execution.execute("DEMO", "T")


def test_alignment_checks_cannot_be_forged_in_technical_assessment(configured):
    workflow = workflow_for(configured, lambda c: alignment_report(c, "misaligned"))
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    payload = {
        key: deepcopy(outcome["assessment"]["attributes"][key])
        for key in (
            "task_digest",
            "summary",
            "checks",
            "unknowns",
            "recommendation",
            "evidence_ids",
            "method",
        )
    }
    payload["checks"].append(
        {"criterion": "client_alignment", "passed": True, "detail": "Forged"}
    )
    payload["recommendation"] = "human_review"
    with pytest.raises(ValueError, match="criteria"):
        configured[1].record_assessment(
            "DEMO", outcome["result"]["node_id"], digest(outcome["result"]), payload
        )


def test_desk_distinguishes_ai_decisions_from_human_verification(configured):
    from engineering_registry.desk import render_page

    workflow = workflow_for(configured)
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    page = render_page(configured[2], demo=True)
    assert "AI Reviewer Gate 1 plan alignment and Gate 2 project assurance" in page
    assert "Delegated AI decisions" in page
    assert outcome["decision"]["node_id"] in page
    assert "Human verification recorded." not in page


def test_same_brain_with_separate_provider_reviewer_uses_original_intent(configured):
    from pipelines.llm.models import LLMResponse
    from pipelines.review.execution_workflow import build_reasoning_workflow
    from tests.support.reasoning_harness import OfflineAnalysisAdapter, finding

    class AlignmentAdapter:
        def analyse(self, request):
            assert request.mode == "client_alignment"
            assert "CLIENT'S perspective" in request.instructions
            if request.context.get("phase") == "plan":
                assert "PRE-EXECUTION PLAN REVIEW" in request.instructions
                assert "execution_result" not in request.context
                assert "exactly one finding" in request.instructions
            if request.context.get("phase") == "plan":
                assert "document_ai_assessment" not in request.context
            else:
                assert "document_ai_assessment" in request.context
            assert (
                request.context["client_mandate"]["definition"]["objective"]
                == mandate_definition()["objective"]
            )
            items = []
            for criterion in (
                ("plan_alignment",)
                if request.context.get("phase") == "plan"
                else (
                    "plan_alignment",
                    "execution_direction",
                    "outcome_alignment",
                )
            ):
                item = finding(status="confirmed")
                item.update(
                    finding_id=criterion,
                    risk_level="low",
                    uncertainties=[],
                    risk_dimensions=["alignment:aligned"],
                )
                items.append(item)
            return LLMResponse("offline-alignment-model", "review-response", items)

    agent, human = configured[1:]
    brain_adapter = OfflineAnalysisAdapter(planning=[], assessment=[])
    delegate = RegistryService(
        agent.store, Principal("delegate", frozenset({"DEMO"}), "delegate")
    )
    flow = build_reasoning_workflow(
        agent,
        analysis_adapter=brain_adapter,
        alignment_adapter=AlignmentAdapter(),
        decision_registry=delegate,
        delegation_policy_id="P1",
    )
    task = prepare(configured, flow)
    outcome = flow.execute_and_assess("DEMO", "T")
    assert task["brain_plan"]["analysis"]["model"] == "offline-analysis-fixture"
    assert (
        outcome["assessment"]["attributes"]["analysis"]["model"]
        == "offline-analysis-fixture"
    )
    assert (
        outcome["alignment_review"]["attributes"]["model"] == "offline-alignment-model"
    )
    assert outcome["decision"]["attributes"]["disposition"] == "accept"
    assert human.list_records("DEMO", "verification") == []


def test_host_reviewer_adapter_preserves_analysis_contract_and_normalizes_findings():
    from pipelines.llm.models import LLMResponse
    from pipelines.review.execution_workflow import _ReviewerAnalysisAdapter
    from tests.support.reasoning_harness import finding

    source_finding = finding(
        status="confirmed",
        evidence_id="evidence:source-1",
        requirement_id="requirement:1",
    )
    source_finding.update(
        finding_id="plan_alignment",
        risk_level="low",
        risk_dimensions=["alignment:aligned"],
        uncertainties=[],
        conflicts=[],
        assumptions=[],
    )

    class CapturingAdapter:
        request = None

        def analyse(self, request):
            self.request = request
            return LLMResponse(
                "offline-reviewer", "review-response", [source_finding], {"calls": 1}
            )

    adapter = CapturingAdapter()
    request = {
        "mode": "client_alignment",
        "project_id": "PROJECT-1",
        "document_ids": [],
        "requirements": [{"node_id": "requirement:1", "attributes": {}}],
        "evidence": [
            {
                "evidence_id": "evidence:source-1",
                "document_id": "document:1",
                "revision": "A",
                "locator": "Section 2",
                "excerpt": "The requirement excerpt.",
                "evidence_class": "source_fact",
            }
        ],
        "context": {"phase": "plan"},
        "instructions": "Review the original client objective.",
    }

    result = _ReviewerAnalysisAdapter(adapter).analyse(request)

    assert adapter.request is not None
    assert adapter.request.mode == request["mode"]
    assert adapter.request.project_id == request["project_id"]
    assert adapter.request.document_ids == []
    assert adapter.request.context == {
        **request["context"],
        "candidate_findings": [],
    }
    assert adapter.request.instructions == request["instructions"]
    assert adapter.request.evidence[0]["evidence_id"] == "evidence:source-1"
    assert result["findings"][0]["finding_id"] == "plan_alignment"
    assert result["model"] == "offline-reviewer"
    assert result["usage"] == {"calls": 1}


def test_independent_reviewer_requires_knowledge_check_and_records_comment():
    from engineering_ai_reviewer import build_alignment_reviewer

    context = {
        "phase": "plan",
        "project_id": "DEMO",
        "source_snapshot": {
            "records": [
                {
                    "node_id": "evidence:1",
                    "node_type": "evidence",
                    "attributes": {"text": "Current design basis"},
                },
                {
                    "node_id": "requirement:1",
                    "node_type": "requirement",
                    "attributes": {"source_text": "Current project limit"},
                },
            ]
        },
        "client_project_brief": {"source_basis": {"records": []}},
        "brain_plan": {
            "analysis": {
                "lesson_context": [
                    {
                        "node_id": "lesson:imported",
                        "attributes": {"scope": "imported_organizational"},
                    }
                ]
            },
            "knowledge_conflicts": [
                {"lesson_id": "lesson:imported", "requirement_ids": ["requirement:1"]}
            ],
        },
    }

    def item(criterion, status="aligned"):
        return {
            "finding_id": criterion,
            "finding": "The Lead flagged lesson:imported against requirement:1 for independent review.",
            "status": "confirmed",
            "risk_level": "low",
            "risk_dimensions": ["alignment:" + status],
            "source_evidence_ids": ["evidence:1"],
            "requirement_ids": ["requirement:1"],
            "uncertainties": [],
            "conflicts": [],
            "assumptions": [],
        }

    class Analyzer:
        def __init__(self, findings):
            self.findings = findings
            self.request = None

        def analyse(self, request):
            self.request = request
            return {
                "findings": self.findings,
                "model": "offline-reviewer",
                "usage": {},
                "response_id": "R1",
            }

    missing = Analyzer([item("plan_alignment")])
    with pytest.raises(ValueError, match="exactly the criteria"):
        build_alignment_reviewer(missing)(context)
    assert "knowledge_applicability" in missing.request["instructions"]

    uncited = item("knowledge_applicability")
    uncited["requirement_ids"] = []
    with pytest.raises(ValueError, match="cite each conflicting project requirement"):
        build_alignment_reviewer(Analyzer([item("plan_alignment"), uncited]))(context)

    reviewer = Analyzer([item("plan_alignment"), item("knowledge_applicability")])
    report = build_alignment_reviewer(reviewer)(context)
    assert report["status"] == "aligned"
    assert {check["criterion"] for check in report["checks"]} == {
        "plan_alignment",
        "knowledge_applicability",
    }
    assert report["review_comments"] == [
        "REVIEW COMMENT — Organisational knowledge: "
        + item("knowledge_applicability")["finding"]
    ]

    outcome_context = {**context, "phase": "outcome"}
    outcome_findings = [
        item(name)
        for name in (
            "plan_alignment",
            "execution_direction",
            "outcome_alignment",
            "knowledge_applicability",
        )
    ]
    outcome = build_alignment_reviewer(Analyzer(outcome_findings))(outcome_context)
    assert len(outcome["checks"]) == 4
    misuse = build_alignment_reviewer(
        Analyzer(
            [
                item("plan_alignment"),
                item("knowledge_applicability", "misaligned"),
            ]
        )
    )(context)
    assert misuse["status"] == "misaligned"
    assert misuse["review_comments"]


def test_standard_automated_workflow_configures_two_lazy_ai_roles(configured):
    from pipelines.review.execution_workflow import build_automated_workflow

    workflow = build_automated_workflow(configured[1])
    assert workflow.analysis is not None
    assert workflow.alignment_reviewer is not None


@pytest.mark.parametrize(
    "finding_changes",
    [
        {"risk_level": "high"},
        {"risk_level": "low", "risk_dimensions": ["safety"]},
        {
            "risk_level": "low",
            "assumptions": ["Assume an unstated client restriction does not apply"],
        },
    ],
)
def test_planning_high_risk_finding_cannot_disappear_into_automatic_acceptance(
    configured,
    finding_changes,
):
    from pipelines.review.execution_workflow import build_reasoning_workflow
    from tests.support.reasoning_harness import OfflineAnalysisAdapter, finding

    item = finding(status="confirmed")
    item.update(uncertainties=[], **finding_changes)
    agent = configured[1]
    delegate = RegistryService(
        agent.store, Principal("delegate", frozenset({"DEMO"}), "delegate")
    )
    workflow = build_reasoning_workflow(
        agent,
        analysis_adapter=OfflineAnalysisAdapter(planning=[item], assessment=[]),
        decision_registry=delegate,
        delegation_policy_id="P1",
    )
    workflow.alignment_reviewer = alignment_report
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert outcome["decision"] is None
    assert "reasoning" in outcome["escalation"]


def test_replaying_reasoned_task_after_client_change_requires_new_task(configured):
    from pipelines.review.execution_workflow import build_reasoning_workflow
    from tests.support.reasoning_harness import OfflineAnalysisAdapter

    workflow = build_reasoning_workflow(
        configured[1],
        analysis_adapter=OfflineAnalysisAdapter(planning=[], assessment=[]),
    )
    prepare(configured, workflow)
    configured[2].record_client_mandate(
        "DEMO", "M2", "issue:F-LOAD", mandate_definition(scope="New client scope")
    )
    with pytest.raises(ValueError, match="changed|mandate|different"):
        workflow.structure_task(
            "DEMO",
            "T",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )


def test_direct_delegate_gate_rejects_changed_tool_and_missing_freshness_validator(
    configured, monkeypatch
):
    from engineering_execution.tools import TOOLS

    workflow = workflow_for(configured)
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    attrs = outcome["decision"]["attributes"]
    payload = {
        key: deepcopy(attrs[key])
        for key in (
            "task_id",
            "policy_id",
            "assessment_id",
            "assessment_digest",
            "alignment_review_id",
            "alignment_review_digest",
            "disposition",
            "rationale",
            "evidence_ids",
        )
    }
    payload["rationale"] = "A new delegated decision after a tool change"
    bare_delegate = RegistryService(
        configured[1].store, Principal("bare-delegate", frozenset({"DEMO"}), "delegate")
    )
    with pytest.raises(ValueError, match="freshness|validator"):
        bare_delegate.decide_delegated_result("DEMO", payload)
    monkeypatch.setitem(TOOLS, "compare_revision", "changed")
    with pytest.raises(ValueError, match="version|fresh|Tool"):
        workflow.decision_registry.decide_delegated_result("DEMO", payload)


@pytest.mark.parametrize("status", ["misaligned", "insufficient_information"])
def test_preexecution_client_review_blocks_human_authorization(configured, status):
    def reviewer(context):
        assert context["phase"] == "plan"
        assert "execution_result" not in context
        report = alignment_report(context)
        report["status"] = status
        report["checks"][0]["status"] = status
        return report

    workflow = workflow_for(configured, reviewer)
    task = workflow.structure_task(
        "DEMO",
        "T",
        "issue:F-LOAD",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )
    with pytest.raises(ValueError, match="Gate 1"):
        ExecutionService(configured[2]).authorize_task(
            "DEMO", "T", task["task_digest"], "Authorize"
        )
    task = workflow.execution.get_task("DEMO", "T")
    assert task["state"] == "proposed" and task["attempts"] == []
    assert configured[1].list_records("DEMO", "result") == []


def test_direct_v2_call_cannot_skip_plan_review(configured):
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    review = workflow.review_plan_alignment("DEMO", "T")
    assert review["attributes"]["phase"] == "plan"
    assert review["attributes"]["result_id"] is None
    assert workflow.execution.execute("DEMO", "T")["node_type"] == "result"


def test_unavailable_plan_reviewer_blocks_without_starting_v2(configured):
    def unavailable(context):
        raise RuntimeError("offline")

    workflow = workflow_for(configured, unavailable)
    with pytest.raises(Exception, match="Plan alignment reviewer is unavailable"):
        workflow.structure_task(
            "DEMO",
            "T",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )
    assert workflow.execution.get_task("DEMO", "T")["attempts"] == []
    assert configured[1].list_records("DEMO", "alignment_review") == []
    workflow.alignment_reviewer = alignment_report
    workflow.review_plan_alignment("DEMO", "T")
    task = workflow.execution.get_task("DEMO", "T")
    ExecutionService(configured[2]).authorize_task(
        "DEMO", "T", task["task_digest"], "Retry after review"
    )
    assert workflow.execute_and_assess("DEMO", "T")["result"]["node_type"] == "result"


def test_plan_review_precedes_execution_and_outcome_review(configured):
    phases = []

    def reviewer(context):
        phases.append(context.get("phase", "outcome"))
        task = configured[1].store.get_record("DEMO", "tasks", "T")
        if context.get("phase") == "plan":
            assert task["state"] == "proposed" and task["attempts"] == []
        else:
            assert task["state"] == "succeeded"
            assert (
                context["document_ai_assessment"]["attributes"]["task_digest"]
                == task["task_digest"]
            )
        return alignment_report(context)

    workflow = workflow_for(configured, reviewer)
    prepare(configured, workflow)
    workflow.execute_and_assess("DEMO", "T")
    workflow.execute_and_assess("DEMO", "T")
    assert phases == ["plan", "outcome"]


def test_gate_one_runs_during_structuring_and_blocks_human_authorization(configured):
    def reviewer(context):
        report = alignment_report(context)
        if context.get("phase") == "plan":
            report["status"] = "misaligned"
            report["checks"][0]["status"] = "misaligned"
        return report

    workflow = workflow_for(configured, reviewer)
    task = workflow.structure_task(
        "DEMO",
        "G1",
        "issue:F-LOAD",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )
    assert task["state"] == "proposed"
    assert (
        configured[1].list_records("DEMO", "alignment_review")[0]["attributes"]["phase"]
        == "plan"
    )
    with pytest.raises(ValueError, match="Gate 1|aligned plan review"):
        ExecutionService(configured[2]).authorize_task(
            "DEMO", "G1", task["task_digest"], "Authorize"
        )


def test_gate_two_runs_after_saved_document_ai_assessment(configured):
    observations = []

    def reviewer(context):
        if context.get("phase") == "outcome":
            observations.append(context["document_ai_assessment"])
        return alignment_report(context)

    workflow = workflow_for(configured, reviewer)
    prepare(configured, workflow)
    outcome = workflow.execute_and_assess("DEMO", "T")
    assert observations == [outcome["assessment"]]
    review_attrs = outcome["alignment_review"]["attributes"]
    assert review_attrs["document_ai_assessment_id"] == outcome["assessment"]["node_id"]
    assert review_attrs["document_ai_assessment_digest"] == digest(
        outcome["assessment"]
    )


def test_human_acceptance_cannot_bypass_missing_gate_two(configured):
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    result = workflow.execution.execute("DEMO", "T")
    workflow.alignment_reviewer = None
    with pytest.raises(Exception, match="reviewer is not configured"):
        workflow.assess_result("DEMO", "T", result["node_id"])
    assessment = configured[1].list_records("DEMO", "assessment")[0]
    with pytest.raises(ValueError, match="Gate 2"):
        configured[2].decide_result(
            "DEMO",
            result["node_id"],
            digest(result),
            assessment["node_id"],
            digest(assessment),
            "accept",
            ["evidence:EB"],
            "Verified",
        )
    with pytest.raises(ValueError, match="Gate 2"):
        configured[2].verify_result(
            "DEMO",
            result["node_id"],
            digest(result),
            ["evidence:EB"],
            "Verified",
            assessment_id=assessment["node_id"],
            assessment_digest=digest(assessment),
        )


def test_changed_client_mandate_invalidates_preexecution_review(configured):
    workflow = workflow_for(configured)
    prepare(configured, workflow)
    workflow.review_plan_alignment("DEMO", "T")
    configured[2].record_client_mandate(
        "DEMO", "M2", "issue:F-LOAD", mandate_definition(scope="Changed client scope")
    )
    with pytest.raises(ValueError, match="mandate|changed|fresh"):
        workflow.execution.execute("DEMO", "T")
    assert workflow.execution.get_task("DEMO", "T")["attempts"] == []


def test_plan_unknowns_block_even_with_aligned_verdict(configured):
    def reviewer(context):
        report = alignment_report(context)
        report["unknowns"] = ["Client acceptance criterion requires clarification"]
        return report

    workflow = workflow_for(configured, reviewer)
    task = workflow.structure_task(
        "DEMO",
        "T",
        "issue:F-LOAD",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )
    with pytest.raises(ValueError, match="Gate 1"):
        ExecutionService(configured[2]).authorize_task(
            "DEMO", "T", task["task_digest"], "Authorize"
        )
    assert workflow.execution.get_task("DEMO", "T")["attempts"] == []
