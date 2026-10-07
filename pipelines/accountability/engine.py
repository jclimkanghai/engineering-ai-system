from __future__ import annotations

import hashlib

from .models import (
    AssignmentSourceClass,
    ResponsibilityAssignment,
    ResponsibilityRole,
)


def _assignment_id(
    role: ResponsibilityRole, responsibility_type: str, party_id: str | None
) -> str:
    raw = "|".join((role.value, responsibility_type, party_id or "unknown"))
    return "ra_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def classify_assignment(
    *,
    party_id: str | None,
    role: str | ResponsibilityRole,
    responsibility_type: str,
    source_evidence_ids: list[str],
    source_requirement_ids: list[str] | None = None,
    source_class: str | AssignmentSourceClass = AssignmentSourceClass.UNKNOWN,
    confidence: str = "unknown",
    metadata: dict[str, object] | None = None,
) -> ResponsibilityAssignment:
    role_value = ResponsibilityRole(role)
    source_value = AssignmentSourceClass(source_class)
    review_required = (
        source_value
        in {
            AssignmentSourceClass.ASSUMED,
            AssignmentSourceClass.UNKNOWN,
        }
        or party_id is None
    )
    return ResponsibilityAssignment(
        assignment_id=_assignment_id(role_value, responsibility_type, party_id),
        party_id=party_id,
        role=role_value,
        responsibility_type=responsibility_type,
        source_evidence_ids=list(source_evidence_ids),
        source_requirement_ids=list(source_requirement_ids or []),
        source_class=source_value,
        confidence=confidence,
        human_review_required=review_required,
        metadata=dict(metadata or {}),
    )
