from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OutputRecord:
    output_id: str
    output_type: str
    finding_id: str | None
    status: str
    source_evidence_ids: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    human_review_required: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutiveSummary:
    output_id: str
    output_type: str
    finding_id: str | None
    status: str
    critical_issues: int
    key_risks: int
    required_decisions: int
    outstanding_information: int
    overall_review_status: str
    human_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditTrailRecord:
    audit_id: str
    finding_id: str
    evidence_ids: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    revisions: list[str] = field(default_factory=list)
    source_locations: list[str] = field(default_factory=list)
    final_disposition: str = "open"
    unresolved_links: list[str] = field(default_factory=list)
    human_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
