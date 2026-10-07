import asyncio
import json
import re

import pytest

from engineering_registry.demo import seed_demo
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


@pytest.fixture
def registry(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        service = RegistryService(store, Principal("reader", frozenset({"DEMO"})))
        yield service


def test_evidence_page_filters_and_paginates_without_returning_other_records(registry):
    registry.register(
        GraphNode(
            "evidence:EMPTY",
            "DEMO",
            NodeType.EVIDENCE,
            "Unparsed drawing page",
            {"document_id": "BASIS-B", "revision": "B", "page": 2},
        )
    )
    page = registry.evidence_page(
        "DEMO", document_id="BASIS-B", revision="B", limit=1, offset=1
    )
    assert page["total"] == 2
    assert page["records"] == [registry.get_record("DEMO", "evidence:EMPTY")]
    assert page["next_offset"] is None


def test_evidence_page_can_target_missing_text_for_human_review(registry):
    registry.register(
        GraphNode(
            "evidence:EMPTY",
            "DEMO",
            NodeType.EVIDENCE,
            "Unparsed drawing page",
            {"document_id": "BASIS-B", "revision": "B", "page": 2},
        )
    )
    page = registry.evidence_page("DEMO", missing_text=True, limit=25)
    assert [record["node_id"] for record in page["records"]] == ["evidence:EMPTY"]
    assert page["total"] == 1


def test_records_page_returns_a_bounded_typed_projection(registry):
    page = registry.records_page("DEMO", "document", limit=1)
    assert page["total"] == 2
    assert len(page["records"]) == 1
    assert page["next_offset"] == 1


def test_mcp_evidence_search_uses_the_bounded_registry_projection(
    registry, monkeypatch
):
    from engineering_registry.mcp_server import create_server

    original = registry.list_records

    def reject_unbounded_evidence(project_id, node_type=None):
        if node_type == "evidence":
            raise AssertionError("MCP must use the bounded evidence projection")
        return original(project_id, node_type)

    monkeypatch.setattr(registry, "list_records", reject_unbounded_evidence)
    server = create_server(registry)

    async def search():
        response = await server.call_tool(
            "search_evidence", {"project_id": "DEMO", "query": "20 kN", "limit": 1}
        )
        payload = (
            response if isinstance(response, dict) else json.loads(response[0].text)
        )
        assert payload["ok"] is True
        return payload["data"]

    result = asyncio.run(search())
    assert [record["node_id"] for record in result["records"]] == ["evidence:EB"]
    assert result["total"] == 1


def test_mcp_human_confirmed_search_uses_a_bounded_registry_projection(
    registry, monkeypatch
):
    from engineering_registry.mcp_server import create_server

    reviewer = RegistryService(
        registry.store, Principal("reviewer", frozenset({"DEMO"}), "reviewer")
    )
    source_id = "document_revision:BASIS-B%3AB"
    control = reviewer.document_control("DEMO", source_id)
    reviewer.review_document_control(
        "DEMO",
        source_id,
        control["control_digest"],
        "governing",
        ["evidence:EB"],
        "Reviewed the sample source revision.",
    )
    original = registry.list_records

    def reject_unbounded_evidence(project_id, node_type=None):
        if node_type == "evidence":
            raise AssertionError("MCP must use a bounded human-confirmed projection")
        return original(project_id, node_type)

    monkeypatch.setattr(registry, "list_records", reject_unbounded_evidence)
    server = create_server(registry)

    async def search():
        response = await server.call_tool(
            "search_evidence",
            {
                "project_id": "DEMO",
                "authority": "governing",
                "human_confirmed": True,
                "limit": 1,
            },
        )
        payload = (
            response if isinstance(response, dict) else json.loads(response[0].text)
        )
        assert payload["ok"] is True
        return payload["data"]

    result = asyncio.run(search())
    assert [record["node_id"] for record in result["records"]] == ["evidence:EB"]
    assert result["total"] == 1
    assert result["next_offset"] is None


def test_mcp_imported_authority_search_uses_the_bounded_registry_projection(
    registry, monkeypatch
):
    from engineering_registry.mcp_server import create_server

    original = registry.list_records

    def reject_unbounded_evidence(project_id, node_type=None):
        if node_type == "evidence":
            raise AssertionError("MCP must use a bounded imported-authority projection")
        return original(project_id, node_type)

    monkeypatch.setattr(registry, "list_records", reject_unbounded_evidence)
    server = create_server(registry)

    async def search():
        response = await server.call_tool(
            "search_evidence",
            {"project_id": "DEMO", "authority": "current", "limit": 1},
        )
        payload = (
            response if isinstance(response, dict) else json.loads(response[0].text)
        )
        assert payload["ok"] is True
        return payload["data"]

    result = asyncio.run(search())
    assert [record["node_id"] for record in result["records"]] == ["evidence:EB"]
    assert result["total"] == 1


def test_source_authority_form_lists_only_pages_from_the_selected_revision(registry):
    from engineering_registry.desk import render_page

    page = render_page(
        RegistryService(
            registry.store,
            Principal("reviewer", frozenset({"DEMO"}), "reviewer"),
        ),
        demo=True,
    )
    authority_forms = [
        form
        for form in re.findall(r"<form.*?</form>", page)
        if "document_control" in form
    ]
    assert len(authority_forms) == 2
    assert sorted(
        re.findall(r"<option value='(evidence:[^']+)'>", form)[-1]
        for form in authority_forms
    ) == ["evidence:EA", "evidence:EB"]


def test_desk_checks_only_the_displayed_page_of_missing_evidence(registry, monkeypatch):
    from engineering_registry.desk import render_page

    for number in range(101):
        registry.register(
            GraphNode(
                f"evidence:MISSING-{number:03d}",
                "DEMO",
                NodeType.EVIDENCE,
                f"Missing page {number}",
                {"document_id": "DRAWINGS", "page": number},
            )
        )
    reviewer = RegistryService(
        registry.store, Principal("reviewer", frozenset({"DEMO"}), "reviewer")
    )
    original = reviewer.evidence_review
    calls = 0

    def bounded_review(project_id, evidence_id):
        nonlocal calls
        calls += 1
        if calls > 100:
            raise AssertionError("Desk must not scan beyond the displayed review page")
        return original(project_id, evidence_id)

    monkeypatch.setattr(reviewer, "evidence_review", bounded_review)
    render_page(reviewer, demo=True)
    assert calls == 100


def test_desk_can_navigate_to_the_next_missing_evidence_page(registry, monkeypatch):
    from engineering_registry.desk import render_page

    for number in range(101):
        registry.register(
            GraphNode(
                f"evidence:MISSING-{number:03d}",
                "DEMO",
                NodeType.EVIDENCE,
                f"Missing page {number}",
                {"document_id": "DRAWINGS", "page": number},
            )
        )
    reviewer = RegistryService(
        registry.store, Principal("reviewer", frozenset({"DEMO"}), "reviewer")
    )
    original = reviewer.evidence_review
    calls = 0

    def one_page_only(project_id, evidence_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("Desk must review only the selected page")
        return original(project_id, evidence_id)

    monkeypatch.setattr(reviewer, "evidence_review", one_page_only)
    page = render_page(reviewer, demo=True, evidence_offset=100)
    assert "evidence:MISSING-100" in page
    assert "evidence:MISSING-000" not in page
    assert "?evidence_offset=0" in page


def test_desk_does_not_hydrate_an_entire_project_for_its_overview(
    registry, monkeypatch
):
    from engineering_registry.desk import render_page

    reviewer = RegistryService(
        registry.store, Principal("reviewer", frozenset({"DEMO"}), "reviewer")
    )
    original = reviewer.list_records

    def bounded_records(project_id, node_type=None):
        if node_type is None:
            raise AssertionError("Desk must use bounded typed record projections")
        return original(project_id, node_type)

    monkeypatch.setattr(reviewer, "list_records", bounded_records)
    page = render_page(reviewer, demo=True)
    assert "Project DEMO" in page


def test_task_context_is_the_shared_source_for_brain_and_execution(registry):
    from engineering_document_ai_brain import DocumentAIWorkflow

    context = registry.task_context("DEMO", "issue:F-LOAD")
    assert context["schema_version"] == 1
    assert context["evidence_ids"] == ["evidence:EA", "evidence:EB"]
    assert context["requirement_ids"] == ["requirement:R-LOAD"]
    assert context["context_digest"]

    task = DocumentAIWorkflow(registry).structure_task(
        "DEMO", "context-task", "issue:F-LOAD", "generate_review_pack", {}
    )
    assert task["input_snapshot"] == context["execution_snapshot"]
    assert task["brain_plan"]["source_control"] == context["source_control"]
    assert task["brain_plan"]["evidence_review"] == context["evidence_review"]


def test_task_context_uses_linked_requirement_and_approved_lesson_projections(
    registry, monkeypatch
):
    original = registry.list_records

    def reject_project_scans(project_id, node_type=None):
        if node_type in {"requirement", "lesson", "result"}:
            raise AssertionError("Task context must use bounded linked projections")
        return original(project_id, node_type)

    monkeypatch.setattr(registry, "list_records", reject_project_scans)
    context = registry.task_context("DEMO", "issue:F-LOAD")
    assert context["requirement_ids"] == ["requirement:R-LOAD"]
    assert context["lesson_ids"] == []
