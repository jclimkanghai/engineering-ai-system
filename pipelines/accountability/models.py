from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ResponsibilityRole(StrEnum):
    RESPONSIBLE_PARTY = "responsible_party"
    ACTION_OWNER = "action_owner"
    REVIEWER = "reviewer"
    APPROVER = "approver"


class AssignmentSourceClass(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResponsibilityAssignment:
    assignment_id: str
    party_id: str | None
    role: ResponsibilityRole
    responsibility_type: str
    source_evidence_ids: list[str] = field(default_factory=list)
    source_requirement_ids: list[str] = field(default_factory=list)
    source_class: AssignmentSourceClass = AssignmentSourceClass.UNKNOWN
    confidence: str = "unknown"
    human_review_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "role": self.role.value,
            "source_class": self.source_class.value,
        }
