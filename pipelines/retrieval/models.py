from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceClass(StrEnum):
    SOURCE_FACT = "source_fact"
    REQUIREMENT = "requirement"
    CONTEXT = "context"


@dataclass(frozen=True)
class EvidenceChunk:
    evidence_id: str
    document_id: str
    document_number: str | None
    title: str | None
    revision: str | None
    revision_date: str | None
    issue_status: str | None
    governing_status: str | None
    discipline: str | None
    section: str | None
    locator: str
    text: str
    evidence_class: str = EvidenceClass.SOURCE_FACT.value
    metadata: dict[str, Any] = field(default_factory=dict)
    # Optional source page. Kept at the end for positional compatibility.
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "document_number": self.document_number,
            "title": self.title,
            "revision": self.revision,
            "revision_date": self.revision_date,
            "issue_status": self.issue_status,
            "governing_status": self.governing_status,
            "discipline": self.discipline,
            "section": self.section,
            "locator": self.locator,
            "text": self.text,
            "evidence_class": self.evidence_class,
            "metadata": self.metadata,
            "page": self.page,
        }


@dataclass(frozen=True)
class RetrievalHit:
    evidence_id: str
    score: float
    lexical_score: float
    semantic_score: float
    match_reasons: tuple[str, ...] = ()


@dataclass
class RetrievalQuery:
    query: str
    document_ids: list[str] = field(default_factory=list)
    document_numbers: list[str] = field(default_factory=list)
    revisions: list[str] = field(default_factory=list)
    disciplines: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    evidence_classes: list[str] = field(default_factory=list)
    governing_only: bool = False
    top_k: int = 12
    min_score: float = 0.0
    include_context: bool = True


@dataclass
class EvidenceBundle:
    query: str
    evidence: list[dict[str, Any]]
    hits: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    retrieval_method: str = "lexical"

    def evidence_ids(self) -> list[str]:
        return [item["evidence_id"] for item in self.evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "evidence": self.evidence,
            "hits": self.hits,
            "warnings": self.warnings,
            "retrieval_method": self.retrieval_method,
        }
