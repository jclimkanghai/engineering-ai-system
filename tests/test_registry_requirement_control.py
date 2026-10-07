"""Controlled requirement candidates: source wording first, human review second."""

import copy

import pytest

from engineering_registry.demo import seed_demo
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import GraphIntegrityError, SQLiteGraphStore


@pytest.fixture
def services(tmp_path):
    database = tmp_path / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("document-ai", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("synthetic-reviewer", frozenset({"DEMO"}), "reviewer")
        )
        yield database, agent, human


def candidate(requirement_id="REQ-SYNTHETIC", source_text="Inspect weekly"):
    return {
        "requirement_id": requirement_id,
        "title": "Synthetic inspection requirement",
        "source_text": source_text,
        "requirement_type": "mandatory",
        "obligation_type": "testing",
        "action": "inspect",
        "source_evidence_id": "evidence:EB",
        "confidence": "medium",
        "uncertainties": ["Responsible party is not established by this excerpt."],
        "extraction_method": "deterministic_text",
    }


def test_agent_candidate_is_pending_and_preserves_exact_source_context(services):
    _, agent, _ = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    assert proposed["status"] == "pending_review"
    assert proposed["candidate"]["source_text"] == "Inspect weekly"
    assert proposed["candidate"]["source_control"]["status"] == "unverified"
    assert proposed["candidate"]["source_evidence"]["node_id"] == "evidence:EB"
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")


def test_human_accepts_candidate_into_immutable_requirement_with_decision(services):
    database, agent, human = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    approved = human.review_requirement_candidate(
        "DEMO",
        proposed["candidate_id"],
        proposed["candidate_digest"],
        True,
        "Source wording reviewed",
    )
    requirement = agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")
    assert approved["status"] == "accepted"
    assert requirement["attributes"]["source_text"] == "Inspect weekly"
    assert requirement["attributes"]["review_status"] == "reviewed"
    assert requirement["attributes"]["authority_status"] == "unverified"
    assert requirement["attributes"]["review_decision_id"].startswith(
        "decision:requirement:"
    )
    with SQLiteGraphStore(database) as reopened:
        reader = RegistryService(
            reopened, Principal("reader", frozenset({"DEMO"}), "reader")
        )
        assert reader.get_record("DEMO", "requirement:REQ-SYNTHETIC") == requirement


def test_agent_cannot_accept_requirement_candidate(services):
    _, agent, _ = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    with pytest.raises(PermissionError):
        agent.review_requirement_candidate(
            "DEMO", proposed["candidate_id"], proposed["candidate_digest"], True, "No"
        )
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")


@pytest.mark.parametrize(
    "mutate",
    [
        "missing_source",
        "rewritten_wording",
        "foreign_evidence",
        "unknown_type",
        "duplicate",
    ],
)
def test_invalid_candidate_never_creates_requirement(services, mutate):
    _, agent, _ = services
    payload = candidate()
    if mutate == "missing_source":
        payload["source_text"] = "Not in source"
    elif mutate == "rewritten_wording":
        payload["source_text"] = "Inspection shall happen weekly"
    elif mutate == "foreign_evidence":
        payload["source_evidence_id"] = "missing"
    elif mutate == "unknown_type":
        payload["requirement_type"] = "approved"
    elif mutate == "duplicate":
        agent.propose_requirement_candidate("DEMO", payload)
        payload["source_text"] = "Design load: 20 kN"
    with pytest.raises((ValueError, PermissionError)):
        agent.propose_requirement_candidate("DEMO", payload)
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")


def test_authority_change_makes_candidate_review_stale(services):
    _, agent, human = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    source = human.document_control("DEMO", "document_revision:BASIS-B%3AB")
    human.review_document_control(
        "DEMO",
        "document_revision:BASIS-B%3AB",
        source["control_digest"],
        "current",
        ["evidence:EB"],
        "Synthetic source review",
    )
    with pytest.raises(ValueError, match="source control changed"):
        human.review_requirement_candidate(
            "DEMO",
            proposed["candidate_id"],
            proposed["candidate_digest"],
            True,
            "Stale",
        )
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")


def test_reject_keeps_candidate_and_never_creates_requirement(services):
    _, agent, human = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    rejected = human.review_requirement_candidate(
        "DEMO",
        proposed["candidate_id"],
        proposed["candidate_digest"],
        False,
        "Sentence is not an obligation",
    )
    assert rejected["status"] == "rejected"
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")
    assert (
        agent.list_requirement_candidates("DEMO")[0]["review_rationale"]
        == "Sentence is not an obligation"
    )


def test_repeated_same_candidate_is_idempotent_and_conflicting_id_is_refused(services):
    _, agent, _ = services
    first = agent.propose_requirement_candidate("DEMO", candidate())
    assert agent.propose_requirement_candidate("DEMO", candidate()) == first
    altered = candidate(source_text="Design load: 20 kN")
    with pytest.raises(ValueError, match="different source wording"):
        agent.propose_requirement_candidate("DEMO", altered)


def test_document_ai_deterministic_workflow_uses_existing_extractor_and_never_auto_accepts(
    services,
):
    from pipelines.review.requirement_workflow import extract_requirement_candidates

    _, agent, _ = services
    result = extract_requirement_candidates(agent, "DEMO", ["evidence:EB"])
    assert result["created"] >= 1
    proposed = next(
        item
        for item in agent.list_requirement_candidates("DEMO")
        if item["candidate"]["source_text"] == "Inspect weekly"
    )
    assert (
        proposed["candidate"]["extraction_method"] == "document_ai_deterministic_text"
    )
    assert all(
        item["status"] == "pending_review"
        for item in agent.list_requirement_candidates("DEMO")
    )


def test_candidate_review_is_bound_to_displayed_digest(services):
    _, agent, human = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    stale = copy.deepcopy(proposed)
    stale["candidate_digest"] = digest({"wrong": True})
    with pytest.raises(ValueError, match="Stale"):
        human.review_requirement_candidate(
            "DEMO", proposed["candidate_id"], stale["candidate_digest"], True, "No"
        )


def test_desk_shows_pending_requirement_and_reviewer_action(services):
    from engineering_registry.desk import apply_action, render_page

    _, agent, human = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    page = render_page(human, demo=True)
    assert "Proposed requirements awaiting review" in page
    assert "Accept extracted requirement" in page
    apply_action(
        human,
        {
            "operation": "review_requirement_candidate",
            "project_id": "DEMO",
            "record_id": proposed["candidate_id"],
            "digest": proposed["candidate_digest"],
            "accept": "yes",
            "rationale": "Synthetic desk review",
        },
    )
    assert (
        agent.get_record("DEMO", "requirement:REQ-SYNTHETIC")["attributes"][
            "review_status"
        ]
        == "reviewed"
    )


def test_mcp_agent_can_propose_but_not_review_requirement_candidate(services):
    import asyncio
    import json

    from engineering_registry.mcp_server import create_server

    _, agent, _ = services
    server = create_server(agent)

    async def call(name, arguments):
        response = await server.call_tool(name, arguments)
        payload = (
            response if isinstance(response, dict) else json.loads(response[0].text)
        )
        assert payload["ok"] is True
        return payload["data"]

    proposed = asyncio.run(
        call(
            "propose_requirement_candidate",
            {"project_id": "DEMO", "candidate": candidate()},
        )
    )
    assert proposed["status"] == "pending_review"

    async def names():
        return {tool.name for tool in await server.list_tools()}

    assert "review_requirement_candidate" not in asyncio.run(names())


def test_accepted_source_requirement_is_included_in_brain_context_without_mutating_issue_links(
    services,
):
    from engineering_document_ai_brain import DocumentAIWorkflow

    _, agent, human = services
    proposed = agent.propose_requirement_candidate("DEMO", candidate())
    human.review_requirement_candidate(
        "DEMO",
        proposed["candidate_id"],
        proposed["candidate_digest"],
        True,
        "Source wording reviewed",
    )
    context = agent.issue_context("DEMO", "issue:F-LOAD")
    assert context["issue"]["requirement_ids"] == ["requirement:R-LOAD"]
    assert "requirement:REQ-SYNTHETIC" in context["source_context_requirement_ids"]
    planned = DocumentAIWorkflow(agent).structure_task(
        "DEMO", "contextual", "issue:F-LOAD", "generate_review_pack", {}
    )
    assert "requirement:REQ-SYNTHETIC" in planned["brain_plan"]["requirement_ids"]
    assert any(
        record["node_id"] == "requirement:REQ-SYNTHETIC"
        for record in planned["input_snapshot"]["records"]
    )
