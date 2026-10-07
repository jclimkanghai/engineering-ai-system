"""Catch disconnected analysis phases, invented citations and model-review bypass."""

import copy

import pytest

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.demo import seed_demo
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore
from tests.support.reasoning_harness import OfflineAnalysisAdapter, finding


@pytest.fixture
def services(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("document-ai", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        yield database, agent, human


def workflow(agent, adapter):
    from pipelines.review.execution_workflow import build_reasoning_workflow

    return build_reasoning_workflow(agent, analysis_adapter=adapter)


def task(flow, task_id="T"):
    return flow.structure_task(
        "DEMO",
        task_id,
        "issue:F-LOAD",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )


def test_analysis_pipeline_reasoning_is_retained_before_and_after_execution(services):
    database, agent, human = services
    adapter = OfflineAnalysisAdapter()
    flow = workflow(agent, adapter)
    planned = task(flow)
    assert planned["brain_plan"]["analysis"]["model"] == "offline-analysis-fixture"
    assert "engineering_review" in planned["brain_plan"]["acceptance_criteria"]
    assert "20 kN" in planned["brain_plan"]["analysis_summary"]
    assert task(flow) == planned and len(adapter.calls) == 1
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Scope checked"
    )
    outcome = flow.execute_and_assess("DEMO", "T")
    assert [request.mode for request in adapter.calls] == [
        "execution_planning",
        "execution_assessment",
    ]
    assessment = outcome["assessment"]
    assert (
        assessment["attributes"]["analysis"]["response_id"]
        == "response-execution_assessment"
    )
    assert assessment["attributes"]["analysis"]["findings"][0][
        "source_evidence_ids"
    ] == ["evidence:EB"]
    assert assessment["attributes"]["analysis"]["human_review_required"] is True
    assert adapter.calls[1].context["execution_result"]["attributes"]["outputs"][
        "added_lines"
    ] == ["Design load: 20 kN"]
    assert flow.assess_output("DEMO", "T") == assessment and len(adapter.calls) == 2
    result = outcome["result"]
    assert human.list_records("DEMO", "verification") == []
    decision = human.decide_result(
        "DEMO",
        result["node_id"],
        digest(result),
        assessment["node_id"],
        digest(assessment),
        "accept",
        ["evidence:EB"],
        "Output and proposed reasoning checked",
    )
    lesson = human.promote_lesson(
        "DEMO",
        "lesson:model-review",
        "Review changed load",
        ["issue:F-LOAD"],
        ["evidence:EB"],
        "Synthetic reviewed learning",
    )
    assert (
        lesson["attributes"]["source_snapshot"]["assessments"][0]["attributes"][
            "analysis"
        ]["model"]
        == "offline-analysis-fixture"
    )
    with SQLiteGraphStore(database) as reopened:
        assert reopened.get_node("DEMO", decision["node_id"]).attributes[
            "assessment_digest"
        ] == digest(assessment)


def test_material_planning_decisions_are_captured_and_do_not_stale_their_own_task(
    services,
):
    _, agent, human = services
    proposed_decision = {
        "decision_id": "retain-spacing",
        "statement": "Retain the current pile spacing while assessing diameter alternatives.",
        "rationale": "The topside arrangement is fixed and the spacing interface is consequential.",
        "decision_level": "D3",
        "evidence_ids": ["evidence:EB"],
        "requirement_ids": ["requirement:R-LOAD"],
        "related_record_ids": [],
        "alternatives": ["Change pile spacing"],
        "assumptions": ["The topside arrangement remains fixed."],
        "impact": "Constrains the structural alternatives for this task.",
    }
    adapter = OfflineAnalysisAdapter(task_decisions=[proposed_decision])
    flow = workflow(agent, adapter)
    planned = task(flow)
    task_decisions = [
        record
        for record in agent.list_records("DEMO", "decision")
        if record["attributes"].get("decision_type") == "task"
    ]
    assert len(task_decisions) == 1
    assert task_decisions[0]["attributes"]["decision_level"] == "D3"
    assert task_decisions[0]["attributes"]["task_id"] == "T"
    assert task_decisions[0]["attributes"]["human_acceptance_required"] is True
    assert task_decisions[0]["attributes"]["requirement_ids"] == ["requirement:R-LOAD"]
    decision_edges = agent.store.get_edges("DEMO", task_decisions[0]["node_id"])
    assert any(edge.target_node_id == "requirement:R-LOAD" for edge in decision_edges)
    assert task(flow) == planned

    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Reviewed the exact planned work"
    )
    result = ExecutionService(agent).execute("DEMO", "T")
    assert result["node_type"] == "result"


def test_invalid_planning_decision_citation_rolls_back_task_and_registry_decision(
    services,
):
    _, agent, _ = services
    invalid = {
        "decision_id": "bad-citation",
        "statement": "Retain the current interface.",
        "rationale": "The source proves the constraint.",
        "decision_level": "D2",
        "evidence_ids": ["not-in-context"],
        "requirement_ids": [],
        "related_record_ids": [],
        "alternatives": [],
        "assumptions": [],
        "impact": None,
    }
    with pytest.raises(ValueError, match="task decision reference"):
        task(workflow(agent, OfflineAnalysisAdapter(task_decisions=[invalid])))
    assert ExecutionService(agent).list_tasks("DEMO") == []
    assert agent.list_records("DEMO", "decision") == []


@pytest.mark.parametrize(
    "bad", [finding(evidence_id="invented"), finding(requirement_id="foreign")]
)
def test_ungrounded_planning_findings_fail_without_task_write(services, bad):
    _, agent, _ = services
    adapter = OfflineAnalysisAdapter(planning=[bad])
    with pytest.raises(ValueError, match="evidence|requirement|validation"):
        task(workflow(agent, adapter))
    assert ExecutionService(agent).list_tasks("DEMO") == []


def test_reasoning_workflow_requires_explicit_adapter_without_constructing_provider(
    services,
):
    from pipelines.review.execution_workflow import build_reasoning_workflow

    _, agent, _ = services
    with pytest.raises(ValueError, match="explicit|adapter"):
        build_reasoning_workflow(agent, analysis_adapter=None)


def test_provider_failure_leaves_v2_output_unverified_and_retry_does_not_rerun_v2(
    services,
):
    _, agent, human = services
    adapter = OfflineAnalysisAdapter()
    flow = workflow(agent, adapter)
    planned = task(flow)
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Scope checked"
    )
    adapter.failure = RuntimeError("offline fixture provider unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        flow.execute_and_assess("DEMO", "T")
    assert ExecutionService(agent).get_task("DEMO", "T")["state"] == "succeeded"
    assert len(agent.list_records("DEMO", "result")) == 1
    assert agent.list_records("DEMO", "assessment") == []
    adapter.failure = None
    assert flow.assess_output("DEMO", "T")["node_type"] == "assessment"
    assert len(ExecutionService(agent).get_task("DEMO", "T")["attempts"]) == 1


def test_offline_assessment_cannot_satisfy_a_reasoning_task(services):
    _, agent, human = services
    flow = workflow(agent, OfflineAnalysisAdapter())
    planned = task(flow)
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Scope checked"
    )
    result = ExecutionService(agent).execute("DEMO", "T")
    assessment = DocumentAIWorkflow(agent).assess_output("DEMO", "T")
    assert any(
        not check["passed"]
        for check in assessment["attributes"]["checks"]
        if check["criterion"] == "engineering_review"
    )
    with pytest.raises(ValueError, match="Failed assessment"):
        human.decide_result(
            "DEMO",
            result["node_id"],
            digest(result),
            assessment["node_id"],
            digest(assessment),
            "accept",
            ["evidence:EB"],
            "Bypass",
        )


def test_model_uncertainty_is_retained_and_blocks_acceptance(services):
    _, agent, human = services
    flow = workflow(
        agent, OfflineAnalysisAdapter(assessment=[finding(status="unverified")])
    )
    planned = task(flow)
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Scope checked"
    )
    outcome = flow.execute_and_assess("DEMO", "T")
    assert outcome["assessment"]["attributes"]["recommendation"] == "hold"
    assert (
        "Design adequacy is unknown." in outcome["assessment"]["attributes"]["unknowns"]
    )
    with pytest.raises(ValueError, match="Failed assessment"):
        human.decide_result(
            "DEMO",
            outcome["result"]["node_id"],
            digest(outcome["result"]),
            outcome["assessment"]["node_id"],
            digest(outcome["assessment"]),
            "accept",
            ["evidence:EB"],
            "Unchecked",
        )


def test_adapter_calls_run_outside_write_transaction_and_source_changes_are_rechecked(
    services,
):
    _, agent, human = services
    adapter = OfflineAnalysisAdapter()

    def changed(request):
        assert agent.store._transaction_depth == 0
        if request.mode == "execution_planning":
            proposal = agent.propose_transition(
                "DEMO",
                "issue:F-LOAD",
                "open",
                "Source context changed during provider call",
            )
            human.review_proposal(
                "DEMO",
                proposal["proposal_id"],
                proposal["proposal_digest"],
                accept=True,
                rationale="Reviewed",
            )

    adapter.before_return = changed
    with pytest.raises(ValueError, match="changed|stale"):
        task(workflow(agent, adapter))
    assert ExecutionService(agent).list_tasks("DEMO") == []


def test_model_phase_receives_complete_current_sources_and_approved_lesson_snapshot(
    services,
):
    _, agent, human = services
    lesson = human.promote_lesson(
        "DEMO",
        "lesson:prior",
        "Compare ordered revisions",
        ["issue:F-LOAD"],
        ["evidence:EB"],
        "Synthetic approved context",
    )
    adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                "lesson_id": "lesson:prior",
                "status": "applicable",
                "rationale": "The source comparison method applies to the current load review.",
                "requirement_ids": ["requirement:R-LOAD"],
                "evidence_ids": ["evidence:EB"],
                "requirements_check": "complies",
                "limitations": [],
                "resolution": None,
                "lead_action": None,
                "conflict_interpretation": None,
                "requirement_concern": None,
            }
        ]
    )
    planned = task(workflow(agent, adapter))
    request = adapter.calls[0]
    assert {e["evidence_id"]: e["excerpt"] for e in request.evidence} == {
        "evidence:EA": "Design load: 10 kN\nInspect weekly",
        "evidence:EB": "Design load: 20 kN\nInspect weekly",
    }
    assert request.requirements[0]["requirement_id"] == "requirement:R-LOAD"
    assert (
        request.requirements[0]["source_text"] == "Review changes to the design load."
    )
    assert request.requirements[0]["source_evidence_ids"] == ["evidence:EB"]
    assert request.context["approved_lessons"] == [lesson]
    assert planned["brain_plan"]["analysis"]["lesson_context"] == [lesson]
    assert "Prefer applicable, validated organisational lessons" in request.instructions
    assert "Describe any conflict in a finding's conflicts" in request.instructions


def test_validated_organisational_knowledge_precedes_project_lessons_in_lead_context(
    services,
):
    _, agent, human = services
    project_lesson = human.promote_lesson(
        "DEMO",
        "lesson:project",
        "Reviewed project approach",
        ["issue:F-LOAD"],
        ["evidence:EB"],
        "Synthetic reviewed outcome",
    )
    curator = RegistryService(
        agent.store,
        Principal(
            "curator",
            frozenset({"DEMO"}),
            "reviewer",
            organization_ids=frozenset({"ACME"}),
        ),
    )
    org_lesson = curator.promote_organizational_lesson(
        "ACME",
        "lesson:org",
        "Reusable load-review approach",
        [("DEMO", project_lesson["node_id"])],
        observation="The source load changed.",
        result="The change was reviewed.",
        interpretation="A new load requires assessment.",
        validated_statement="Check the current governing load before applying a precedent.",
        applicability="Comparable load revisions.",
        limitations="Current project requirements govern.",
        relevant_standards=[],
        review_due="2099-01-01",
        rationale="Human-validated synthetic example.",
    )
    org_digest = digest(
        {
            "lesson": org_lesson,
            "status": agent.store.get_record(
                "ORG:ACME", "organizational_lesson_status", "lesson:org"
            ),
        }
    )
    curator.import_organizational_lesson(
        "DEMO",
        "ACME",
        "lesson:org",
        "lesson:imported",
        "Applicable to DEMO",
        org_digest,
    )
    project_review = {
        "lesson_id": "lesson:project",
        "status": "applicable",
        "rationale": "The reviewed approach applies to the current load comparison.",
        "requirement_ids": ["requirement:R-LOAD"],
        "evidence_ids": ["evidence:EB"],
        "requirements_check": "complies",
        "limitations": [],
        "resolution": None,
        "lead_action": None,
        "conflict_interpretation": None,
        "requirement_concern": None,
    }
    adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                "lesson_id": "lesson:imported",
                "status": "uncertain",
                "rationale": "Applicability needs human review.",
                "requirement_ids": [],
                "evidence_ids": [],
                "requirements_check": "unknown",
                "limitations": [],
                "resolution": None,
                "lead_action": None,
                "conflict_interpretation": None,
                "requirement_concern": None,
            },
            project_review,
        ]
    )
    planned = task(workflow(agent, adapter))
    assert [
        lesson["node_id"] for lesson in adapter.calls[0].context["approved_lessons"]
    ] == [
        "lesson:imported",
        "lesson:project",
    ]
    assert planned["brain_plan"]["analysis"]["lesson_reviews"] == adapter.lesson_reviews
    assert any(
        "lesson:imported: uncertain" in item
        for item in planned["brain_plan"]["unknowns"]
    )
    with pytest.raises(ValueError, match="reused knowledge item requires a review"):
        task(workflow(agent, OfflineAnalysisAdapter()), task_id="T-missing-review")
    context_flow = workflow(agent, OfflineAnalysisAdapter())
    real_analysis = context_flow.analysis

    def omitted_lesson_context(phase, context):
        assert real_analysis is not None
        result = real_analysis(phase, context)
        result["lesson_context"] = []
        return result

    context_flow.analysis = omitted_lesson_context
    with pytest.raises(ValueError, match="retain the current lesson context"):
        task(context_flow, task_id="T-omitted-context")
    with pytest.raises(ValueError, match="conflict requires current project citations"):
        task(
            workflow(
                agent,
                OfflineAnalysisAdapter(
                    lesson_reviews=[
                        {
                            **adapter.lesson_reviews[0],
                            "status": "conflict",
                            "requirements_check": "conflict",
                            "resolution": "unresolved",
                            "lead_action": "Find a solution within the current requirement.",
                            "conflict_interpretation": "project_requirement_controls",
                        },
                        project_review,
                    ]
                ),
            ),
            task_id="T-uncited-conflict",
        )
    conflict_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **adapter.lesson_reviews[0],
                "status": "conflict",
                "requirements_check": "conflict",
                "resolution": "unresolved",
                "lead_action": "Develop a compliant option before considering the lesson.",
                "conflict_interpretation": "project_requirement_controls",
                "rationale": "The prior approach conflicts with the current load requirement.",
                "requirement_ids": ["requirement:R-LOAD"],
                "evidence_ids": ["evidence:EB"],
            },
            project_review,
        ]
    )
    conflict_flow = workflow(agent, conflict_adapter)
    conflict_task = task(conflict_flow, task_id="T-conflict")
    assert any(
        "Knowledge conflict lesson:imported" in item
        for item in conflict_task["brain_plan"]["unknowns"]
    )
    ExecutionService(human).authorize_task(
        "DEMO", "T-conflict", conflict_task["task_digest"], "Review conflict before use"
    )
    outcome = conflict_flow.execute_and_assess("DEMO", "T-conflict")
    assert outcome["assessment"]["attributes"]["recommendation"] == "hold"
    assert (
        outcome["assessment"]["attributes"]["analysis"]["lesson_reviews"]
        == conflict_adapter.lesson_reviews
    )

    compliant_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **conflict_adapter.lesson_reviews[0],
                "resolution": "compliant_alternative",
                "lead_action": "Develop a solution within the current load requirement.",
            },
            project_review,
        ]
    )
    compliant_task = task(
        workflow(agent, compliant_adapter), task_id="T-compliant-option"
    )
    conflict = compliant_task["brain_plan"]["knowledge_conflicts"][0]
    assert conflict["lesson_id"] == "lesson:imported"
    assert conflict["requirement_ids"] == ["requirement:R-LOAD"]
    assert conflict["human_client_decision_required"] is False
    assert conflict["early_warning"] is False
    assert conflict["conflict_interpretation"] == "project_requirement_controls"
    assert "KNOWLEDGE CONFLICT — Project Requirement" in conflict["message"]
    assert "does not make the knowledge wrong" in conflict["message"]
    assert "Check the current governing load" in conflict["message"]
    assert "Review changes to the design load" in conflict["message"]
    assert (
        "Develop a solution within the current load requirement" in conflict["message"]
    )
    from engineering_registry.desk import render_page

    assert "KNOWLEDGE CONFLICT — Project Requirement" in render_page(human, demo=True)
    assert not any(
        "lesson:imported: conflict" in item
        for item in compliant_task["brain_plan"]["unknowns"]
    )
    ExecutionService(human).authorize_task(
        "DEMO",
        "T-compliant-option",
        compliant_task["task_digest"],
        "Only the compliant alternative is authorised",
    )
    compliant_outcome = workflow(agent, compliant_adapter).execute_and_assess(
        "DEMO", "T-compliant-option"
    )
    assert compliant_outcome["assessment"]["attributes"]["recommendation"] != "hold"
    assert (
        "KNOWLEDGE CONFLICT" in compliant_outcome["assessment"]["attributes"]["summary"]
    )

    deviation_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **conflict_adapter.lesson_reviews[0],
                "resolution": "deviation_proposed",
                "lead_action": "Prepare a justified deviation request for human/client review.",
            },
            project_review,
        ]
    )
    deviation_task = task(workflow(agent, deviation_adapter), task_id="T-deviation")
    assert (
        deviation_task["brain_plan"]["knowledge_conflicts"][0][
            "human_client_decision_required"
        ]
        is True
    )
    assert any(
        "deviation" in item.lower() for item in deviation_task["brain_plan"]["unknowns"]
    )

    warning_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **conflict_adapter.lesson_reviews[0],
                "conflict_interpretation": "possible_requirement_problem",
                "requirement_concern": "The current load criterion may omit a governing site condition.",
                "lead_action": "Check the design basis and raise the concern with the project owner.",
            },
            project_review,
        ]
    )
    warning_task = task(workflow(agent, warning_adapter), task_id="T-early-warning")
    warning = warning_task["brain_plan"]["knowledge_conflicts"][0]
    assert warning["early_warning"] is True
    assert warning["human_client_decision_required"] is False
    assert "EARLY WARNING" in warning["message"]
    assert "current load criterion may omit" in warning["message"]
    assert "Current project requirement remains governing" in warning["message"]
    with pytest.raises(ValueError, match="requirement concern requires an explanation"):
        task(
            workflow(
                agent,
                OfflineAnalysisAdapter(
                    lesson_reviews=[
                        {
                            **warning_adapter.lesson_reviews[0],
                            "requirement_concern": None,
                        },
                        project_review,
                    ]
                ),
            ),
            task_id="T-unclear-warning",
        )

    partial_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **adapter.lesson_reviews[0],
                "status": "partial",
                "requirements_check": "complies",
                "rationale": "The method applies only to the current load revision.",
                "requirement_ids": ["requirement:R-LOAD"],
                "evidence_ids": ["evidence:EB"],
                "limitations": ["Recheck the load if the design basis changes."],
            },
            project_review,
        ]
    )
    partial_task = task(workflow(agent, partial_adapter), task_id="T-partial")
    assert (
        partial_task["brain_plan"]["analysis"]["lesson_reviews"]
        == partial_adapter.lesson_reviews
    )
    assert any(
        "Recheck the load" in item
        for item in partial_task["brain_plan"]["lesson_limitations"]
    )
    ExecutionService(human).authorize_task(
        "DEMO", "T-partial", partial_task["task_digest"], "Review bounded lesson use"
    )
    partial_outcome = workflow(agent, partial_adapter).execute_and_assess(
        "DEMO", "T-partial"
    )
    assert "Recheck the load" in partial_outcome["assessment"]["attributes"]["summary"]

    with pytest.raises(ValueError, match="partial applicability requires limitations"):
        task(
            workflow(
                agent,
                OfflineAnalysisAdapter(
                    lesson_reviews=[
                        {
                            **partial_adapter.lesson_reviews[0],
                            "limitations": [],
                        },
                        project_review,
                    ]
                ),
            ),
            task_id="T-partial-unbounded",
        )
    with pytest.raises(ValueError, match="current project requirements check"):
        task(
            workflow(
                agent,
                OfflineAnalysisAdapter(
                    lesson_reviews=[
                        {
                            **partial_adapter.lesson_reviews[0],
                            "requirements_check": "unknown",
                        },
                        project_review,
                    ]
                ),
            ),
            task_id="T-partial-unknown",
        )
    ignored_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **adapter.lesson_reviews[0],
                "status": "not_relevant",
                "requirements_check": "not_applicable",
                "rationale": "The lesson concerns a different design objective.",
            },
            project_review,
        ]
    )
    ignored_task = task(workflow(agent, ignored_adapter), task_id="T-irrelevant")
    assert "lesson_limitations" not in ignored_task["brain_plan"]
    assert not any(
        "lesson:imported" in item for item in ignored_task["brain_plan"]["unknowns"]
    )
    inapplicable_adapter = OfflineAnalysisAdapter(
        lesson_reviews=[
            {
                **adapter.lesson_reviews[0],
                "status": "not_applicable",
                "requirements_check": "not_applicable",
                "rationale": "The prior lesson concerns a different pile system.",
            },
            project_review,
        ]
    )
    inapplicable_task = task(
        workflow(agent, inapplicable_adapter), task_id="T-inapplicable"
    )
    assert "knowledge_conflicts" not in inapplicable_task["brain_plan"]


def test_registry_rejects_claimed_engineering_pass_without_reasoning_provenance(
    services,
):
    _, agent, human = services
    flow = workflow(agent, OfflineAnalysisAdapter())
    planned = task(flow)
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Scope checked"
    )
    result = ExecutionService(agent).execute("DEMO", "T")
    blocked = DocumentAIWorkflow(agent).assess_output("DEMO", "T")
    payload = {
        key: copy.deepcopy(blocked["attributes"][key])
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
    for check in payload["checks"]:
        check["passed"] = True
    with pytest.raises(ValueError, match="reasoning|analysis"):
        agent.record_assessment("DEMO", result["node_id"], digest(result), payload)


def test_desk_can_inject_reasoning_workflow_without_calling_adapter_under_write_lock(
    services,
):
    from engineering_registry.desk import apply_action

    _, agent, human = services
    adapter = OfflineAnalysisAdapter()
    adapter.before_return = lambda _request: (
        (_ for _ in ()).throw(AssertionError("Provider called under write lock"))
        if agent.store._transaction_depth
        else None
    )

    def factory(registry):
        return workflow(registry, adapter)

    context = human.issue_context("DEMO", "issue:F-LOAD")
    apply_action(
        human,
        {
            "operation": "prepare_task",
            "project_id": "DEMO",
            "record_id": "issue:F-LOAD",
            "digest": context["output_digest"],
            "tool": "compare_revision",
            "base": "evidence:EA",
            "head": "evidence:EB",
            "rationale": "Compare",
        },
        workflow_factory=factory,
    )
    planned = ExecutionService(agent).list_tasks("DEMO")[0]
    ExecutionService(human).authorize_task(
        "DEMO", planned["task_id"], planned["task_digest"], "Checked"
    )
    apply_action(
        human,
        {
            "operation": "execute",
            "project_id": "DEMO",
            "record_id": planned["task_id"],
            "rationale": "Run",
        },
        workflow_factory=factory,
    )
    assert (
        len(adapter.calls) == 2 and len(agent.list_records("DEMO", "assessment")) == 1
    )


def test_openai_adapter_passes_phase_instructions_and_lesson_review_schema():
    import json
    from types import SimpleNamespace

    from pipelines.llm.adapter import OpenAIAnalysisAdapter
    from pipelines.llm.models import AnalysisRequest

    class OfflineClient:
        def create(self, **arguments):
            self.arguments = arguments
            return SimpleNamespace(
                output_text='{"findings":[],"lesson_reviews":[]}',
                id="offline",
                usage=None,
            )

    client = OfflineClient()
    OpenAIAnalysisAdapter(client=client, model="offline-fixture").analyse(
        AnalysisRequest(
            mode="execution_assessment",
            instructions="Compare V2 output with the exact task and source evidence.",
        )
    )
    payload = json.loads(client.arguments["input"].split("INPUT:\n", 1)[1])
    assert (
        payload["task_instructions"]
        == "Compare V2 output with the exact task and source evidence."
    )
    assert client.arguments["text"]["format"]["schema"]["required"] == [
        "findings",
        "lesson_reviews",
    ]


def test_mcp_host_does_not_expose_single_task_agent_routes(services):
    import asyncio

    from engineering_registry.mcp_server import create_server

    _, agent, _ = services
    adapter = OfflineAnalysisAdapter()
    server = create_server(
        agent, workflow_factory=lambda registry: workflow(registry, adapter)
    )

    async def scenario():
        names = {tool.name for tool in await server.list_tools()}
        assert not {"propose_task", "execute_task"} & names

    asyncio.run(scenario())
    assert adapter.calls == []
    assert agent.list_records("DEMO", "assessment") == []


def test_existing_responses_adapter_drives_both_phases_without_network(services):
    import json
    from types import SimpleNamespace

    from pipelines.llm.adapter import OpenAIAnalysisAdapter

    _, agent, human = services

    class OfflineResponsesClient:
        def __init__(self):
            self.calls = []

        def create(self, **arguments):
            self.calls.append(arguments)
            payload = {"findings": [finding()]}
            if "task_decisions" in arguments["text"]["format"]["schema"]["required"]:
                payload["task_decisions"] = []
            return SimpleNamespace(
                output_text=json.dumps(payload),
                id="offline-response",
                usage=None,
            )

    client = OfflineResponsesClient()
    flow = workflow(
        agent, OpenAIAnalysisAdapter(client=client, model="offline-fixture")
    )
    planned = task(flow)
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Synthetic scope checked"
    )
    outcome = flow.execute_and_assess("DEMO", "T")
    assert len(client.calls) == 2
    assert (
        outcome["assessment"]["attributes"]["analysis"]["response_id"]
        == "offline-response"
    )
    assert all(c["store"] is False for c in client.calls)


def test_desk_provider_failure_returns_recoverable_error_and_preserves_output(services):
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request
    from http.cookiejar import CookieJar

    from engineering_registry.desk import make_server

    database, agent, human = services
    adapter = OfflineAnalysisAdapter()
    planned = task(workflow(agent, adapter))
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Synthetic scope checked"
    )
    adapter.failure = RuntimeError("fixture provider unavailable")
    server, url = make_server(
        database,
        {"DEMO"},
        "synthetic-http-reviewer",
        workflow_factory=lambda registry: workflow(registry, adapter),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = url.split("?")[0]
    try:
        client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        client.open(url).read()
        body = urllib.parse.urlencode(
            {
                "operation": "execute",
                "project_id": "DEMO",
                "record_id": "T",
                "rationale": "Synthetic run",
            }
        ).encode()
        request = urllib.request.Request(
            base + "action", body, headers={"Origin": base.rstrip("/")}
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            client.open(request)
        assert error.value.code == 503
        assert "unverified" in error.value.read().decode()
        assert ExecutionService(agent).get_task("DEMO", "T")["state"] == "succeeded"
        assert len(agent.list_records("DEMO", "result")) == 1
        assert agent.list_records("DEMO", "verification") == []
        assert client.open(base).status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_registry_cannot_mark_unresolved_model_findings_as_passed(services):
    _, agent, human = services
    flow = workflow(
        agent, OfflineAnalysisAdapter(assessment=[finding(status="unverified")])
    )
    planned = task(flow)
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Synthetic scope checked"
    )
    outcome = flow.execute_and_assess("DEMO", "T")
    payload = {
        key: copy.deepcopy(outcome["assessment"]["attributes"][key])
        for key in (
            "task_digest",
            "summary",
            "checks",
            "unknowns",
            "recommendation",
            "evidence_ids",
            "method",
            "analysis",
        )
    }
    for check in payload["checks"]:
        check["passed"] = True
    with pytest.raises(ValueError, match="unresolved|Unresolved"):
        agent.record_assessment(
            "DEMO", outcome["result"]["node_id"], digest(outcome["result"]), payload
        )


def test_duplicate_provider_finding_identifiers_fail_before_task_write(services):
    _, agent, _ = services
    with pytest.raises(ValueError, match="duplicate|Duplicate"):
        task(workflow(agent, OfflineAnalysisAdapter(planning=[finding(), finding()])))
    assert ExecutionService(agent).list_tasks("DEMO") == []
