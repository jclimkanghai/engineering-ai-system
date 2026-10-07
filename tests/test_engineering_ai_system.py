import tempfile
import tomllib
import unittest
from pathlib import Path

from engineering_ai_system import EngineeringAISystem
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


class EngineeringAISystemFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_distribution_metadata_is_the_unified_host_wheel(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as handle:
            metadata = tomllib.load(handle)
        included = set(metadata["tool"]["setuptools"]["packages"]["find"]["include"])

        self.assertEqual(metadata["project"]["name"], "engineering-ai-system")
        self.assertEqual(metadata["project"]["version"], "0.1.6")
        self.assertIn(
            "engineering-registry>=0.1.19,<0.2", metadata["project"]["dependencies"]
        )
        self.assertTrue({"pipelines*", "agents*", "engineering_ai_system*"} <= included)
        self.assertFalse((root / "engineering_ai_system" / "pyproject.toml").exists())

    def test_package_discovery_excludes_nested_build_copies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as handle:
            metadata = tomllib.load(handle)
        excluded = set(metadata["tool"]["setuptools"]["packages"]["find"]["exclude"])

        self.assertTrue({"*.build*", "*.dist*"} <= excluded)

    def test_system_components_must_share_registry_store(self) -> None:
        first = SQLiteGraphStore(self.root / "first.sqlite")
        second = SQLiteGraphStore(self.root / "second.sqlite")
        registry = RegistryService(first, Principal("lead", frozenset({"KDC"})))
        from engineering_document_ai_brain import DocumentAIWorkflow

        other_brain = DocumentAIWorkflow(
            RegistryService(second, Principal("lead", frozenset({"KDC"})))
        )
        with self.assertRaisesRegex(ValueError, "share one Registry store"):
            EngineeringAISystem(registry, other_brain)
        first.close()
        second.close()

    def test_direct_governed_facade_can_open_without_reviewer(self) -> None:
        from engineering_document_ai_brain import DocumentAIWorkflow

        store = SQLiteGraphStore(self.root / "registry.sqlite")
        try:
            registry = RegistryService(store, Principal("lead", frozenset({"KDC"})))
            system = EngineeringAISystem(registry, DocumentAIWorkflow(registry))
            self.assertEqual(system.execution_mode, "GOVERNED")
            self.assertIsNone(system.brain.alignment_reviewer)
        finally:
            store.close()

    def test_open_project_uses_exact_registry_database_path(self) -> None:
        database = self.root / "current" / "registry.sqlite3"
        system = EngineeringAISystem.open_project(
            database,
            "KDC",
            Principal("lead", frozenset({"KDC"})),
            create=True,
            mode="TEST",
        )
        self.assertEqual(Path(system.registry.store.path), database.resolve())
        system.close()

    def test_facade_composes_run_service_into_trusted_mcp_host(self) -> None:
        import asyncio

        from engineering_registry.identity import (
            ResolvedIdentity,
            StaticTrustedIdentityResolver,
        )

        principal = Principal("agent", frozenset({"KDC"}))
        system = EngineeringAISystem.open_project(
            self.root / "mcp.sqlite3", "KDC", principal, create=True, mode="TEST"
        )
        try:
            server = system.create_mcp_server(
                StaticTrustedIdentityResolver(
                    ResolvedIdentity(principal, "test-host", "verified test grants")
                )
            )
            names = {tool.name for tool in asyncio.run(server.list_tools())}
            self.assertIn("propose_project_run", names)
            self.assertIn("request_project_run_gate2", names)
            self.assertNotIn("decide_project_run_outcome", names)
        finally:
            system.close()

    def test_data_root_opens_physically_isolated_project_and_organization_stores(
        self,
    ) -> None:
        from engineering_registry.paths import (
            organization_registry_path,
            project_registry_path,
        )

        system = EngineeringAISystem.open_project_from_data_root(
            self.root / "data",
            "KDC",
            Principal("reviewer", frozenset({"KDC"}), "reviewer", frozenset({"ACME"})),
            organization_id="ACME",
            create=True,
            mode="TEST",
        )
        self.assertEqual(
            Path(system.registry.store.path),
            project_registry_path(self.root / "data", "KDC"),
        )
        self.assertEqual(
            Path(system.organization_registry.store.path),
            organization_registry_path(self.root / "data", "ACME"),
        )
        self.assertIsNotNone(system.organizational_knowledge)
        system.close()

    def test_task_decision_facade_persists_to_shared_project_registry(self) -> None:
        database = self.root / "registry.sqlite"
        system = EngineeringAISystem.open_project(
            database,
            "KDC",
            Principal("document-ai", frozenset({"KDC"})),
            create=True,
            mode="TEST",
        )
        try:
            system.registry.register(
                GraphNode("E1", "KDC", NodeType.EVIDENCE, "Reviewed source")
            )
            system.registry.propose_issue("KDC", "I1", "Assess interface", ["E1"])
            decision = system.record_task_decision(
                "KDC", "I1", "D1", "Retain interface", "Source constraint", ["E1"]
            )
            self.assertEqual(decision["attributes"]["authority"], "ai_lead")
            self.assertIsNotNone(system.registry.store.get_node("KDC", "D1"))
            context_ids = {
                record["node_id"]
                for record in system.registry.task_context("KDC", "I1")["records"]
            }
            self.assertIn("D1", context_ids)
        finally:
            system.close()

    def test_open_project_does_not_create_a_second_database_implicitly(self) -> None:
        database = self.root / "missing.sqlite"
        with self.assertRaisesRegex(FileNotFoundError, "exact existing path"):
            EngineeringAISystem.open_project(
                database,
                "KDC",
                Principal("lead", frozenset({"KDC"})),
                mode="TEST",
            )
        self.assertFalse(database.exists())

    def test_open_governed_project_can_open_without_reviewer(self) -> None:
        database = self.root / "new-registry.sqlite"
        system = EngineeringAISystem.open_project(
            database, "KDC", Principal("lead", frozenset({"KDC"})), create=True
        )
        try:
            self.assertEqual(system.execution_mode, "GOVERNED")
            self.assertIsNone(system.brain.alignment_reviewer)
            self.assertEqual(system.registry.store.bound_scope, "KDC")
        finally:
            system.close()

    def test_open_project_accepts_configured_reviewer(self) -> None:
        database = self.root / "reviewed-registry.sqlite"

        def reviewer(context: dict) -> dict:
            return {"status": "insufficient_information"}

        system = EngineeringAISystem.open_project(
            database,
            "KDC",
            Principal("lead", frozenset({"KDC"})),
            create=True,
            alignment_reviewer=reviewer,
        )
        self.assertIs(system.brain.alignment_reviewer, reviewer)
        system.close()

    def test_open_project_requires_explicit_project_grant(self) -> None:
        with self.assertRaisesRegex(PermissionError, "no grant"):
            EngineeringAISystem.open_project(
                self.root, "KDC", Principal("lead", frozenset({"OTHER"}))
            )

    def test_open_project_rejects_unsafe_project_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe"):
            EngineeringAISystem.open_project(
                self.root, "../KDC", Principal("lead", frozenset({"../KDC"}))
            )


if __name__ == "__main__":
    unittest.main()
