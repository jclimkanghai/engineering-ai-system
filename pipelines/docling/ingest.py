"""
Engineering Document AI - Docling Ingestion Pipeline

Purpose
-------
Convert engineering documents into structured, traceable outputs suitable
for downstream document control, requirement extraction, retrieval and
engineering review.

Design principles
-----------------
1. Preserve the original source file.
2. Calculate a deterministic SHA-256 hash.
3. Use Docling only for document ingestion/understanding.
4. Preserve page and element provenance where available.
5. Export Markdown and JSON representations.
6. Record processing metadata and warnings.
7. Assess basic extraction quality.
8. Never silently treat extraction as engineering verification.

This module deliberately does NOT perform:
- engineering judgement
- contractual interpretation
- compliance determination
- gap analysis
- risk assessment
- LLM reasoning

Those functions belong to downstream agents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_ROOT = Path("data/processed")
DEFAULT_MANIFEST_DIR = DEFAULT_OUTPUT_ROOT / "manifests"
DEFAULT_MARKDOWN_DIR = DEFAULT_OUTPUT_ROOT / "markdown"
DEFAULT_JSON_DIR = DEFAULT_OUTPUT_ROOT / "json"
DEFAULT_TEXT_DIR = DEFAULT_OUTPUT_ROOT / "text"
DEFAULT_ERROR_DIR = DEFAULT_OUTPUT_ROOT / "errors"

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".csv",
    ".txt",
    ".md",
}

LOGGER = logging.getLogger("engineering_document_ai.docling")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class ExtractionQuality:
    """Basic extraction quality assessment."""

    text_score: float = 0.0
    structure_score: float = 0.0
    table_score: float = 0.0
    overall_score: float = 0.0
    level: str = "failed"

    warnings: list[str] = field(default_factory=list)


@dataclass
class SourcePreflight:
    """Cheap source inspection used to avoid unnecessary layout/OCR work."""

    status: str = "unknown"
    format: str | None = None
    page_count: int | None = None
    pages_with_text: int = 0
    pages_with_images: int = 0
    text_char_count: int = 0
    text_density: float | None = None
    recommendation: str = "docling_layout_ocr"
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcessingManifest:
    """Traceable record of one ingestion operation."""

    document_id: str
    source_file: str
    source_hash: str

    processing_timestamp: str
    docling_version: str | None

    configuration_version: str = "1.0"

    output_files: dict[str, str] = field(default_factory=dict)

    page_count: int | None = None

    extraction_quality: dict[str, Any] = field(default_factory=dict)

    source_preflight: dict[str, Any] = field(default_factory=dict)

    ocr_used: bool = False
    table_extraction_used: bool = False
    figure_extraction_used: bool = False

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    status: str = "failed"


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return datetime.now(UTC).isoformat()


def calculate_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Calculate SHA-256 hash of a source file.

    The hash is used to establish source-file identity and reproducibility.
    """

    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)

    return digest.hexdigest()


def build_document_id(path: Path, source_hash: str) -> str:
    """
    Generate a deterministic document ID.

    The hash prefix prevents collisions between different files with the
    same filename.
    """

    safe_name = "".join(
        character.lower() if character.isalnum() else "_" for character in path.stem
    ).strip("_")

    return f"{safe_name}_{source_hash[:12]}"


def ensure_directories(output_root: Path) -> dict[str, Path]:
    """Create required processing directories."""

    directories = {
        "markdown": output_root / "markdown",
        "json": output_root / "json",
        "text": output_root / "text",
        "errors": output_root / "errors",
        "manifests": output_root / "manifests",
    }

    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)

    return directories


def validate_input(path: Path) -> None:
    """Validate that the source document can be processed."""

    if not path.exists():
        raise FileNotFoundError(f"Input document not found: {path}")

    if not path.is_file():
        raise ValueError(f"Input path is not a file: {path}")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension: {path.suffix}. "
            f"Supported extensions: {sorted(SUPPORTED_EXTENSIONS)}"
        )


def preflight_source(
    source_path: Path,
    *,
    minimum_chars_per_page: int = 80,
    minimum_text_page_ratio: float = 0.80,
) -> SourcePreflight:
    """Inspect a source cheaply before loading Docling models.

    The preflight is deliberately conservative. It only recommends text-first
    extraction when most PDF pages expose enough embedded text. It never
    claims that tables, drawings, or layout semantics were extracted.
    """
    suffix = source_path.suffix.lower()
    if suffix != ".pdf":
        return SourcePreflight(
            status="not_applicable",
            format=suffix.lstrip(".") or None,
            recommendation="docling_layout_ocr",
        )
    try:
        from pypdf import PdfReader
    except ImportError:
        return SourcePreflight(
            status="unavailable",
            format="pdf",
            recommendation="docling_layout_ocr",
            warnings=["pypdf is not installed; PDF text preflight was unavailable."],
        )
    try:
        pages = PdfReader(str(source_path)).pages
        lengths = []
        pages_with_images = 0
        for page in pages:
            lengths.append(len((page.extract_text() or "").strip()))
            try:
                if len(getattr(page, "images", [])):
                    pages_with_images += 1
            except Exception:
                # Image inspection is advisory; text density remains usable.
                pass
    except Exception as exc:
        return SourcePreflight(
            status="failed",
            format="pdf",
            recommendation="docling_layout_ocr",
            warnings=[f"PDF text preflight failed: {type(exc).__name__}."],
        )
    page_count = len(lengths)
    pages_with_text = sum(length >= minimum_chars_per_page for length in lengths)
    total_chars = sum(lengths)
    ratio = (pages_with_text / page_count) if page_count else 0.0
    eligible = (
        page_count > 0
        and ratio >= minimum_text_page_ratio
        and total_chars >= page_count * minimum_chars_per_page
    )
    warnings = []
    if pages_with_images and (
        not eligible or (total_chars / max(page_count, 1)) < minimum_chars_per_page * 4
    ):
        eligible = False
        warnings.append(
            "Embedded images were detected with limited text; layout/OCR was retained."
        )
    elif pages_with_images:
        warnings.append(
            "Embedded images were detected; text-first output does not reconstruct figures or tables."
        )
    elif not eligible:
        warnings.append(
            "Embedded text was insufficient for text-first extraction; layout/OCR may be required."
        )
    return SourcePreflight(
        status="complete",
        format="pdf",
        page_count=page_count,
        pages_with_text=pages_with_text,
        pages_with_images=pages_with_images,
        text_char_count=total_chars,
        text_density=(total_chars / page_count) if page_count else 0.0,
        recommendation="text_first" if eligible else "docling_layout_ocr",
        warnings=warnings,
    )


def extract_pdf_text_first(source_path: Path) -> list[str] | None:
    """Extract page text without OCR/layout models when pypdf can do so."""
    try:
        from pypdf import PdfReader

        pages = PdfReader(str(source_path)).pages
        texts = [(page.extract_text() or "").strip() for page in pages]
    except Exception:
        return None
    return texts if texts and any(texts) else None


# ---------------------------------------------------------------------------
# Docling Import
# ---------------------------------------------------------------------------


def load_docling():
    """
    Import Docling lazily.

    Lazy import allows the repository to remain importable for tests or
    metadata-only operations even when Docling has not yet been installed.
    """

    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Install project dependencies before "
            "running document ingestion."
        ) from exc

    return DocumentConverter


def get_docling_version() -> str | None:
    """Return installed Docling version when available."""

    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Docling Conversion
# ---------------------------------------------------------------------------


def convert_document(
    source_path: Path,
):
    """
    Convert a document using Docling.

    Returns
    -------
    conversion_result
        Native Docling conversion result.
    """

    DocumentConverter = load_docling()

    converter = DocumentConverter()

    LOGGER.info("Starting Docling conversion: %s", source_path)

    result = converter.convert(str(source_path))

    LOGGER.info("Docling conversion completed: %s", source_path)

    return result


# ---------------------------------------------------------------------------
# Export Functions
# ---------------------------------------------------------------------------


def export_markdown(result, output_path: Path) -> None:
    """Export Docling document to Markdown."""

    document = result.document

    markdown = document.export_to_markdown()

    output_path.write_text(
        markdown,
        encoding="utf-8",
    )


def export_json(result, output_path: Path) -> None:
    """
    Export Docling document to JSON.

    Docling's native JSON representation is preferred because it preserves
    structured document information required by downstream processing.
    """

    document = result.document

    if hasattr(document, "export_to_dict"):
        payload = document.export_to_dict()
    else:
        payload = document.model_dump(mode="json")

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def export_text(result, output_path: Path) -> None:
    """Export plain text representation."""

    document = result.document

    if hasattr(document, "export_to_text"):
        text = document.export_to_text()
    else:
        text = document.export_to_markdown()

    output_path.write_text(
        text,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Document Inspection
# ---------------------------------------------------------------------------


def get_page_count(result) -> int | None:
    """Attempt to determine document page count."""

    document = result.document

    try:
        pages = getattr(document, "pages", None)

        if pages is not None:
            return len(pages)

    except Exception:
        pass

    return None


def count_document_elements(result) -> dict[str, int]:
    """
    Obtain basic counts of structured document elements.

    The function intentionally uses defensive introspection because Docling's
    internal representation may evolve between versions.
    """

    document = result.document

    counts = {
        "tables": 0,
        "figures": 0,
        "pictures": 0,
        "texts": 0,
        "headings": 0,
    }

    try:
        tables = getattr(document, "tables", None)
        if tables is not None:
            counts["tables"] = len(tables)
    except Exception:
        pass

    try:
        pictures = getattr(document, "pictures", None)
        if pictures is not None:
            counts["pictures"] = len(pictures)
    except Exception:
        pass

    try:
        texts = getattr(document, "texts", None)
        if texts is not None:
            counts["texts"] = len(texts)
    except Exception:
        pass

    try:
        headings = getattr(document, "headings", None)
        if headings is not None:
            counts["headings"] = len(headings)
    except Exception:
        pass

    counts["figures"] = counts["pictures"]

    return counts


def detect_ocr_usage(result) -> bool:
    """
    Detect whether OCR appears to have been used.

    This is deliberately conservative. If the underlying Docling result does
    not expose reliable OCR metadata, return False rather than inventing it.
    """

    try:
        document = result.document

        for attribute in (
            "ocr",
            "ocr_used",
            "was_ocr",
        ):
            value = getattr(document, attribute, None)

            if isinstance(value, bool):
                return value

    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Quality Assessment
# ---------------------------------------------------------------------------


def assess_extraction_quality(
    result,
    markdown_path: Path,
) -> ExtractionQuality:
    """
    Perform a basic ingestion-level quality assessment.

    This is NOT engineering-quality verification.

    It only checks whether the extracted representation appears sufficiently
    populated for downstream processing.
    """

    quality = ExtractionQuality()

    try:
        markdown = markdown_path.read_text(encoding="utf-8")

    except Exception as exc:
        quality.warnings.append(f"Unable to read extracted Markdown: {exc}")
        return quality

    text_length = len(markdown.strip())

    if text_length == 0:
        quality.text_score = 0.0
        quality.warnings.append("No extracted text detected.")

    elif text_length < 500:
        quality.text_score = 0.50
        quality.warnings.append(
            "Very low extracted text volume; document may be scanned "
            "or extraction may be incomplete."
        )

    elif text_length < 2000:
        quality.text_score = 0.75

    else:
        quality.text_score = 1.0

    counts = count_document_elements(result)

    structure_indicators = 0

    if counts["texts"] > 0:
        structure_indicators += 1

    if counts["headings"] > 0:
        structure_indicators += 1

    if counts["tables"] > 0:
        structure_indicators += 1

    if counts["figures"] > 0:
        structure_indicators += 1

    if structure_indicators >= 3:
        quality.structure_score = 1.0

    elif structure_indicators == 2:
        quality.structure_score = 0.85

    elif structure_indicators == 1:
        quality.structure_score = 0.70

    else:
        quality.structure_score = 0.50
        quality.warnings.append("Limited structured elements detected.")

    if counts["tables"] > 0:
        quality.table_score = 1.0
    else:
        # No tables is not a failure.
        quality.table_score = 1.0

    quality.overall_score = (
        quality.text_score * 0.50
        + quality.structure_score * 0.35
        + quality.table_score * 0.15
    )

    if quality.overall_score >= 0.90:
        quality.level = "high"

    elif quality.overall_score >= 0.75:
        quality.level = "medium"

    elif quality.overall_score >= 0.50:
        quality.level = "low"

    else:
        quality.level = "failed"

    return quality


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------


def write_error_record(
    document_id: str,
    error: Exception,
    output_path: Path,
) -> None:
    """Write a machine-readable ingestion error record."""

    payload = {
        "document_id": document_id,
        "timestamp": utc_now(),
        "error_type": type(error).__name__,
        "error_message": str(error),
    }

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main Ingestion Function
# ---------------------------------------------------------------------------


def ingest_document(
    source_path: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    configuration_version: str = "1.0",
    preserve_source: bool = True,
) -> ProcessingManifest:
    """
    Run the complete ingestion pipeline for one document.
    """

    validate_input(source_path)

    directories = ensure_directories(output_root)

    source_hash = calculate_sha256(source_path)
    document_id = build_document_id(source_path, source_hash)

    manifest = ProcessingManifest(
        document_id=document_id,
        source_file=str(source_path.resolve()),
        source_hash=source_hash,
        processing_timestamp=utc_now(),
        docling_version=get_docling_version(),
        configuration_version=configuration_version,
    )

    LOGGER.info(
        "Processing document_id=%s source=%s",
        document_id,
        source_path,
    )

    preflight = preflight_source(source_path)
    manifest.source_preflight = asdict(preflight)
    manifest.warnings.extend(preflight.warnings)

    # -----------------------------------------------------------------------
    # Preserve source
    # -----------------------------------------------------------------------

    if preserve_source:
        raw_directory = output_root.parent / "raw"
        raw_directory.mkdir(parents=True, exist_ok=True)

        destination = raw_directory / source_path.name

        if not destination.exists():
            shutil.copy2(source_path, destination)

    try:
        # -------------------------------------------------------------------
        # Text-first conversion for clearly born-digital PDFs
        # -------------------------------------------------------------------
        base_name = document_id

        markdown_path = directories["markdown"] / f"{base_name}.md"
        json_path = directories["json"] / f"{base_name}.json"
        text_path = directories["text"] / f"{base_name}.txt"

        page_texts = (
            extract_pdf_text_first(source_path)
            if preflight.recommendation == "text_first"
            else None
        )
        if page_texts is not None:
            markdown = "\n\n".join(
                f"[PAGE:{index}]\n{text}"
                for index, text in enumerate(page_texts, start=1)
                if text
            )
            plain_text = "\n\n".join(text for text in page_texts if text)
            markdown_path.write_text(markdown, encoding="utf-8")
            text_path.write_text(plain_text, encoding="utf-8")
            json_path.write_text(
                json.dumps(
                    {
                        "extraction_method": "text_first",
                        "page_count": len(page_texts),
                        "pages": [
                            {"page": index, "text": text}
                            for index, text in enumerate(page_texts, start=1)
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            manifest.output_files = {
                "markdown": str(markdown_path),
                "json": str(json_path),
                "text": str(text_path),
            }
            manifest.page_count = len(page_texts)
            manifest.extraction_quality = asdict(
                ExtractionQuality(
                    text_score=1.0,
                    structure_score=0.0,
                    table_score=0.0,
                    overall_score=0.65,
                    level="medium",
                    warnings=[
                        "Text-first extraction used; table, drawing, and layout semantics were not reconstructed."
                    ],
                )
            )
            manifest.warnings.extend(manifest.extraction_quality["warnings"])
            manifest.status = "completed"
        else:
            # ---------------------------------------------------------------
            # Docling conversion for image-heavy or layout-sensitive sources
            # ---------------------------------------------------------------
            result = convert_document(source_path)

            # ---------------------------------------------------------------
            # Export
            # ---------------------------------------------------------------

            export_markdown(result, markdown_path)
            export_json(result, json_path)
            export_text(result, text_path)

            manifest.output_files = {
                "markdown": str(markdown_path),
                "json": str(json_path),
                "text": str(text_path),
            }

            # ---------------------------------------------------------------
            # Document information
            # ---------------------------------------------------------------

            manifest.page_count = get_page_count(result)

            counts = count_document_elements(result)

            manifest.ocr_used = detect_ocr_usage(result)

            manifest.table_extraction_used = counts["tables"] > 0

            manifest.figure_extraction_used = counts["figures"] > 0

            # ---------------------------------------------------------------
            # Quality
            # ---------------------------------------------------------------

            quality = assess_extraction_quality(
                result,
                markdown_path,
            )

            manifest.extraction_quality = asdict(quality)

            manifest.warnings.extend(quality.warnings)

            if quality.level in {"low", "failed"}:
                manifest.warnings.append(
                    "Extraction quality is below the preferred threshold. "
                    "Material engineering conclusions require review."
                )

            manifest.status = "completed"

    except Exception as exc:
        LOGGER.exception(
            "Document ingestion failed: %s",
            source_path,
        )

        manifest.status = "failed"
        manifest.errors.append(f"{type(exc).__name__}: {exc}")

        error_path = directories["errors"] / f"{document_id}.error.json"

        write_error_record(
            document_id,
            exc,
            error_path,
        )

    # -----------------------------------------------------------------------
    # Manifest
    # -----------------------------------------------------------------------

    manifest_path = directories["manifests"] / f"{document_id}.manifest.json"

    manifest_path.write_text(
        json.dumps(
            asdict(manifest),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return manifest


# ---------------------------------------------------------------------------
# Batch Processing
# ---------------------------------------------------------------------------


def discover_documents(input_directory: Path) -> list[Path]:
    """Find supported documents recursively."""

    if not input_directory.exists():
        raise FileNotFoundError(f"Input directory not found: {input_directory}")

    documents = [
        path
        for path in input_directory.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    return sorted(documents)


def ingest_directory(
    input_directory: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> list[ProcessingManifest]:
    """Process all supported documents in a directory."""

    documents = discover_documents(input_directory)

    LOGGER.info(
        "Found %d supported documents.",
        len(documents),
    )

    manifests = []

    for document in documents:
        manifest = ingest_document(
            source_path=document,
            output_root=output_root,
        )

        manifests.append(manifest)

    return manifests


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argument_parser() -> argparse.ArgumentParser:
    """Create command-line interface."""

    parser = argparse.ArgumentParser(
        description=("Engineering Document AI - Docling document ingestion pipeline.")
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Input document or directory.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=("Output root directory. Default: data/processed"),
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=[
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
        ],
        help="Logging level.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    parser = build_argument_parser()

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format=("%(asctime)s | %(levelname)s | %(name)s | %(message)s"),
    )

    try:
        if args.input.is_file():
            manifest = ingest_document(
                source_path=args.input,
                output_root=args.output,
            )

            print(
                json.dumps(
                    asdict(manifest),
                    ensure_ascii=False,
                    indent=2,
                )
            )

            return 0 if manifest.status == "completed" else 1

        if args.input.is_dir():
            manifests = ingest_directory(
                input_directory=args.input,
                output_root=args.output,
            )

            completed = sum(1 for item in manifests if item.status == "completed")

            failed = len(manifests) - completed

            print(
                f"Processed: {len(manifests)} | "
                f"Completed: {completed} | "
                f"Failed: {failed}"
            )

            return 0 if failed == 0 else 1

        parser.error("Input must be an existing file or directory.")

    except Exception as exc:
        LOGGER.error(
            "Fatal ingestion error: %s",
            exc,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
