"""Tests for the Engineering Document AI document registry."""

from pathlib import Path

import pytest

from pipelines.registry.document_manifest import (
    DocumentRecord,
    DocumentRegistry,
    build_document_id,
    calculate_sha256,
)


def test_legacy_document_registry_module_reexports_manifest_api():
    from pipelines.registry.document_manifest import DocumentRecord
    from pipelines.registry.document_registry import (
        DocumentRecord as LegacyDocumentRecord,
    )

    assert LegacyDocumentRecord is DocumentRecord


def test_calculate_sha256(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("Engineering Document AI", encoding="utf-8")

    digest = calculate_sha256(source)

    assert len(digest) == 64
    assert digest == calculate_sha256(source)


def test_build_document_id_is_deterministic() -> None:
    document_id_1 = build_document_id(
        title="Sample EPC Tender",
        source_hash="a" * 64,
    )
    document_id_2 = build_document_id(
        title="Sample EPC Tender",
        source_hash="a" * 64,
    )

    assert document_id_1 == document_id_2


def test_build_document_id_changes_when_title_changes() -> None:
    document_id_1 = build_document_id(
        title="Sample EPC Tender",
        source_hash="a" * 64,
    )
    document_id_2 = build_document_id(
        title="Sample Technical Specification",
        source_hash="a" * 64,
    )

    assert document_id_1 != document_id_2


def test_build_document_id_changes_when_hash_changes() -> None:
    document_id_1 = build_document_id(
        title="Sample EPC Tender",
        source_hash="a" * 64,
    )
    document_id_2 = build_document_id(
        title="Sample EPC Tender",
        source_hash="b" * 64,
    )

    assert document_id_1 != document_id_2


def test_register_and_retrieve_document(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry = DocumentRegistry(registry_path)

    record = DocumentRecord(
        document_id="DOC-TEST-001",
        document_number="RFP-001",
        title="Sample EPC Tender",
        document_type="rfp",
        revision="0",
        issue_status="issued",
        governing_status="governing",
        source_file="RFP_Sample_Rev0.pdf",
        source_hash="a" * 64,
    )

    registry.register(record)

    retrieved = registry.get("DOC-TEST-001")

    assert retrieved is not None
    assert retrieved.document_number == "RFP-001"
    assert retrieved.revision == "0"
    assert retrieved.governing_status == "governing"


def test_registry_persists_between_instances(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"

    registry = DocumentRegistry(registry_path)

    record = DocumentRecord(
        document_id="DOC-TEST-002",
        document_number="TB-001",
        title="Tender Bulletin 01",
        document_type="tender_bulletin",
        revision="0",
        issue_status="issued",
        governing_status="governing",
        source_file="Tender_Bulletin_01.pdf",
        source_hash="b" * 64,
    )

    registry.register(record)

    new_registry = DocumentRegistry(registry_path)
    retrieved = new_registry.get("DOC-TEST-002")

    assert retrieved is not None
    assert retrieved.title == "Tender Bulletin 01"


def test_find_by_document_number(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry.json")

    registry.register(
        DocumentRecord(
            document_id="DOC-TEST-003",
            document_number="SPEC-001",
            title="Technical Specification",
            document_type="technical_specification",
            revision="0",
            source_file="Technical_Specification_Rev0.pdf",
            source_hash="c" * 64,
        )
    )

    results = registry.find_by_document_number("SPEC-001")

    assert len(results) == 1
    assert results[0].document_id == "DOC-TEST-003"


def test_find_by_hash(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry.json")

    source_hash = "d" * 64

    registry.register(
        DocumentRecord(
            document_id="DOC-TEST-004",
            document_number="BOQ-001",
            title="Sample BOQ",
            document_type="boq",
            revision="0",
            source_file="BOQ_Rev0.pdf",
            source_hash=source_hash,
        )
    )

    results = registry.find_by_hash(source_hash)

    assert len(results) == 1
    assert results[0].document_id == "DOC-TEST-004"


def test_duplicate_document_id_is_rejected(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry.json")

    record = DocumentRecord(
        document_id="DOC-DUPLICATE",
        document_number="RFP-002",
        title="Duplicate Test",
        document_type="rfp",
        revision="0",
        source_file="duplicate.pdf",
        source_hash="e" * 64,
    )

    registry.register(record)

    with pytest.raises(ValueError):
        registry.register(record)


def test_update_governing_status(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry.json")

    record = DocumentRecord(
        document_id="DOC-TEST-005",
        document_number="RFP-003",
        title="Tender Document",
        document_type="rfp",
        revision="0",
        source_file="RFP.pdf",
        source_hash="f" * 64,
        governing_status="governing",
    )

    registry.register(record)

    registry.update_status(
        "DOC-TEST-005",
        governing_status="superseded",
    )

    updated = registry.require("DOC-TEST-005")

    assert updated.governing_status == "superseded"


def test_register_does_not_infer_supersession(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry.json")

    revision_0 = DocumentRecord(
        document_id="DOC-RFP-REV0",
        document_number="RFP-004",
        title="Tender",
        document_type="rfp",
        revision="0",
        source_file="RFP_Rev0.pdf",
        source_hash="1" * 64,
    )

    revision_1 = DocumentRecord(
        document_id="DOC-RFP-REV1",
        document_number="RFP-004",
        title="Tender",
        document_type="rfp",
        revision="1",
        source_file="RFP_Rev1.pdf",
        source_hash="2" * 64,
    )

    registry.register(revision_0)
    registry.register(revision_1)

    assert registry.require("DOC-RFP-REV0").superseded_by is None
    assert registry.require("DOC-RFP-REV1").supersedes is None


def test_statistics(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "registry.json")

    registry.register(
        DocumentRecord(
            document_id="DOC-STAT-001",
            document_number="RFP-005",
            title="RFP",
            document_type="rfp",
            revision="0",
            source_file="rfp.pdf",
            source_hash="3" * 64,
        )
    )

    registry.register(
        DocumentRecord(
            document_id="DOC-STAT-002",
            document_number="TB-002",
            title="Tender Bulletin",
            document_type="tender_bulletin",
            revision="0",
            source_file="tb.pdf",
            source_hash="4" * 64,
        )
    )

    statistics = registry.statistics()

    assert statistics["total_documents"] == 2
