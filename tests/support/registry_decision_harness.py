"""Shared multi-project Registry fixture for decision lifecycle tests."""

from __future__ import annotations

from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


def setup_registry(tmp_path):
    store = SQLiteGraphStore(tmp_path / "registry.sqlite")
    agent = RegistryService(store, Principal("brain", frozenset({"P1", "P2"})))
    human = RegistryService(
        store,
        Principal(
            "engineer",
            frozenset({"P1", "P2"}),
            "reviewer",
            organization_ids=frozenset({"ACME"}),
        ),
    )
    for project in ("P1", "P2"):
        agent.register(GraphNode("E-" + project, project, NodeType.EVIDENCE, "Source"))
        agent.propose_issue(project, "I-" + project, "Review", ["E-" + project])
    return store, agent, human
