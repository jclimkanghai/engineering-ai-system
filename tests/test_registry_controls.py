import importlib.util

import pytest

from engineering_registry.models import (
    EngineeringIssue,
    GraphNode,
    IssueStatus,
    NodeType,
)
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import GraphIntegrityError, SQLiteGraphStore


def _seed_control_documents(review_result, database):
    from dataclasses import asdict

    from engineering_registry.importers import import_bundle
    from engineering_registry.service import Principal as RegistryPrincipal
    from engineering_registry.service import RegistryService as CanonicalRegistryService
    from engineering_registry.store import SQLiteGraphStore as CanonicalStore
    from pipelines.registry.document_manifest import DocumentRegistry

    request = review_result.request
    legacy = DocumentRegistry(request.metadata["registry_path"])
    package = {
        "schema_version": 1,
        "project_id": "P",
        "documents": [
            {**asdict(legacy.require(doc_id)), "project_id": "P"}
            for doc_id in request.document_ids
        ],
    }
    with CanonicalStore(database) as store:
        service = CanonicalRegistryService(
            store, RegistryPrincipal("reviewer", frozenset({"P"}), "reviewer")
        )
        import_bundle(service, package, dry_run=False)


def test_registry_can_be_imported_without_document_ai():
    assert importlib.util.find_spec("engineering_registry") is not None


def test_deprecated_registry_import_paths_reexport_canonical_api():
    from engineering_registry.models import GraphNode as CanonicalGraphNode
    from pipelines.engineering_graph.models import GraphNode as OlderGraphNode
    from pipelines.registry.engineering.models import GraphNode as LegacyGraphNode

    assert LegacyGraphNode is CanonicalGraphNode
    assert OlderGraphNode is CanonicalGraphNode


def test_service_registers_project_and_round_trips(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        service = RegistryService(store, Principal("agent", frozenset({"P"})))
        service.register(GraphNode("P", "P", NodeType.PROJECT, "Project"))
        assert service.list_records("P")[0]["title"] == "Project"


def test_raw_issue_creation_cannot_claim_closed(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        with pytest.raises(GraphIntegrityError, match="proposed"):
            store.save_issue(
                EngineeringIssue("I", "P", "Issue", status=IssueStatus.CLOSED)
            )
        assert store.get_issue("P", "I") is None


def test_raw_issue_creation_requires_human_review(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        with pytest.raises(GraphIntegrityError, match="unverified"):
            store.save_issue(
                EngineeringIssue("I", "P", "Issue", human_review_required=False)
            )


def test_public_transaction_rolls_back_all_nested_writes(tmp_path):
    with SQLiteGraphStore(tmp_path / "registry.sqlite") as store:
        with pytest.raises(ValueError):
            with store.transaction():
                store.add_node(GraphNode("E", "P", NodeType.EVIDENCE, "Source"))
                raise ValueError("Rejected entire operation")
        assert store.get_node("P", "E") is None


def test_approval_refuses_changed_review_before_first_write(tmp_path):
    from pipelines.review.models import ReviewApproval
    from pipelines.review.production import approve_review, build_production_pipeline
    from tests.support.review_harness import (
        make_review_fixture,
        stage_data,
        valid_recording_adapter,
    )

    result = make_review_fixture(tmp_path, valid_recording_adapter())
    result.request.metadata["project_id"] = "P"
    result = build_production_pipeline(
        "compliance", analysis_adapter=valid_recording_adapter()
    ).run(result.request)
    gate = stage_data(result, "human_review_gate")
    approval = ReviewApproval(
        "human", "approved", "2026-10-01T12:00:00+08:00", gate["output_digest"]
    )
    result.findings[0]["finding"] = "Unreviewed replacement"
    approve_review(result, approval)
    assert gate["gate_passed"] is False
    assert gate["approval_status"] == "stale"
    assert not (tmp_path / "finding_registries").exists()


def test_review_can_persist_to_registry_without_json_dual_write(tmp_path):
    from pipelines.review.models import ReviewApproval
    from pipelines.review.production import approve_review, build_production_pipeline
    from tests.support.review_harness import (
        make_review_fixture,
        stage_data,
        valid_recording_adapter,
    )

    result = make_review_fixture(tmp_path, valid_recording_adapter())
    result.request.metadata.update(
        project_id="P", engineering_registry_path=str(tmp_path / "memory.sqlite")
    )
    _seed_control_documents(result, tmp_path / "memory.sqlite")
    result = build_production_pipeline(
        "compliance", analysis_adapter=valid_recording_adapter()
    ).run(result.request)
    gate = stage_data(result, "human_review_gate")
    approve_review(
        result,
        ReviewApproval(
            "human", "approved", "2026-10-01T12:00:00+08:00", gate["output_digest"]
        ),
    )
    assert gate["gate_passed"] is True
    assert not (tmp_path / "finding_registries").exists()
    with SQLiteGraphStore(tmp_path / "memory.sqlite") as store:
        issue_node = store.list_nodes("P", "engineering_issue")[0]
        assert store.get_issue("P", issue_node.node_id).status.value == "proposed"
        assert store.list_nodes("P", "requirement")[0].attributes["source_text"]
        approval = store.get_record("P", "review_approvals", gate["output_digest"])
        assert approval["reviewed_at"] == "2026-10-01T12:00:00+08:00"
        assert approval["authority_scope"] == "document_review_approval_only"
        assert approval["project_acceptance"] is False
        assert approval["human_outcome_decision"] is False


def test_approved_findings_cannot_write_to_legacy_json_registry(tmp_path):
    from pipelines.review.integration import persist_approved_findings

    path = tmp_path / "legacy-findings.json"
    with pytest.raises(ValueError, match="Engineering Registry"):
        persist_approved_findings(
            project_id="P",
            registry_path=str(path),
            findings=[],
            review_digest="review-digest",
            reviewer_id="human",
            document_ids=[],
            document_revisions={},
            evidence=[],
        )
    assert not path.exists()


def test_legacy_finding_registry_is_read_only(tmp_path):
    from pipelines.registry.finding_registry import FindingRegistry

    path = tmp_path / "legacy-findings.json"
    registry = FindingRegistry(path, "P")
    with pytest.raises(ValueError, match="read-only"):
        registry.record_approved_review(
            [],
            review_digest="review-digest",
            reviewer_id="human",
            document_ids=[],
            document_revisions={},
            evidence_by_id={},
        )
    assert not path.exists()


def test_document_control_authority_comes_from_engineering_registry(tmp_path):
    from engineering_registry.importers import import_bundle
    from engineering_registry.service import Principal, RegistryService
    from engineering_registry.store import SQLiteGraphStore
    from pipelines.registry.document_manifest import (
        DocumentRecord,
        DocumentRegistry,
        RegistryBackedDocumentControl,
    )

    manifest = tmp_path / "document-registry.json"
    cache = DocumentRegistry(manifest)
    cache.register(
        DocumentRecord(
            document_id="D1",
            title="Stale cached title",
            source_file="source.pdf",
            source_hash="a" * 64,
            project_id="P",
            revision="A",
            governing_status="governing",
            extraction_engine="docling",
            metadata={"output_files": {"markdown": "source.md"}},
        )
    )
    database = tmp_path / "engineering-registry.sqlite"
    package = {
        "schema_version": 1,
        "project_id": "P",
        "documents": [
            {
                "document_id": "D1",
                "title": "Controlled title",
                "revision": "A",
                "source_file": "source.pdf",
                "source_hash": "b" * 64,
                "issue_status": "issued",
                "governing_status": "supporting",
            }
        ],
        "evidence": [
            {
                "evidence_id": "E1",
                "title": "Human authority confirmation",
                "text": "The controlled project record confirms this revision is current.",
            }
        ],
    }
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("agent", frozenset({"P"}), "agent"))
        human = RegistryService(store, Principal("human", frozenset({"P"}), "reviewer"))
        import_bundle(
            human,
            package,
            dry_run=False,
        )
        revision_id = store.get_record("P", "aliases", "document_revision:D1:A")[
            "canonical_id"
        ]
        evidence_id = store.get_record("P", "aliases", "evidence:E1")["canonical_id"]
        view = human.document_control("P", revision_id)
        assert view["status"] == "unverified"
        with pytest.raises(PermissionError, match="Human reviewer"):
            agent.review_document_control(
                "P",
                revision_id,
                view["control_digest"],
                "governing",
                [evidence_id],
                "forged",
            )
        human.review_document_control(
            "P",
            revision_id,
            view["control_digest"],
            "governing",
            [evidence_id],
            "Confirmed against controlled project record",
        )
    record = RegistryBackedDocumentControl(database, "P", manifest).require("D1")
    assert record.title == "Controlled title"
    assert record.governing_status == "governing"
    assert record.source_hash == "b" * 64
    assert record.metadata["output_files"]["markdown"] == "source.md"


@pytest.mark.parametrize("replacement", ["persistence_plan", "displayed_findings"])
def test_replaced_stage_or_display_content_invalidates_approval(tmp_path, replacement):
    import copy

    from pipelines.review.models import ReviewApproval
    from pipelines.review.production import approve_review, build_production_pipeline
    from tests.support.review_harness import (
        make_review_fixture,
        stage_data,
        valid_recording_adapter,
    )

    fixture = make_review_fixture(tmp_path, valid_recording_adapter())
    fixture.request.metadata.update(
        project_id="P", engineering_registry_path=str(tmp_path / "memory.sqlite")
    )
    _seed_control_documents(fixture, tmp_path / "memory.sqlite")
    result = build_production_pipeline(
        "compliance", analysis_adapter=valid_recording_adapter()
    ).run(fixture.request)
    gate = stage_data(result, "human_review_gate")
    approval = ReviewApproval(
        "human", "approved", "2026-10-01T12:00:00+08:00", gate["output_digest"]
    )
    if replacement == "persistence_plan":
        stage = stage_data(result, "finding_registry_reconciliation")
        replaced = copy.deepcopy(stage["finding_registry_reconciliation"])
        replaced["registry_database"] = str(tmp_path / "unreviewed-destination.sqlite")
        stage["finding_registry_reconciliation"] = replaced
    else:
        gate["report_payload"]["findings"] = [
            {"finding": "Unreviewed displayed substitution"}
        ]
    approve_review(result, approval)
    assert gate["approval_status"] == "stale"
    assert gate["gate_passed"] is False
    with SQLiteGraphStore(tmp_path / "memory.sqlite") as store:
        assert store.list_records("P", "review_approvals") == []
    assert not (tmp_path / "unreviewed-destination.sqlite").exists()
