import copy

import pytest

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.demo import seed_demo
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import GraphIntegrityError, SQLiteGraphStore


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


def test_evidence_review_defaults_distinguish_extraction_availability_without_human_claim(
    services,
):
    _, agent, _ = services
    agent.register(
        GraphNode(
            "evidence:EMPTY",
            "DEMO",
            NodeType.EVIDENCE,
            "Synthetic empty page",
            {"document_id": "SYNTH", "revision": "A", "page": 2},
        )
    )
    available = agent.evidence_review("DEMO", "evidence:EB")
    missing = agent.evidence_review("DEMO", "evidence:EMPTY")
    assert available["status"] == "unreviewed_text_available"
    assert missing["status"] == "unreviewed_text_missing"
    assert available["decision_id"] is None and missing["decision_id"] is None
    assert available["human_reviewed"] is False


def test_human_evidence_review_preserves_source_and_history_after_reopen(services):
    database, agent, human = services
    before = copy.deepcopy(agent.get_record("DEMO", "evidence:EB"))
    view = human.evidence_review("DEMO", "evidence:EB")
    decision = human.review_evidence(
        "DEMO",
        "evidence:EB",
        view["review_digest"],
        "visually_reviewed",
        "Reviewed the source page against the imported text.",
    )
    assert decision["attributes"]["reviewed_by"] == "synthetic-reviewer"
    assert agent.get_record("DEMO", "evidence:EB") == before
    current = agent.evidence_review("DEMO", "evidence:EB")
    assert current["status"] == "visually_reviewed"
    assert current["decision_id"] == decision["node_id"]
    with SQLiteGraphStore(database) as reopened:
        reader = RegistryService(
            reopened, Principal("reader", frozenset({"DEMO"}), "reader")
        )
        assert (
            reader.evidence_review("DEMO", "evidence:EB")["decision_id"]
            == decision["node_id"]
        )
        assert any(
            event["kind"] == "evidence_reviewed"
            for event in reader.history("DEMO", "evidence:EB")
        )


@pytest.mark.parametrize("status", ["approved", "", "not-a-status"])
def test_invalid_or_agent_evidence_review_never_creates_decision(services, status):
    _, agent, human = services
    view = human.evidence_review("DEMO", "evidence:EB")
    with pytest.raises(ValueError):
        human.review_evidence(
            "DEMO", "evidence:EB", view["review_digest"], status, "Review"
        )
    with pytest.raises(PermissionError):
        agent.review_evidence(
            "DEMO", "evidence:EB", view["review_digest"], "visually_reviewed", "Review"
        )
    assert agent.list_records("DEMO", "decision") == []


def test_stale_evidence_review_is_rejected_without_decision(services):
    _, agent, human = services
    with pytest.raises(ValueError, match="changed"):
        human.review_evidence(
            "DEMO", "evidence:EB", "outdated", "visually_reviewed", "Review"
        )
    with pytest.raises(GraphIntegrityError):
        agent.get_record("DEMO", "decision:evidence:missing")


def test_evidence_review_stales_authorized_task_and_fresh_brain_keeps_context(services):
    _, agent, human = services
    flow = DocumentAIWorkflow(agent)
    planned = flow.structure_task(
        "DEMO", "before-review", "issue:F-LOAD", "generate_review_pack", {}
    )
    ExecutionService(human).authorize_task(
        "DEMO", "before-review", planned["task_digest"], "Synthetic scope"
    )
    view = human.evidence_review("DEMO", "evidence:EB")
    decision = human.review_evidence(
        "DEMO",
        "evidence:EB",
        view["review_digest"],
        "visually_reviewed",
        "Reviewed page before execution.",
    )
    with pytest.raises(ValueError, match="changed|stale"):
        ExecutionService(agent).execute("DEMO", "before-review")
    fresh = flow.structure_task(
        "DEMO", "after-review", "issue:F-LOAD", "generate_review_pack", {}
    )
    reviews = fresh["brain_plan"]["evidence_review"]
    assert any(item["decision_id"] == decision["node_id"] for item in reviews)
    assert "evidence_review" in fresh["input_snapshot"]


def test_desk_shows_evidence_requiring_human_review_and_binds_digest(services):
    from engineering_registry.desk import apply_action, render_page

    _, agent, human = services
    agent.register(
        GraphNode(
            "evidence:EMPTY", "DEMO", NodeType.EVIDENCE, "Synthetic empty page", {}
        )
    )
    page = render_page(human, demo=True)
    assert "Evidence requiring human review" in page
    assert "evidence:EMPTY" in page
    view = human.evidence_review("DEMO", "evidence:EB")
    fields = {
        "operation": "review_evidence",
        "project_id": "DEMO",
        "record_id": "evidence:EB",
        "digest": view["review_digest"],
        "status": "visually_reviewed",
        "rationale": "Synthetic desk review",
    }
    apply_action(human, fields)
    assert agent.evidence_review("DEMO", "evidence:EB")["human_reviewed"] is True
    with pytest.raises(ValueError, match="changed"):
        apply_action(human, fields)


def test_mcp_has_no_human_evidence_review_operation(services):
    import asyncio

    from engineering_registry.mcp_server import create_server

    _, agent, _ = services
    server = create_server(agent)

    async def names():
        return {tool.name for tool in await server.list_tools()}

    assert "review_evidence" not in asyncio.run(names())
