from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class ReviewApproval:
    reviewer_id: str
    decision: str
    reviewed_at: str
    output_digest: str


@dataclass
class StageResult:
    stage_id: str
    status: StageStatus
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ReviewRequest:
    document_ids: list[str]
    review_mode: str = "review"
    requested_scope: str | None = None
    compare_document_ids: list[str] = field(default_factory=list)
    require_human_gate: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    approval: ReviewApproval | dict[str, Any] | None = None


@dataclass
class ReviewResult:
    request: ReviewRequest
    stages: list[StageResult]
    findings: list[dict[str, Any]] = field(default_factory=list)
    final_status: StageStatus = StageStatus.PENDING
