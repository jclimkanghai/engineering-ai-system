"""
Engineering Document AI - Document Registry

Purpose
-------
Maintain a controlled, traceable registry of project documents after
document ingestion.

The Document Registry is the bridge between:

    Source Documents
          ↓
    Docling Ingestion
          ↓
    Document Registry
          ↓
    Requirements / Retrieval / Review Agents

Core principles
---------------
1. A document is identified by its source hash and document ID.
2. Revision information is preserved rather than overwritten.
3. Supersession is explicit.
4. Duplicate files are detected.
5. Source provenance remains traceable.
6. Registry operations do not perform engineering interpretation.
7. Missing or uncertain metadata is represented explicitly.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("engineering_document_ai.document_registry")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGISTRY_VERSION = "1.0"

DEFAULT_REGISTRY_PATH = Path("data/processed/document_registry.json")

DOCUMENT_STATUSES = {
    "unknown",
    "draft",
    "for_review",
    "for_approval",
    "approved",
    "issued",
    "superseded",
    "withdrawn",
    "cancelled",
    "as_built",
    "final",
}

GOVERNING_STATUSES = {
    "unknown",
    "governing",
    "supporting",
    "superseded",
    "informative",
    "not_applicable",
}

CONFIDENTIALITY_LEVELS = {
    "public",
    "internal",
    "confidential",
    "commercially_sensitive",
    "restricted",
}


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """Return the current UTC timestamp."""

    return datetime.now(UTC).isoformat()


def calculate_sha256(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> str:
    """
    Calculate the SHA-256 hash of a file.

    The function reads the file incrementally to avoid loading large
    engineering documents into memory.
    """

    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)

    return digest.hexdigest()


def normalise_identifier(value: str) -> str:
    """
    Convert a human-readable value into a stable identifier component.
    """

    result = "".join(
        character.lower() if character.isalnum() else "_" for character in value
    )

    result = "_".join(part for part in result.split("_") if part)

    return result


def build_document_id(
    title: str,
    source_hash: str,
) -> str:
    """
    Generate a deterministic document ID.

    Example:

        Jetty General Arrangement
        +
        SHA-256
        ↓
        jetty_general_arrangement_a1b2c3d4e5f6
    """

    safe_title = normalise_identifier(title)

    return f"{safe_title}_{source_hash[:12]}"


# ---------------------------------------------------------------------------
# Document Record
# ---------------------------------------------------------------------------


@dataclass
class DocumentRecord:
    """
    Registry representation of one source document.

    This record intentionally mirrors the important fields in
    schemas/document.schema.yaml while remaining lightweight enough for
    local JSON registry storage.
    """

    document_id: str

    title: str

    source_file: str

    source_hash: str

    document_number: str | None = None

    document_type: str | None = None

    discipline: str | None = None

    revision: str | None = None

    revision_date: str | None = None

    issue_status: str = "unknown"

    governing_status: str = "unknown"

    project_id: str | None = None

    package_id: str | None = None

    contract_id: str | None = None

    confidentiality: str = "confidential"

    supersedes: list[str] | None = None

    superseded_by: list[str] | None = None

    related_documents: list[str] = field(default_factory=list)

    referenced_documents: list[str] = field(default_factory=list)

    referenced_standards: list[str] = field(default_factory=list)

    page_count: int | None = None

    table_count: int | None = None

    figure_count: int | None = None

    extraction_engine: str | None = None

    extraction_version: str | None = None

    extraction_quality: float | None = None

    extraction_quality_level: str | None = None

    ocr_used: bool = False

    ingestion_timestamp: str = field(default_factory=utc_now)

    registry_timestamp: str = field(default_factory=utc_now)

    registry_version: str = REGISTRY_VERSION

    notes: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class DocumentRegistry:
    """
    Persistent JSON document registry.

    The initial implementation intentionally uses a JSON file rather than
    introducing a database dependency.

    A database can be introduced later without changing the conceptual
    document model.
    """

    def __init__(
        self,
        registry_path: Path = DEFAULT_REGISTRY_PATH,
    ) -> None:

        self.registry_path = Path(registry_path)

        self.documents: dict[str, DocumentRecord] = {}

        self.load()

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def load(self) -> None:
        """Load the registry from disk if it exists."""

        if not self.registry_path.exists():
            LOGGER.info(
                "Document registry does not exist yet: %s",
                self.registry_path,
            )
            return

        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))

            records = payload.get(
                "documents",
                {},
            )

            for document_id, data in records.items():
                self.documents[document_id] = DocumentRecord(**data)

            LOGGER.info(
                "Loaded %d documents from registry.",
                len(self.documents),
            )

        except Exception as exc:
            raise RuntimeError(
                f"Unable to load document registry: {self.registry_path}"
            ) from exc

    def save(self) -> None:
        """Persist the current registry to disk."""

        self.registry_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "registry_version": REGISTRY_VERSION,
            "updated_at": utc_now(),
            "document_count": len(self.documents),
            "documents": {
                document_id: asdict(record)
                for document_id, record in self.documents.items()
            },
        }

        temporary_path = self.registry_path.with_suffix(".tmp")

        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temporary_path.replace(self.registry_path)

        LOGGER.info(
            "Document registry saved: %s",
            self.registry_path,
        )

    # -----------------------------------------------------------------------
    # Registration
    # -----------------------------------------------------------------------

    def register(
        self,
        record: DocumentRecord,
        save: bool = True,
    ) -> DocumentRecord:
        """
        Register a document.

        If the same document ID already exists, registration is rejected
        unless the source hash is identical.
        """

        self._validate_record(record)

        existing = self.documents.get(record.document_id)

        if existing is not None:
            raise ValueError(f"Document ID already registered: {record.document_id}")

        duplicates = self.find_by_hash(record.source_hash)

        if duplicates:
            LOGGER.warning(
                "Duplicate source file detected. "
                "Existing document_id=%s, new document_id=%s",
                duplicates[0].document_id,
                record.document_id,
            )

            record.notes.append("Source hash matches an existing registered document.")

        self.documents[record.document_id] = record

        if save:
            self.save()

        LOGGER.info(
            "Registered document: %s",
            record.document_id,
        )

        return record

    # -----------------------------------------------------------------------
    # Registration From Ingestion Manifest
    # -----------------------------------------------------------------------

    def register_from_manifest(
        self,
        manifest_path: Path,
        project_id: str | None = None,
        package_id: str | None = None,
        contract_id: str | None = None,
        save: bool = True,
    ) -> DocumentRecord:
        """
        Create and register a DocumentRecord from a Docling ingestion
        manifest.

        This is the primary bridge between the ingestion layer and the
        document registry.
        """

        manifest_path = Path(manifest_path)

        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        source_file = manifest.get("source_file")

        source_hash = manifest.get("source_hash")

        document_id = manifest.get("document_id")

        if not source_file:
            raise ValueError("Manifest does not contain source_file.")

        if not source_hash:
            raise ValueError("Manifest does not contain source_hash.")

        if not document_id:
            raise ValueError("Manifest does not contain document_id.")

        source_path = Path(source_file)

        title = source_path.stem

        quality = manifest.get(
            "extraction_quality",
            {},
        )

        output_files = manifest.get(
            "output_files",
            {},
        )

        record = DocumentRecord(
            document_id=document_id,
            title=title,
            source_file=source_file,
            source_hash=source_hash,
            project_id=project_id,
            package_id=package_id,
            contract_id=contract_id,
            page_count=manifest.get("page_count"),
            table_count=(
                self._read_count_from_manifest(
                    manifest,
                    "tables",
                )
            ),
            figure_count=(
                self._read_count_from_manifest(
                    manifest,
                    "figures",
                )
            ),
            extraction_engine="docling",
            extraction_version=manifest.get("docling_version"),
            extraction_quality=quality.get("overall_score"),
            extraction_quality_level=quality.get("level"),
            ocr_used=bool(
                manifest.get(
                    "ocr_used",
                    False,
                )
            ),
            metadata={
                "manifest_path": str(manifest_path),
                "output_files": output_files,
                "warnings": manifest.get(
                    "warnings",
                    [],
                ),
                "errors": manifest.get(
                    "errors",
                    [],
                ),
                "processing_status": manifest.get("status"),
            },
        )

        return self.register(
            record,
            save=save,
        )

    # -----------------------------------------------------------------------
    # Retrieval
    # -----------------------------------------------------------------------

    def get(
        self,
        document_id: str,
    ) -> DocumentRecord | None:
        """Return a document by ID."""

        return self.documents.get(document_id)

    def require(
        self,
        document_id: str,
    ) -> DocumentRecord:
        """Return a document or raise KeyError."""

        record = self.get(document_id)

        if record is None:
            raise KeyError(f"Document not found: {document_id}")

        return record

    def find_by_hash(
        self,
        source_hash: str,
    ) -> list[DocumentRecord]:
        """Find all documents using a source SHA-256 hash."""

        return [
            record
            for record in self.documents.values()
            if record.source_hash == source_hash
        ]

    def find_by_document_number(
        self,
        document_number: str,
    ) -> list[DocumentRecord]:
        """Find all records matching a document number."""

        return [
            record
            for record in self.documents.values()
            if record.document_number == document_number
        ]

    def find_by_revision(
        self,
        document_number: str,
        revision: str,
    ) -> list[DocumentRecord]:
        """Find documents matching number and revision."""

        return [
            record
            for record in self.documents.values()
            if (
                record.document_number == document_number
                and record.revision == revision
            )
        ]

    def find_by_project(
        self,
        project_id: str,
    ) -> list[DocumentRecord]:
        """Return documents belonging to a project."""

        return [
            record
            for record in self.documents.values()
            if record.project_id == project_id
        ]

    def find_by_package(
        self,
        package_id: str,
    ) -> list[DocumentRecord]:
        """Return documents belonging to a package."""

        return [
            record
            for record in self.documents.values()
            if record.package_id == package_id
        ]

    def find_governing_documents(
        self,
        project_id: str | None = None,
    ) -> list[DocumentRecord]:
        """Return documents marked as governing."""

        documents = [
            record
            for record in self.documents.values()
            if record.governing_status == "governing"
        ]

        if project_id is not None:
            documents = [
                record for record in documents if record.project_id == project_id
            ]

        return documents

    # -----------------------------------------------------------------------
    # Revision Relationships
    # -----------------------------------------------------------------------

    def link_supersession(
        self,
        superseding_document_id: str,
        superseded_document_id: str,
        save: bool = True,
    ) -> None:
        """
        Establish an explicit supersession relationship.

        The relationship is bidirectional in the registry.
        """

        superseding = self.require(superseding_document_id)

        superseded = self.require(superseded_document_id)

        if superseding.supersedes is None:
            superseding.supersedes = []
        if superseded.superseded_by is None:
            superseded.superseded_by = []

        if superseded_document_id not in superseding.supersedes:
            superseding.supersedes.append(superseded_document_id)

        if superseding_document_id not in superseded.superseded_by:
            superseded.superseded_by.append(superseding_document_id)

        superseded.governing_status = "superseded"

        if save:
            self.save()

    def link_related_documents(
        self,
        document_id_a: str,
        document_id_b: str,
        save: bool = True,
    ) -> None:
        """Create a bidirectional related-document relationship."""

        document_a = self.require(document_id_a)

        document_b = self.require(document_id_b)

        if document_id_b not in document_a.related_documents:
            document_a.related_documents.append(document_id_b)

        if document_id_a not in document_b.related_documents:
            document_b.related_documents.append(document_id_a)

        if save:
            self.save()

    # -----------------------------------------------------------------------
    # Status Management
    # -----------------------------------------------------------------------

    def update_status(
        self,
        document_id: str,
        issue_status: str | None = None,
        governing_status: str | None = None,
        save: bool = True,
    ) -> DocumentRecord:
        """Update document status."""

        record = self.require(document_id)

        if issue_status is not None:
            if issue_status not in DOCUMENT_STATUSES:
                raise ValueError(f"Unsupported issue status: {issue_status}")

            record.issue_status = issue_status

        if governing_status is not None:
            if governing_status not in GOVERNING_STATUSES:
                raise ValueError(f"Unsupported governing status: {governing_status}")

            record.governing_status = governing_status

        record.registry_timestamp = utc_now()

        if save:
            self.save()

        return record

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------

    @staticmethod
    def _validate_record(
        record: DocumentRecord,
    ) -> None:
        """Validate minimum registry requirements."""

        if not record.document_id:
            raise ValueError("document_id is required.")

        if not record.title:
            raise ValueError("title is required.")

        if not record.source_file:
            raise ValueError("source_file is required.")

        if not record.source_hash:
            raise ValueError("source_hash is required.")

        if len(record.source_hash) != 64:
            raise ValueError("source_hash must be a SHA-256 hash.")

        if record.issue_status not in DOCUMENT_STATUSES:
            raise ValueError(f"Unsupported issue_status: {record.issue_status}")

        if record.governing_status not in GOVERNING_STATUSES:
            raise ValueError(f"Unsupported governing_status: {record.governing_status}")

        if record.confidentiality not in CONFIDENTIALITY_LEVELS:
            raise ValueError(f"Unsupported confidentiality: {record.confidentiality}")

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        """Return registry statistics."""

        documents = list(self.documents.values())

        return {
            "registry_version": REGISTRY_VERSION,
            "document_count": len(documents),
            "total_documents": len(documents),
            "governing_count": sum(
                document.governing_status == "governing" for document in documents
            ),
            "superseded_count": sum(
                document.governing_status == "superseded" for document in documents
            ),
            "low_quality_count": sum(
                document.extraction_quality_level in {"low", "failed"}
                for document in documents
            ),
            "ocr_count": sum(document.ocr_used for document in documents),
            "table_document_count": sum(
                (document.table_count or 0) > 0 for document in documents
            ),
        }

    @staticmethod
    def _read_count_from_manifest(
        manifest: dict[str, Any], element_name: str
    ) -> int | None:
        """Read an optional count from a Docling ingestion manifest."""
        counts = manifest.get("element_counts")
        if isinstance(counts, dict):
            value = counts.get(element_name)
            if isinstance(value, int):
                return value
        return None


class RegistryBackedDocumentControl:
    """Read-only document authority view backed by Engineering Registry.

    The JSON registry is consulted only for ingestion-specific metadata such as
    extracted Markdown paths. Revision and governing state always come from the
    Registry graph and its human-authorised control updates.
    """

    def __init__(
        self, database: str | Path, project_id: str, manifest_path: str | Path
    ):
        self.database = Path(database).expanduser()
        self.project_id = project_id
        self.manifest = DocumentRegistry(Path(manifest_path).expanduser())

    def require(self, document_id: str) -> DocumentRecord:
        if not self.database.is_file():
            raise FileNotFoundError(
                "Engineering Registry database is required for document control"
            )
        from engineering_registry.service import Principal, RegistryService
        from engineering_registry.store import SQLiteGraphStore

        with SQLiteGraphStore(self.database) as store:
            service = RegistryService(
                store,
                Principal(
                    "document-control-reader", frozenset({self.project_id}), "reader"
                ),
            )
            control = service.controlled_document(self.project_id, document_id)
        if control is None:
            raise KeyError(f"Document not found in Engineering Registry: {document_id}")
        node = control["document"]
        attributes = dict(node.get("attributes") or {})
        cached = self.manifest.get(document_id)
        if cached is not None:
            # Cache metadata can locate extraction artifacts; Registry fields win.
            metadata = dict(cached.metadata)
            base = asdict(cached)
        else:
            metadata = {}
            base = {}
        revisions = control["revisions"]
        preferred_revision = attributes.get("revision")
        selected = next(
            (
                item
                for item in revisions
                if (item.get("attributes") or {}).get("revision") == preferred_revision
            ),
            revisions[-1] if revisions else None,
        )
        if selected is not None:
            attributes.update(selected.get("attributes") or {})
        authority_status = (control.get("authority_control") or {}).get(
            "status", "unverified"
        )
        if authority_status == "unverified":
            authority_status = "unknown"
        document_number = attributes.get("document_number") or base.get(
            "document_number"
        )
        return DocumentRecord(
            document_id=document_id,
            title=node.get("title") or base.get("title") or document_id,
            source_file=attributes.get("source_file")
            or base.get("source_file")
            or "UNKNOWN",
            source_hash=attributes.get("source_hash")
            or base.get("source_hash")
            or "0" * 64,
            document_number=document_number,
            document_type=attributes.get("document_type") or base.get("document_type"),
            discipline=attributes.get("discipline") or base.get("discipline"),
            revision=attributes.get("revision"),
            revision_date=attributes.get("revision_date"),
            issue_status=attributes.get("issue_status", "unknown"),
            governing_status=authority_status,
            project_id=self.project_id,
            package_id=attributes.get("package_id") or base.get("package_id"),
            contract_id=attributes.get("contract_id") or base.get("contract_id"),
            confidentiality=attributes.get("confidentiality")
            or base.get("confidentiality", "confidential"),
            supersedes=control["supersedes"],
            superseded_by=control["superseded_by"],
            extraction_engine=base.get("extraction_engine"),
            extraction_version=base.get("extraction_version"),
            extraction_quality=base.get("extraction_quality"),
            extraction_quality_level=base.get("extraction_quality_level"),
            ocr_used=base.get("ocr_used", False),
            metadata=metadata,
        )


# The implementation is retained for readers of historical ingestion files;
# this alias names its current role accurately for new integrations.
DocumentManifest = DocumentRegistry

# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------


def register_ingestion_manifest(
    manifest_path: Path,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    project_id: str | None = None,
    package_id: str | None = None,
    contract_id: str | None = None,
) -> DocumentRecord:
    """
    Convenience wrapper for registering a Docling manifest.
    """

    registry = DocumentRegistry(registry_path)

    return registry.register_from_manifest(
        manifest_path=manifest_path,
        project_id=project_id,
        package_id=package_id,
        contract_id=contract_id,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """
    Minimal command-line interface.

    Example:

        python -m pipelines.registry.document_registry \
            data/processed/manifests/document.manifest.json
    """

    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Register a Docling ingestion manifest "
            "in the Engineering Document AI document registry."
        )
    )

    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to Docling ingestion manifest.",
    )

    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help=("Document registry path. Default: data/processed/document_registry.json"),
    )

    parser.add_argument(
        "--project-id",
        default=None,
        help="Optional project ID.",
    )

    parser.add_argument(
        "--package-id",
        default=None,
        help="Optional package ID.",
    )

    parser.add_argument(
        "--contract-id",
        default=None,
        help="Optional contract ID.",
    )

    args = parser.parse_args()

    record = register_ingestion_manifest(
        manifest_path=args.manifest,
        registry_path=args.registry,
        project_id=args.project_id,
        package_id=args.package_id,
        contract_id=args.contract_id,
    )

    print(
        json.dumps(
            asdict(record),
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
