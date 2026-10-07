import copy

import pytest

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.demo import seed_demo
from engineering_registry.desk import apply_action, render_page
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


@pytest.fixture
def services(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("brain", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("synthetic-reviewer", frozenset({"DEMO"}), "reviewer")
        )
        yield database, agent, human


SOURCE = "document_revision:BASIS-B%3AB"


def review(human, status="governing", basis=None, expected=None):
    view = human.document_control("DEMO", SOURCE)
    return human.review_document_control(
        "DEMO",
        SOURCE,
        expected or view["control_digest"],
        status,
        basis or ["evidence:EB"],
        "Synthetic source review",
    )


def test_imported_current_label_does_not_establish_human_authority(services):
    _, agent, _ = services
    context = agent.source_control("DEMO", ["evidence:EB"])
    assert context[0]["status"] == "unverified"
    assert context[0]["decision_id"] is None
    assert context[0]["source_id"] == SOURCE


def test_review_preserves_source_records_and_retains_history_after_reopen(services):
    database, agent, human = services
    original = copy.deepcopy(agent.get_record("DEMO", "evidence:EB"))
    first = review(human)
    assert first["attributes"]["reviewed_by"] == "synthetic-reviewer"
    assert first["attributes"]["evidence_ids"] == ["evidence:EB"]
    second = review(human, "superseded")
    assert first["node_id"] != second["node_id"]
    assert agent.get_record("DEMO", "evidence:EB") == original
    with SQLiteGraphStore(database) as reopened:
        reader = RegistryService(
            reopened, Principal("read", frozenset({"DEMO"}), "reader")
        )
        current = reader.document_control("DEMO", SOURCE)
        assert current["status"] == "superseded"
        assert current["decision_id"] == second["node_id"]
        assert (
            len(
                [
                    e
                    for e in reader.history("DEMO", SOURCE)
                    if e["kind"] == "source_authority_reviewed"
                ]
            )
            == 2
        )
        assert reopened.get_node("DEMO", first["node_id"]) is not None


def test_agent_cannot_review_source_authority(services):
    _, agent, _ = services
    with pytest.raises(PermissionError):
        review(agent)


@pytest.mark.parametrize("case", ["stale", "foreign", "wrong_type", "blank", "status"])
def test_invalid_review_fails_without_decisions(services, case):
    _, agent, human = services
    view = human.document_control("DEMO", SOURCE)
    args = [
        "DEMO",
        SOURCE,
        view["control_digest"],
        "governing",
        ["evidence:EB"],
        "Synthetic review",
    ]
    if case == "stale":
        args[2] = "old"
    if case == "foreign":
        args[0] = "OTHER"
    if case == "wrong_type":
        args[4] = [SOURCE]
    if case == "blank":
        args[5] = ""
    if case == "status":
        args[3] = "automatically-approved"
    with pytest.raises((ValueError, PermissionError)):
        human.review_document_control(*args)
    assert agent.list_records("DEMO", "decision") == []
    assert agent.source_control("DEMO", ["evidence:EB"])[0]["status"] == "unverified"


def test_source_review_invalidates_authorised_execution_and_new_brain_retains_control(
    services,
):
    _, agent, human = services
    flow = DocumentAIWorkflow(agent)
    planned = flow.structure_task(
        "DEMO", "old", "issue:F-LOAD", "generate_review_pack", {}
    )
    assert planned["brain_plan"]["source_control"][1]["status"] == "unverified"
    ExecutionService(human).authorize_task(
        "DEMO", "old", planned["task_digest"], "Synthetic scope"
    )
    decision = review(human)
    with pytest.raises(ValueError, match="changed|stale"):
        ExecutionService(agent).execute("DEMO", "old")
    fresh = flow.structure_task(
        "DEMO", "fresh", "issue:F-LOAD", "generate_review_pack", {}
    )
    assert (
        fresh["brain_plan"]["source_control"][1]["decision_id"] == decision["node_id"]
    )
    assert "source_control" in fresh["input_snapshot"]


def test_desk_has_source_review_and_binds_displayed_digest(services):
    _, agent, human = services
    page = render_page(human, demo=True)
    assert "Review source authority" in page and "Imported labels" in page
    fields = {
        "operation": "document_control",
        "project_id": "DEMO",
        "record_id": SOURCE,
        "digest": human.document_control("DEMO", SOURCE)["control_digest"],
        "authority": "current",
        "evidence_ids": "evidence:EB",
        "rationale": "Synthetic review",
    }
    apply_action(human, fields)
    assert agent.source_control("DEMO", ["evidence:EB"])[0]["status"] == "current"
    with pytest.raises(ValueError, match="changed|Stale"):
        apply_action(human, fields)


def test_forged_brain_authority_context_is_rejected(services):
    _, agent, _ = services
    flow = DocumentAIWorkflow(agent)
    plan = flow.structure_task("DEMO", "T", "issue:F-LOAD", "generate_review_pack", {})[
        "brain_plan"
    ]
    plan["source_control"][1]["status"] = "governing"
    with pytest.raises(ValueError, match="control"):
        ExecutionService(agent).propose_task(
            "DEMO",
            "forged",
            "issue:F-LOAD",
            "generate_review_pack",
            {},
            brain_plan=plan,
        )


def test_empty_basis_and_revisionless_wrong_target_are_rejected(services):
    _, agent, human = services
    with pytest.raises(ValueError):
        human.review_document_control(
            "DEMO",
            SOURCE,
            human.document_control("DEMO", SOURCE)["control_digest"],
            "governing",
            [],
            "Synthetic",
        )
    with pytest.raises(ValueError, match="revision"):
        human.review_document_control(
            "DEMO",
            "document:BASIS-B",
            human.document_control("DEMO", "document:BASIS-B")["control_digest"],
            "governing",
            ["evidence:EB"],
            "Synthetic",
        )
    assert agent.list_records("DEMO", "decision") == []


def test_event_failure_rolls_back_authority_and_decision(services, monkeypatch):
    _, agent, human = services

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic event failure")

    monkeypatch.setattr(human, "_event", fail)
    with pytest.raises(RuntimeError):
        review(human)
    assert agent.list_records("DEMO", "decision") == []
    assert agent.document_control("DEMO", SOURCE)["status"] == "unverified"


def test_planning_and_assessment_keep_exact_source_authority_snapshot(services):
    from pipelines.llm.models import LLMResponse
    from pipelines.review.execution_workflow import build_reasoning_workflow

    _, agent, human = services
    first = review(human)

    class OfflineAdapter:
        def __init__(self):
            self.calls = []

        def analyse(self, request):
            self.calls.append(copy.deepcopy(request))
            return LLMResponse("offline-control-fixture", "synthetic", [], {})

    adapter = OfflineAdapter()
    flow = build_reasoning_workflow(agent, analysis_adapter=adapter)
    planned = flow.structure_task(
        "DEMO", "T", "issue:F-LOAD", "generate_review_pack", {}
    )
    ExecutionService(human).authorize_task(
        "DEMO", "T", planned["task_digest"], "Synthetic"
    )
    result = ExecutionService(agent).execute("DEMO", "T")
    review(human, "superseded")
    flow.assess_output("DEMO", "T")
    assert all(
        request.context["brain_plan"]["source_control"][1]["decision_id"]
        == first["node_id"]
        for request in adapter.calls
    )
    assert (
        result["attributes"]["inputs"]["snapshot"]["source_control"][1]["status"]
        == "governing"
    )


def test_revision_authority_does_not_inherit_to_another_revision(services):
    _, agent, human = services
    review(human)
    earlier = agent.source_control("DEMO", ["evidence:EA"])[0]
    assert earlier["status"] == "unverified" and earlier["decision_id"] is None


def test_mcp_human_confirmed_filter_tracks_review_without_new_privileges(services):
    import asyncio
    import json

    from engineering_registry.mcp_server import create_server

    _, agent, human = services
    server = create_server(agent)

    async def query():
        result = await server.call_tool(
            "search_evidence",
            {"project_id": "DEMO", "authority": "governing", "human_confirmed": True},
        )
        payload = result if isinstance(result, dict) else json.loads(result[0].text)
        assert payload["ok"] is True
        return payload["data"]

    assert asyncio.run(query())["total"] == 0
    review(human)
    result = asyncio.run(query())
    assert result["total"] == 1 and result["records"][0]["node_id"] == "evidence:EB"
    review(human, "superseded")
    assert asyncio.run(query())["total"] == 0

    async def tools():
        return [t.name for t in await server.list_tools()]

    assert "review_document_control" not in asyncio.run(tools())
