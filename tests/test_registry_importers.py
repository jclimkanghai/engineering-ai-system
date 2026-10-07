import importlib

import pytest

from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import GraphIntegrityError, SQLiteGraphStore


def bundle():
    return {
        "schema_version": 1,
        "project_id": "P",
        "documents": [
            {
                "document_id": "D1",
                "title": "Basis",
                "revision": "A",
                "source_hash": "abc",
            },
            {
                "document_id": "D2",
                "title": "Basis",
                "revision": "B",
                "supersedes": ["D1"],
            },
        ],
        "evidence": [
            {
                "evidence_id": "SAME",
                "document_id": "D2",
                "revision": "B",
                "page": 4,
                "locator": "page=4",
                "text": "The pile shall be checked.",
            }
        ],
        "requirements": [
            {
                "requirement_id": "SAME",
                "source_text": "The pile shall be checked.",
                "source_evidence_ids": ["SAME"],
            }
        ],
        "findings": [
            {
                "finding_id": "F",
                "title": "Pile check",
                "finding": "Check needed",
                "source_evidence_ids": ["SAME"],
                "requirement_ids": ["SAME"],
            }
        ],
    }


def import_bundle(*args, **kwargs):
    assert importlib.util.find_spec("engineering_registry.importers") is not None
    return importlib.import_module("engineering_registry.importers").import_bundle(
        *args, **kwargs
    )


def test_full_bundle_preserves_colliding_legacy_ids_and_supersession(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        report = import_bundle(service, bundle(), dry_run=False)
        assert report["status"] == "imported"
        assert store.get_node("P", "evidence:SAME").attributes["page"] == 4
        assert (
            store.get_node("P", "requirement:SAME").attributes["source_text"]
            == "The pile shall be checked."
        )
        assert any(
            e.relationship.value == "supersedes"
            for e in store.get_edges("P", "document:D2")
        )
        assert service.issue_context("P", "issue:F")["issue"]["status"] == "proposed"
        assert (
            import_bundle(service, bundle(), dry_run=False)["status"]
            == "already_imported"
        )


def test_dry_run_and_failed_import_leave_no_partial_records(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        assert import_bundle(service, bundle())["status"] == "dry_run"
        assert service.list_records("P") == []
        bad = bundle()
        bad["findings"][0]["source_evidence_ids"] = ["MISSING"]
        with pytest.raises((GraphIntegrityError, ValueError)):
            import_bundle(service, bad, dry_run=False)
        assert service.list_records("P") == []


def test_conflicting_source_and_wrong_project_are_rejected(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        import_bundle(service, bundle(), dry_run=False)
        changed = bundle()
        changed["evidence"][0]["text"] = "Changed immutable source"
        with pytest.raises(GraphIntegrityError):
            import_bundle(service, changed, dry_run=False)
        changed["project_id"] = "OTHER"
        with pytest.raises(PermissionError):
            import_bundle(service, changed, dry_run=False)


def test_backup_and_restore_includes_aliases_history_and_requirements(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(
            store, Principal("human", frozenset({"P"}), "reviewer")
        )
        import_bundle(service, bundle(), dry_run=False)
        service.backup(tmp_path / "backup.sqlite")
    with SQLiteGraphStore(tmp_path / "backup.sqlite") as restored:
        assert restored.get_node("P", "requirement:SAME").attributes["source_text"]
        assert (
            restored.get_record("P", "aliases", "evidence:SAME")["canonical_id"]
            == "evidence:SAME"
        )
        assert restored.get_issue("P", "issue:F").source_finding_id == "F"


def test_document_ids_with_separators_keep_original_identity_and_typed_links(tmp_path):
    package = bundle()
    package["documents"][1]["document_id"] = "Design/B:1"
    package["evidence"][0]["document_id"] = "Design/B:1"
    package["findings"][0]["source_document_ids"] = ["Design/B:1"]
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        import_bundle(service, package, dry_run=False)
        assert store.get_issue("P", "issue:F").source_document_ids == ["Design/B:1"]
        assert any(
            e.target_node_id == "document:Design%2FB%3A1"
            for e in store.get_edges("P", "issue:F")
        )


@pytest.mark.parametrize(
    "collection", ["documents", "evidence", "requirements", "findings"]
)
def test_source_package_cannot_relabel_another_projects_records(tmp_path, collection):
    package = bundle()
    package[collection][0]["project_id"] = "OTHER"
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        with pytest.raises(GraphIntegrityError, match="project"):
            import_bundle(service, package, dry_run=False)
        assert service.list_records("P") == []


def test_new_revision_of_same_document_retains_previous_source_and_supersession(
    tmp_path,
):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        import_bundle(service, bundle(), dry_run=False)
        next_revision = {
            "schema_version": 1,
            "project_id": "P",
            "documents": [
                {
                    "document_id": "D2",
                    "title": "Updated basis",
                    "revision": "C",
                    "source_hash": "new-hash",
                    "supersedes_revisions": [{"document_id": "D2", "revision": "B"}],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "EC",
                    "document_id": "D2",
                    "revision": "C",
                    "page": 5,
                    "text": "The revised pile shall be checked.",
                }
            ],
            "requirements": [
                {
                    "requirement_id": "RC",
                    "source_text": "The revised pile shall be checked.",
                    "source_evidence_ids": ["EC"],
                }
            ],
            "findings": [
                {
                    "finding_id": "FC",
                    "finding": "Review updated source",
                    "source_evidence_ids": ["EC"],
                    "requirement_ids": ["RC"],
                }
            ],
        }
        import_bundle(service, next_revision, dry_run=False)
        assert (
            store.get_node("P", "document_revision:D2%3AB").attributes["revision"]
            == "B"
        )
        assert (
            store.get_node("P", "document_revision:D2%3AC").attributes["source_hash"]
            == "new-hash"
        )
        assert store.get_issue("P", "issue:F").evidence_ids == ["evidence:SAME"]
        assert any(
            e.target_node_id == "document_revision:D2%3AB"
            and e.relationship.value == "supersedes"
            for e in store.get_edges("P", "document_revision:D2%3AC")
        )
        assert any(
            e.target_node_id == "document_revision:D2%3AC"
            for e in store.get_edges("P", "evidence:EC")
        )
        context = service.issue_context("P", "issue:FC")
        assert any(
            n["node_id"] == "document_revision:D2%3AC"
            and n["attributes"]["source_hash"] == "new-hash"
            for n in context["linked_records"]
        )


def test_legacy_finding_json_migration_is_dry_run_source_preserving_and_idempotent(
    tmp_path,
):
    import json

    from engineering_registry.importers import import_legacy_finding_registry
    from engineering_registry.models import NodeType

    finding = {
        "finding_id": "F-LEGACY",
        "title": "Legacy finding",
        "finding": "A source check remains open.",
        "source_evidence_ids": [],
        "requirement_ids": [],
    }
    legacy = {
        "registry_version": 1,
        "project_id": "P",
        "applied_review_digests": ["review-1"],
        "entries": [
            {
                "finding_id": "F-LEGACY",
                "signature": "legacy-signature",
                "title": "Legacy finding",
                "project_id": "P",
                "lifecycle_status": "OPEN",
                "closure_status": "NOT_PROPOSED",
                "human_verified": False,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "observations": [
                    {
                        "review_digest": "review-1",
                        "source_finding_id": "F-LEGACY",
                        "reviewer_id": "reviewer-1",
                        "recorded_at": "2026-01-02T00:00:00Z",
                        "document_ids": [],
                        "document_revisions": {},
                        "source_evidence_ids": [],
                        "evidence_locations": [],
                        "finding": finding,
                    }
                ],
            }
        ],
    }
    source = tmp_path / "findings.json"
    source.write_text(json.dumps(legacy), encoding="utf-8")
    original = source.read_bytes()

    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(
            store, Principal("reviewer", frozenset({"P"}), "reviewer")
        )
        dry_run = import_legacy_finding_registry(service, source)
        assert dry_run["status"] == "dry_run"
        assert store.list_nodes("P", NodeType.FINDING) == []
        assert source.read_bytes() == original

        applied = import_legacy_finding_registry(service, source, dry_run=False)
        assert applied["status"] == "imported"
        issue_id = applied["records_requiring_review"][0]
        assert store.get_issue("P", issue_id).status.value == "proposed"
        finding_node = store.list_nodes("P", NodeType.FINDING)[0]
        assert (
            finding_node.attributes["legacy_registry_entry"]["observations"]
            == legacy["entries"][0]["observations"]
        )
        assert source.read_bytes() == original
        repeated = import_legacy_finding_registry(service, source, dry_run=False)
        assert repeated["status"] == "already_imported"


def test_legacy_finding_json_migration_reports_missing_source_references(tmp_path):
    import json

    from engineering_registry.importers import import_legacy_finding_registry

    source = tmp_path / "findings.json"
    source.write_text(
        json.dumps(
            {
                "registry_version": 1,
                "project_id": "P",
                "entries": [
                    {
                        "finding_id": "F",
                        "observations": [
                            {
                                "finding": {
                                    "finding_id": "F",
                                    "finding": "Check source.",
                                    "source_evidence_ids": ["MISSING"],
                                    "requirement_ids": [],
                                }
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(
            store, Principal("reviewer", frozenset({"P"}), "reviewer")
        )
        report = import_legacy_finding_registry(service, source, dry_run=False)
        assert report["status"] == "blocked"
        assert report["missing_references"] == [
            {"kind": "evidence", "legacy_id": "MISSING"}
        ]
        assert service.list_records("P") == []


def test_exact_repeat_finding_adds_observation_without_creating_duplicate_issue(
    tmp_path,
):
    from engineering_registry.models import NodeType

    first = bundle()
    first["run_id"] = "review-1"
    second = bundle()
    second["run_id"] = "review-2"
    second["findings"][0]["finding_id"] = "F-SECOND-OBSERVATION"

    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(
            store, Principal("reviewer", frozenset({"P"}), "reviewer")
        )
        initial = import_bundle(service, first, dry_run=False)
        reconciliation = service.reconcile_findings("P", second["findings"])
        assert reconciliation["matches"][0]["match_type"] == "possible_existing_match"
        assert reconciliation["matches"][0]["human_lifecycle_decision_required"] is True
        repeated = import_bundle(service, second, dry_run=False)
        issues = store.list_nodes("P", NodeType.ENGINEERING_ISSUE)
        assert len(issues) == 1
        assert (
            repeated["records_requiring_review"] == initial["records_requiring_review"]
        )
        observations = [
            node
            for node in store.list_nodes("P", NodeType.FINDING)
            if node.attributes.get("finding") == "Check needed"
        ]
        assert len(observations) == 2
        assert any(
            event["kind"] == "finding_observed"
            for event in service.history("P", issues[0].node_id)
        )


def test_legacy_finding_json_import_is_available_as_dry_run_cli(tmp_path, capsys):
    import json

    from engineering_registry.cli import main

    source = tmp_path / "findings.json"
    source.write_text(
        json.dumps({"registry_version": 1, "project_id": "P", "entries": []}),
        encoding="utf-8",
    )
    result = main(
        [
            "--database",
            str(tmp_path / "registry.sqlite"),
            "--project",
            "P",
            "import-finding-json",
            str(source),
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"


def test_legacy_document_json_migration_preserves_source_and_control_graph(tmp_path):
    import json

    from engineering_registry.importers import import_legacy_document_registry
    from engineering_registry.models import RelationshipType
    from engineering_registry.service import Principal, RegistryService
    from engineering_registry.store import SQLiteGraphStore

    source = tmp_path / "documents.json"
    payload = {
        "registry_version": "1.0",
        "documents": {
            "D1": {
                "document_id": "D1",
                "title": "Original",
                "source_file": "d1.pdf",
                "source_hash": "a" * 64,
                "project_id": "P",
                "revision": "A",
                "issue_status": "superseded",
                "governing_status": "superseded",
                "superseded_by": ["D2"],
                "supersedes": [],
            },
            "D2": {
                "document_id": "D2",
                "title": "Replacement",
                "source_file": "d2.pdf",
                "source_hash": "b" * 64,
                "project_id": "P",
                "revision": "B",
                "issue_status": "issued",
                "governing_status": "governing",
                "superseded_by": [],
                "supersedes": [],
            },
        },
    }
    source.write_text(json.dumps(payload), encoding="utf-8")
    original = source.read_bytes()
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(
            store, Principal("human", frozenset({"P"}), "reviewer")
        )
        dry_run = import_legacy_document_registry(service, source, project_id="P")
        assert dry_run["status"] == "dry_run"
        assert store.list_nodes("P", "document") == []
        applied = import_legacy_document_registry(
            service, source, project_id="P", dry_run=False
        )
        assert applied["status"] == "imported"
        assert store.get_record("P", "aliases", "document:D1")
        document_id = store.get_record("P", "aliases", "document:D2")["canonical_id"]
        assert any(
            edge.relationship == RelationshipType.SUPERSEDES
            for edge in store.get_edges("P", document_id)
        )
        assert source.read_bytes() == original
