"""
Automated tests for the Engineering Document AI Docling ingestion pipeline.

These tests focus on deterministic, testable behaviour and deliberately
separate ingestion testing from engineering reasoning.

Run with:

    pytest tests/test_docling_ingest.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipelines.docling.ingest import (
    ExtractionQuality,
    assess_extraction_quality,
    build_document_id,
    calculate_sha256,
    discover_documents,
    ensure_directories,
    preflight_source,
    validate_input,
)

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_text_file(tmp_path: Path) -> Path:
    """Create a small representative engineering text document."""

    file_path = tmp_path / "sample_spec.txt"

    file_path.write_text(
        """
        PROJECT TECHNICAL SPECIFICATION

        1.0 GENERAL

        The Contractor shall provide all works required for completion.

        2.0 STRUCTURAL REQUIREMENTS

        The permanent structure shall have a design life of 50 years.

        3.0 MATERIALS

        Structural steel shall comply with the applicable project
        specification and approved material requirements.
        """,
        encoding="utf-8",
    )

    return file_path


@pytest.fixture
def sample_pdf_placeholder(tmp_path: Path) -> Path:
    """
    Create a minimal PDF-like test file.

    This fixture is intentionally not used to test Docling conversion.
    Actual Docling integration tests should use controlled PDF fixtures.
    """

    file_path = tmp_path / "sample.pdf"

    file_path.write_bytes(b"%PDF-1.4\n% Test fixture placeholder\n")

    return file_path


# ---------------------------------------------------------------------------
# Hash Tests
# ---------------------------------------------------------------------------


def test_calculate_sha256_returns_same_hash_for_same_file(
    sample_text_file: Path,
):
    """The same source file must always produce the same SHA-256 hash."""

    hash_1 = calculate_sha256(sample_text_file)
    hash_2 = calculate_sha256(sample_text_file)

    assert hash_1 == hash_2
    assert len(hash_1) == 64


def test_calculate_sha256_changes_when_file_changes(
    sample_text_file: Path,
):
    """Changing the source file must change its SHA-256 hash."""

    original_hash = calculate_sha256(sample_text_file)

    sample_text_file.write_text(
        sample_text_file.read_text(encoding="utf-8") + "\nAdditional requirement.",
        encoding="utf-8",
    )

    changed_hash = calculate_sha256(sample_text_file)

    assert original_hash != changed_hash


def test_preflight_does_not_route_non_pdf_to_text_first(sample_text_file: Path):
    result = preflight_source(sample_text_file)

    assert result.status == "not_applicable"
    assert result.recommendation == "docling_layout_ocr"


def test_preflight_handles_invalid_pdf_without_inventing_text(
    sample_pdf_placeholder: Path,
):
    result = preflight_source(sample_pdf_placeholder)

    assert result.format == "pdf"
    assert result.recommendation == "docling_layout_ocr"
    assert result.status in {"failed", "complete", "unavailable"}


def test_preflight_routes_text_rich_pdf_to_text_first(
    monkeypatch, sample_pdf_placeholder: Path
):
    class FakePage:
        images = []

        def extract_text(self):
            return "Engineering requirement. " * 20

    class FakeReader:
        def __init__(self, _path):
            self.pages = [FakePage(), FakePage()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))
    result = preflight_source(sample_pdf_placeholder)

    assert result.recommendation == "text_first"
    assert result.pages_with_text == 2


# ---------------------------------------------------------------------------
# Document ID Tests
# ---------------------------------------------------------------------------


def test_build_document_id_is_deterministic(
    sample_text_file: Path,
):
    """The same filename and hash must generate the same document ID."""

    source_hash = calculate_sha256(sample_text_file)

    document_id_1 = build_document_id(
        sample_text_file,
        source_hash,
    )

    document_id_2 = build_document_id(
        sample_text_file,
        source_hash,
    )

    assert document_id_1 == document_id_2


def test_build_document_id_contains_hash_prefix(
    sample_text_file: Path,
):
    """Document ID should contain a deterministic hash prefix."""

    source_hash = calculate_sha256(sample_text_file)

    document_id = build_document_id(
        sample_text_file,
        source_hash,
    )

    assert source_hash[:12] in document_id


# ---------------------------------------------------------------------------
# Input Validation Tests
# ---------------------------------------------------------------------------


def test_validate_input_accepts_supported_file(
    sample_text_file: Path,
):
    """Supported file types should pass validation."""

    validate_input(sample_text_file)


def test_validate_input_rejects_missing_file(
    tmp_path: Path,
):
    """Missing source files should raise FileNotFoundError."""

    missing_file = tmp_path / "does_not_exist.pdf"

    with pytest.raises(FileNotFoundError):
        validate_input(missing_file)


def test_validate_input_rejects_directory(
    tmp_path: Path,
):
    """A directory passed as a file should be rejected."""

    with pytest.raises(ValueError):
        validate_input(tmp_path)


def test_validate_input_rejects_unsupported_extension(
    tmp_path: Path,
):
    """Unsupported extensions should be rejected."""

    unsupported_file = tmp_path / "example.exe"
    unsupported_file.write_bytes(b"test")

    with pytest.raises(ValueError):
        validate_input(unsupported_file)


@pytest.mark.parametrize(
    "extension",
    [
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".csv",
        ".txt",
        ".md",
    ],
)
def test_supported_extensions_are_accepted(
    tmp_path: Path,
    extension: str,
):
    """All configured supported extensions should pass validation."""

    file_path = tmp_path / f"document{extension}"

    file_path.write_text(
        "Test document",
        encoding="utf-8",
    )

    validate_input(file_path)


# ---------------------------------------------------------------------------
# Directory Tests
# ---------------------------------------------------------------------------


def test_ensure_directories_creates_processing_structure(
    tmp_path: Path,
):
    """Required output directories should be created."""

    output_root = tmp_path / "processed"

    directories = ensure_directories(output_root)

    expected = {
        "markdown",
        "json",
        "text",
        "errors",
        "manifests",
    }

    assert set(directories.keys()) == expected

    for directory in directories.values():
        assert directory.exists()
        assert directory.is_dir()


# ---------------------------------------------------------------------------
# Document Discovery Tests
# ---------------------------------------------------------------------------


def test_discover_documents_finds_supported_files(
    tmp_path: Path,
):
    """Document discovery should find supported files recursively."""

    input_directory = tmp_path / "input"
    input_directory.mkdir()

    (input_directory / "document1.pdf").write_bytes(b"test")

    (input_directory / "document2.txt").write_text(
        "test",
        encoding="utf-8",
    )

    nested_directory = input_directory / "nested"
    nested_directory.mkdir()

    (nested_directory / "document3.docx").write_bytes(b"test")

    (input_directory / "ignore.exe").write_bytes(b"test")

    documents = discover_documents(input_directory)

    names = {document.name for document in documents}

    assert names == {
        "document1.pdf",
        "document2.txt",
        "document3.docx",
    }


def test_discover_documents_returns_sorted_results(
    tmp_path: Path,
):
    """Discovered documents should have deterministic ordering."""

    input_directory = tmp_path / "input"
    input_directory.mkdir()

    for name in [
        "z_document.pdf",
        "a_document.pdf",
        "m_document.pdf",
    ]:
        (input_directory / name).write_bytes(b"test")

    documents = discover_documents(input_directory)

    names = [document.name for document in documents]

    assert names == sorted(names)


def test_discover_documents_rejects_missing_directory(
    tmp_path: Path,
):
    """Missing input directories should raise FileNotFoundError."""

    missing_directory = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        discover_documents(missing_directory)


# ---------------------------------------------------------------------------
# Quality Assessment Tests
# ---------------------------------------------------------------------------


class FakeDocument:
    """Minimal fake document used for quality testing."""

    def __init__(
        self,
        tables=None,
        pictures=None,
        texts=None,
        headings=None,
    ):
        self.tables = tables or []
        self.pictures = pictures or []
        self.texts = texts or []
        self.headings = headings or []


class FakeResult:
    """Minimal fake Docling result."""

    def __init__(self, document):
        self.document = document


def create_markdown(
    path: Path,
    length: int,
):
    """Create deterministic Markdown content for quality tests."""

    path.write_text(
        "# Engineering Specification\n\n"
        + ("Engineering requirement. " * max(1, length // 25)),
        encoding="utf-8",
    )


def test_quality_assessment_identifies_empty_extraction(
    tmp_path: Path,
):
    """Empty extracted content should generate a warning."""

    markdown_path = tmp_path / "empty.md"
    markdown_path.write_text(
        "",
        encoding="utf-8",
    )

    result = FakeResult(FakeDocument())

    quality = assess_extraction_quality(
        result,
        markdown_path,
    )

    assert quality.text_score == 0.0
    assert quality.level in {"failed", "low"}
    assert quality.warnings


def test_quality_assessment_identifies_populated_document(
    tmp_path: Path,
):
    """A populated structured document should receive a positive score."""

    markdown_path = tmp_path / "document.md"

    create_markdown(
        markdown_path,
        3000,
    )

    result = FakeResult(
        FakeDocument(
            tables=[object()],
            pictures=[object()],
            texts=[object()],
            headings=[object()],
        )
    )

    quality = assess_extraction_quality(
        result,
        markdown_path,
    )

    assert quality.text_score == 1.0
    assert quality.structure_score == 1.0
    assert quality.overall_score >= 0.90
    assert quality.level == "high"


def test_quality_assessment_flags_low_text_volume(
    tmp_path: Path,
):
    """Very small extracted text should generate a warning."""

    markdown_path = tmp_path / "short.md"

    markdown_path.write_text(
        "Short extracted text.",
        encoding="utf-8",
    )

    result = FakeResult(
        FakeDocument(
            texts=[object()],
        )
    )

    quality = assess_extraction_quality(
        result,
        markdown_path,
    )

    assert quality.text_score < 1.0
    assert quality.warnings


# ---------------------------------------------------------------------------
# Data Model Tests
# ---------------------------------------------------------------------------


def test_extraction_quality_default_values():
    """ExtractionQuality should have safe defaults."""

    quality = ExtractionQuality()

    assert quality.text_score == 0.0
    assert quality.structure_score == 0.0
    assert quality.table_score == 0.0
    assert quality.overall_score == 0.0
    assert quality.level == "failed"
    assert quality.warnings == []


# ---------------------------------------------------------------------------
# Manifest Structure Test
# ---------------------------------------------------------------------------


def test_manifest_output_can_be_serialized(
    tmp_path: Path,
):
    """
    Verify that a representative manifest can be written as JSON.

    This test does not require Docling.
    """

    manifest = {
        "document_id": "sample_123456789abc",
        "source_file": "sample.pdf",
        "source_hash": "a" * 64,
        "processing_timestamp": "2026-01-01T00:00:00+00:00",
        "docling_version": "2.x",
        "configuration_version": "1.0",
        "output_files": {
            "markdown": "sample.md",
            "json": "sample.json",
            "text": "sample.txt",
        },
        "page_count": 10,
        "extraction_quality": {
            "overall_score": 0.95,
            "level": "high",
        },
        "ocr_used": False,
        "table_extraction_used": True,
        "figure_extraction_used": True,
        "warnings": [],
        "errors": [],
        "status": "completed",
    }

    manifest_path = tmp_path / "manifest.json"

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
        ),
        encoding="utf-8",
    )

    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert loaded["document_id"] == manifest["document_id"]
    assert loaded["source_hash"] == manifest["source_hash"]
    assert loaded["status"] == "completed"


# ---------------------------------------------------------------------------
# Engineering Safety Tests
# ---------------------------------------------------------------------------


def test_source_hash_is_sha256(
    sample_text_file: Path,
):
    """
    Verify that source identity uses a 64-character hexadecimal SHA-256 hash.
    """

    source_hash = calculate_sha256(sample_text_file)

    assert len(source_hash) == 64
    assert all(character in "0123456789abcdef" for character in source_hash)


def test_original_source_is_not_modified_by_hashing(
    sample_text_file: Path,
):
    """Hash calculation must not modify the source file."""

    before = sample_text_file.read_bytes()

    calculate_sha256(sample_text_file)

    after = sample_text_file.read_bytes()

    assert before == after
