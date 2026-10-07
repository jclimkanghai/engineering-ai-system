from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ControlRelationType(StrEnum):
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    MODIFIES = "modifies"
    MODIFIED_BY = "modified_by"
    CLARIFIES = "clarifies"
    CLARIFIED_BY = "clarified_by"
    REPLACES = "replaces"
    REPLACED_BY = "replaced_by"
    REFERENCES = "references"
    REFERENCED_BY = "referenced_by"
    RELATED = "related"


@dataclass(frozen=True)
class DocumentControlRecord:
    document_id: str
    document_number: str | None = None
    title: str | None = None
    document_type: str | None = None
    discipline: str | None = None
    revision: str | None = None
    issue_status: str = "unknown"
    governing_status: str = "unknown"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentControlRelation:
    source_document_id: str
    target_document_id: str
    relation_type: ControlRelationType
    evidence_ids: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class ControlAssessment:
    document_id: str
    governing_status: str
    confidence: float
    warnings: tuple[str, ...] = ()
    active_revision: str | None = None
    superseded: bool = False
    conflict: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "governing_status": self.governing_status,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "active_revision": self.active_revision,
            "superseded": self.superseded,
            "conflict": self.conflict,
        }
