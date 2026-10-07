import hashlib
import sqlite3

import pytest

from engineering_registry.knowledge_stores import OrganizationalKnowledgeService
from engineering_registry.migration import apply_migration, validate_migration
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.paths import organization_registry_path, project_registry_path
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import GraphStoreError, SQLiteGraphStore


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migration_dry_run_and_apply_copy_scopes_without_touching_source(tmp_path):
    source = tmp_path / "shared.sqlite3"
    with SQLiteGraphStore(source) as store:
        store.add_node(GraphNode("P-A", "ALPHA", NodeType.EVIDENCE, "Project A"))
        store.add_node(GraphNode("P-B", "BETA", NodeType.EVIDENCE, "Project B"))
        store.add_node(
            GraphNode("O-1", "ORG:ACME", NodeType.LESSON, "Validated lesson")
        )
        store.put_record("ALPHA", "decisions", "D-1", {"decision_id": "D-1"})
        store.put_record(
            "ORG:ACME", "organizational_lesson_status", "O-1", {"status": "active"}
        )

    original = _sha(source)
    root = tmp_path / "separated"
    report = validate_migration(source, root)
    assert report["valid"] is True
    assert len(report["entries"]) == 3
    assert all(not entry["collision"] for entry in report["entries"])
    assert not project_registry_path(root, "ALPHA").exists()
    assert _sha(source) == original

    result = apply_migration(report)
    assert result["source_unchanged"] is True
    assert _sha(source) == original
    with SQLiteGraphStore(project_registry_path(root, "ALPHA")) as project_store:
        assert project_store.bound_scope == "ALPHA"
        assert project_store.project_ids() == {"ALPHA"}
        assert project_store.get_record("ALPHA", "decisions", "D-1") == {
            "decision_id": "D-1"
        }
    with SQLiteGraphStore(project_registry_path(root, "BETA")) as project_store:
        assert project_store.project_ids() == {"BETA"}
    org_path = organization_registry_path(root, "ACME")
    with SQLiteGraphStore(org_path) as organization_store:
        assert organization_store.bound_scope == "ORG:ACME"
        assert organization_store.project_ids() == {"ORG:ACME"}
        assert organization_store.get_record(
            "ORG:ACME", "organizational_lesson_status", "O-1"
        )
    assert all(entry["counts"] == entry["copied_counts"] for entry in result["entries"])


def test_migration_reports_existing_target_and_rejects_apply(tmp_path):
    source = tmp_path / "source.sqlite3"
    with SQLiteGraphStore(source) as store:
        store.add_node(GraphNode("E", "P", NodeType.EVIDENCE, "Evidence"))
    target = project_registry_path(tmp_path / "root", "P")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"occupied")
    report = validate_migration(source, tmp_path / "root")
    assert report["valid"] is False
    assert report["entries"][0]["collision"] is True
    with pytest.raises(ValueError, match="validation failed"):
        apply_migration(report)
    assert target.read_bytes() == b"occupied"


def test_migration_reports_missing_graph_references_before_apply(tmp_path):
    source = tmp_path / "broken.sqlite3"
    with SQLiteGraphStore(source) as store:
        store.add_node(GraphNode("E", "P", NodeType.EVIDENCE, "Evidence"))
    with sqlite3.connect(source) as connection:
        connection.execute(
            "INSERT INTO graph_edges VALUES (?,?,?,?,?)",
            ("P", "BROKEN", "MISSING-SOURCE", "supports", "MISSING-TARGET"),
        )
    original = _sha(source)
    report = validate_migration(source, tmp_path / "root")
    assert report["valid"] is False
    assert {item["kind"] for item in report["entries"][0]["missing_references"]} == {
        "edge_source",
        "edge_target",
    }
    with pytest.raises(ValueError, match="validation failed"):
        apply_migration(report)
    assert _sha(source) == original
    assert not project_registry_path(tmp_path / "root", "P").exists()


def test_migration_upgrades_legacy_source_without_registry_metadata(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    with SQLiteGraphStore(source) as store:
        store.add_node(GraphNode("E", "P", NodeType.EVIDENCE, "Evidence"))
    with sqlite3.connect(source) as connection:
        connection.execute("DROP TABLE registry_metadata")
    original = _sha(source)

    report = validate_migration(source, tmp_path / "root")
    assert report["valid"] is True
    result = apply_migration(report)

    target = project_registry_path(tmp_path / "root", "P")
    with SQLiteGraphStore(target) as migrated:
        assert migrated.bound_scope == "P"
        assert migrated.get_node("P", "E").title == "Evidence"
    assert result["entries"][0]["copied_counts"] == report["entries"][0]["counts"]
    assert _sha(source) == original


def test_migration_staging_failure_leaves_no_partial_targets(tmp_path, monkeypatch):
    import engineering_registry.migration as migration

    source = tmp_path / "shared.sqlite3"
    with SQLiteGraphStore(source) as store:
        store.add_node(GraphNode("A", "ALPHA", NodeType.EVIDENCE, "A"))
        store.add_node(GraphNode("B", "BETA", NodeType.EVIDENCE, "B"))
    root = tmp_path / "root"
    report = validate_migration(source, root)
    original = _sha(source)
    stage = migration._stage_scope
    count = 0

    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("synthetic staging failure")
        return stage(*args, **kwargs)

    monkeypatch.setattr(migration, "_stage_scope", fail_second)
    with pytest.raises(RuntimeError, match="staging failure"):
        apply_migration(report)
    assert not project_registry_path(root, "ALPHA").exists()
    assert not project_registry_path(root, "BETA").exists()
    assert _sha(source) == original


def test_registry_database_binding_prevents_scope_reuse(tmp_path):
    database = tmp_path / "one.sqlite3"
    with SQLiteGraphStore(database) as store:
        store.bind_scope("ALPHA")
        with pytest.raises(GraphStoreError, match="different scope"):
            store.bind_scope("BETA")
        with pytest.raises(GraphStoreError, match="cannot write ORG:ACME"):
            store.add_node(
                GraphNode("ORG-E", "ORG:ACME", NodeType.LESSON, "Not in project DB")
            )


def test_organisational_promotion_and_import_are_explicit_across_stores(tmp_path):
    project_path = project_registry_path(tmp_path, "ALPHA")
    organization_path = organization_registry_path(tmp_path, "ACME")
    destination_path = project_registry_path(tmp_path, "BETA")
    actor = Principal("human-1", frozenset({"ALPHA"}), "reviewer", frozenset({"ACME"}))
    with (
        SQLiteGraphStore(project_path) as project_store,
        SQLiteGraphStore(organization_path) as org_store,
        SQLiteGraphStore(destination_path) as destination_store,
    ):
        project_store.bind_scope("ALPHA")
        org_store.bind_scope("ORG:ACME")
        destination_store.bind_scope("BETA")
        project_store.add_node(
            GraphNode(
                "LESSON-1",
                "ALPHA",
                NodeType.LESSON,
                "Approved project lesson",
                {"scope": "project", "approved_by": "human-1"},
            )
        )
        source_registry = RegistryService(project_store, actor)
        organization_registry = RegistryService(org_store, actor)
        knowledge = OrganizationalKnowledgeService(
            source_registry, organization_registry
        )
        promoted = knowledge.promote(
            "ACME",
            "ORG-LESSON-1",
            "Validated reusable lesson",
            [("ALPHA", "LESSON-1")],
            observation="Repeated issue observed",
            result="Controlled outcome",
            interpretation="Reusable method",
            validated_statement="Use the checked method",
            applicability="Similar project context",
            limitations="Recheck current requirements",
            relevant_standards=[],
            review_due="2099-01-01",
            rationale="Human validation",
        )
        org_node = promoted["node"]
        status = promoted["status"]
        expected = digest({"lesson": org_node, "status": status})
        destination_principal = Principal(
            "human-1", frozenset({"BETA"}), "reviewer", frozenset({"ACME"})
        )
        destination_registry = RegistryService(
            destination_store,
            destination_principal,
            organizational_registry=organization_registry,
        )
        knowledge_for_import = OrganizationalKnowledgeService(
            destination_registry, organization_registry
        )
        imported = knowledge_for_import.import_lesson(
            "BETA",
            "ACME",
            "ORG-LESSON-1",
            "IMPORT-1",
            "Project review accepted",
            expected,
        )
        assert (
            imported["attributes"]["source_organizational_lesson"]["node_id"]
            == "ORG-LESSON-1"
        )
        assert imported["attributes"]["source_organizational_lesson_digest"] == digest(
            org_node
        )
        assert (
            destination_registry.organizational_lesson_status("ACME", "ORG-LESSON-1")[
                "status"
            ]
            == "active"
        )
        organization_registry.review_organizational_lesson(
            "ACME",
            "ORG-LESSON-1",
            digest({"lesson": org_node, "status": status}),
            "retired",
            "Superseded by a validated update",
        )
        assert (
            destination_registry.organizational_lesson_status("ACME", "ORG-LESSON-1")[
                "status"
            ]
            == "retired"
        )
        assert project_store.project_ids() == {"ALPHA"}
        assert org_store.project_ids() == {"ORG:ACME"}
        assert destination_store.project_ids() == {"BETA"}
