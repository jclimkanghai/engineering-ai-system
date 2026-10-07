import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "mcp", reason="Install Registry's optional MCP extra for protocol integration tests"
)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from engineering_registry.models import GraphEdge, GraphNode, NodeType, RelationshipType
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore
from tests.support.project_run_harness import integrate


def test_project_run_tools_are_only_advertised_by_configured_host(
    run_fixture,  # noqa: F811
):  # noqa: F811
    from engineering_registry.mcp_server import create_server

    _, agent, _ = run_fixture
    server = create_server(agent)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert not any(
        name.startswith(("propose_project_run", "get_project_run")) for name in names
    )
    assert not {"propose_task", "execute_task"} & names


def test_ready_batch_dispatch_tool_requires_host_dispatcher(run_fixture):
    from engineering_ai_system.project_run import ProjectRunService
    from engineering_registry.mcp_server import create_server
    from tests.support.project_run_harness import alignment_report, workflow_for

    _, agent, _ = run_fixture
    service = ProjectRunService(
        agent, workflow_for(run_fixture), reviewer=alignment_report
    )
    without_dispatch = create_server(agent, project_run_service=service)
    with_dispatch = create_server(
        agent,
        project_run_service=service,
        project_run_dispatcher=lambda project_id, run_id, *, max_workers: {
            "project_id": project_id,
            "run_id": run_id,
            "max_workers": max_workers,
        },
    )
    without_names = {tool.name for tool in asyncio.run(without_dispatch.list_tools())}
    with_names = {tool.name for tool in asyncio.run(with_dispatch.list_tools())}

    assert "dispatch_project_run_ready_tasks" not in without_names
    assert "dispatch_project_run_ready_tasks" in with_names


def test_project_run_mcp_is_bounded_and_delegates_all_gates(run_fixture):  # noqa: F811
    import asyncio

    from engineering_ai_system.project_run import ProjectRunService
    from engineering_execution import ExecutionService as HumanExecutionService
    from engineering_registry.mcp_server import create_server
    from tests.support.project_run_harness import alignment_report, workflow_for

    configured = run_fixture
    _, agent, _ = configured
    brain = workflow_for(configured)
    service = ProjectRunService(
        agent, brain, reviewer=alignment_report, integrator=integrate
    )
    server = create_server(agent, project_run_service=service)

    async def scenario():
        names = {tool.name for tool in await server.list_tools()}
        assert {
            "propose_project_run",
            "get_project_run",
            "get_project_run_specialist_output",
            "request_project_run_gate1",
            "execute_project_run_task",
            "integrate_project_run",
            "request_project_run_gate2",
        }.issubset(names)
        assert (
            not {
                "approve_critical_plan",
                "decide_project_run_outcome",
                "promote_candidate_knowledge",
            }
            & names
        )

        async def call(name, arguments):
            result = await server.call_tool(name, arguments)
            return result if isinstance(result, dict) else json.loads(result[0].text)

        undeclared = await call(
            "propose_project_run",
            {
                "project_id": "DEMO",
                "run_id": "MCP-UNDECLARED-SPECIALIST",
                "issue_id": "issue:RUN",
                "tasks": [
                    {
                        "task_id": "MCP-UNDECLARED-T1",
                        "tool": "compare_revision",
                        "parameters": {
                            "base_evidence_id": "evidence:EA",
                            "head_evidence_id": "evidence:EB",
                        },
                    }
                ],
            },
        )
        assert undeclared["ok"] is False
        assert "specialist_assignment" in undeclared["error"]["message"]

        proposed = await call(
            "propose_project_run",
            {
                "project_id": "DEMO",
                "run_id": "MCP-RUN",
                "issue_id": "issue:RUN",
                "tasks": [
                    {
                        "task_id": "MCP-RUN-T1",
                        "tool": "compare_revision",
                        "parameters": {
                            "base_evidence_id": "evidence:EA",
                            "head_evidence_id": "evidence:EB",
                        },
                        "specialist_assignment": None,
                    }
                ],
                "execution_class": "engineering",
            },
        )
        assert proposed["ok"] is True
        status = await call(
            "get_project_run", {"project_id": "DEMO", "run_id": "MCP-RUN"}
        )
        assert status["data"]["run"]["run_id"] == "MCP-RUN"

        before_gate = await call(
            "execute_project_run_task",
            {
                "project_id": "DEMO",
                "run_id": "MCP-RUN",
                "task_id": "MCP-RUN-T1",
            },
        )
        assert before_gate["ok"] is False

        gate1 = await call(
            "request_project_run_gate1",
            {"project_id": "DEMO", "run_id": "MCP-RUN"},
        )
        assert gate1["data"]["report"]["status"] == "aligned"
        current = await call(
            "get_project_run", {"project_id": "DEMO", "run_id": "MCP-RUN"}
        )
        task = current["data"]["run"]["plan"]["tasks"][0]
        _, _, human = configured
        HumanExecutionService(human).authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "MCP run plan reviewed"
        )
        no_auth = await call(
            "execute_project_run_task",
            {
                "project_id": "DEMO",
                "run_id": "MCP-RUN",
                "task_id": "MCP-RUN-T1",
            },
        )
        assert no_auth["ok"] is True
        integrated = await call(
            "integrate_project_run",
            {"project_id": "DEMO", "run_id": "MCP-RUN"},
        )
        assert integrated["ok"] is True
        assert integrated["data"]["assessment_id"]
        gate2 = await call(
            "request_project_run_gate2",
            {"project_id": "DEMO", "run_id": "MCP-RUN"},
        )
        assert gate2["ok"] is True
        assert (
            gate2["data"]["integrated_assessment_id"]
            == integrated["data"]["assessment_id"]
        )
        assert (
            gate2["data"]["integrated_assessment_digest"]
            == integrated["data"]["assessment_digest"]
        )

    asyncio.run(scenario())


def test_real_stdio_discovery_is_scoped_bounded_and_cannot_approve(tmp_path):
    database = tmp_path / "registry.sqlite"
    with SQLiteGraphStore(database) as store:
        registry = RegistryService(store, Principal("setup", frozenset({"P"})))
        registry.register(
            GraphNode(
                "E",
                "P",
                NodeType.EVIDENCE,
                "Source",
                {"text": "Load check", "revision": "A", "governing_status": "current"},
            )
        )
        for evidence_id, text in (
            ("ER", "Inspect weekly"),
            ("ET", "Commercial terms"),
            ("EC", "Commercial terms"),
        ):
            registry.register(
                GraphNode(
                    evidence_id,
                    "P",
                    NodeType.EVIDENCE,
                    evidence_id,
                    {
                        "text": text,
                        "document_id": evidence_id,
                        "revision": "A",
                        "page": 1,
                        "locator": "page=1",
                        "governing_status": "current",
                    },
                )
            )
        registry.register(
            GraphNode(
                "R",
                "P",
                NodeType.REQUIREMENT,
                "Synthetic accepted requirement",
                {
                    "source_text": "Inspect weekly",
                    "source_evidence_ids": ["ER"],
                    "review_status": "reviewed",
                    "review_decision_id": "decision:R",
                },
            ),
            links=[
                GraphEdge(
                    "R:source", "P", "R", RelationshipType.SUPPORTED_BY, "ER", ["ER"]
                )
            ],
        )
        registry.propose_issue("P", "I", "Check", ["E"])
        registry.propose_issue(
            "P", "IP", "Proposal readiness", [], ["R"], work_type="proposal_readiness"
        )
        revision_nodes, revision_edges = [], []
        for document_id, evidence_id, revision in (
            ("TEMPLATE", "ET", "A"),
            ("CANDIDATE", "EC", "A"),
        ):
            revision_id = f"document-revision:{document_id}:{revision}"
            revision_nodes.append(
                GraphNode(
                    revision_id,
                    "P",
                    NodeType.DOCUMENT_REVISION,
                    f"{document_id} Rev {revision}",
                    {"document_id": document_id, "revision": revision},
                )
            )
            revision_edges.append(
                GraphEdge(
                    f"{evidence_id}:revision",
                    "P",
                    evidence_id,
                    RelationshipType.DERIVED_FROM,
                    revision_id,
                    [evidence_id],
                )
            )
        store.add_subgraph(revision_nodes, revision_edges)
        registry.propose_submission(
            "P",
            "SUBMISSION",
            "Synthetic proposal",
            "IP",
            "document-revision:CANDIDATE:A",
            "document-revision:TEMPLATE:A",
            ["R"],
            [
                {
                    "template_evidence_id": "ET",
                    "candidate_evidence_id": "EC",
                    "label": "Terms",
                }
            ],
            [
                {
                    "matrix_id": "M-R",
                    "requirement_id": "R",
                    "disposition": "included",
                    "proposal_evidence_ids": ["EC"],
                    "qualification": None,
                    "unresolved_question": None,
                    "owner": None,
                }
            ],
        )
        store.add_node(
            GraphNode("PRIVATE", "OTHER", NodeType.EVIDENCE, "Private source")
        )

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "engineering_registry.mcp_server",
                "--database",
                str(database),
                "--project",
                "P",
                "--offline",
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert {
                    "list_records",
                    "issue_context",
                    "propose_transition",
                    "search_evidence",
                }.issubset(names)
                assert not {"propose_task", "execute_task"} & names
                assert (
                    not {
                        "approve",
                        "review_proposal",
                        "authorize_task",
                        "close_issue",
                        "promote_lesson",
                        "verify_result",
                        "approve_submission",
                        "issue_proposal",
                    }
                    & names
                )

                def payload(result):
                    assert result.isError is not True
                    return json.loads(result.content[0].text)

                listed = payload(
                    await session.call_tool(
                        "list_records", {"project_id": "P", "limit": 1}
                    )
                )
                assert listed["ok"] is True and len(listed["data"]["records"]) == 1
                submissions = payload(
                    await session.call_tool(
                        "list_records", {"project_id": "P", "node_type": "submission"}
                    )
                )
                assert [r["node_id"] for r in submissions["data"]["records"]] == [
                    "SUBMISSION"
                ]
                submission = payload(
                    await session.call_tool(
                        "get_record", {"project_id": "P", "record_id": "SUBMISSION"}
                    )
                )
                assert submission["data"]["node_type"] == "submission"
                foreign_record = payload(
                    await session.call_tool(
                        "get_record", {"project_id": "OTHER", "record_id": "SUBMISSION"}
                    )
                )
                assert foreign_record["ok"] is False
                denied = payload(
                    await session.call_tool("list_records", {"project_id": "OTHER"})
                )
                assert (
                    denied["ok"] is False and denied["error"]["code"] == "access_denied"
                )
                invalid = await session.call_tool(
                    "list_records", {"project_id": "P", "limit": 1000}
                )
                assert invalid.isError is True
                missing = payload(
                    await session.call_tool(
                        "get_record", {"project_id": "P", "record_id": "MISSING"}
                    )
                )
                assert missing["ok"] is False
                filtered = payload(
                    await session.call_tool(
                        "search_evidence",
                        {"project_id": "P", "query": "Load", "revision": "B"},
                    )
                )
                assert filtered["data"]["records"] == []
                proposal = payload(
                    await session.call_tool(
                        "propose_transition",
                        {
                            "project_id": "P",
                            "issue_id": "I",
                            "to_status": "open",
                            "rationale": "Request engineer review",
                        },
                    )
                )
                assert proposal["data"]["status"] == "pending"
                templates = await session.list_resource_templates()
                assert any(
                    "issues" in t.uriTemplate for t in templates.resourceTemplates
                )
                resource = await session.read_resource(
                    "engineering://projects/P/issues/I"
                )
                assert (
                    json.loads(resource.contents[0].text)["data"]["issue"]["status"]
                    == "proposed"
                )
                project_resource = await session.read_resource(
                    "engineering://projects/P"
                )
                assert (
                    json.loads(project_resource.contents[0].text)["data"][
                        "client_project_brief"
                    ]["status"]
                    == "missing"
                )
                private = await session.read_resource("engineering://projects/OTHER")
                assert json.loads(private.contents[0].text)["ok"] is False

    asyncio.run(scenario())
    with SQLiteGraphStore(database) as store:
        assert store.get_issue("P", "I").status.value == "proposed"
