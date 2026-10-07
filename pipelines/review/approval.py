from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .models import ReviewApproval

_DIGEST_FIELDS = (
    "requirements",
    "coverage",
    "truth_assessments",
    "truth_coverage",
    "findings",
    "changes",
    "control_assessments",
    "controlled_outputs",
    "audit_trail",
    "qa",
    "review_items",
)


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__fspath__"):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_review_digest(payload: Any) -> str:
    """Canonical digest for one finalized, display-bound review artifact."""
    serialized = json.dumps(
        _json_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def review_output_digest(source: Any) -> str:
    """Hash the finalized review artifacts using stable canonical JSON."""
    artifacts = getattr(source, "artifacts", source)
    payload = {key: artifacts.get(key) for key in _DIGEST_FIELDS if key in artifacts}
    request = getattr(source, "request", None)
    if request is not None:
        payload["review_request"] = {
            "document_ids": request.document_ids,
            "compare_document_ids": request.compare_document_ids,
            "review_mode": request.review_mode,
            "requested_scope": request.requested_scope,
        }
        payload["document_records"] = [
            {
                "document_id": record.document_id,
                "document_number": record.document_number,
                "title": record.title,
                "revision": record.revision,
                "revision_date": record.revision_date,
                "issue_status": record.issue_status,
                "governing_status": record.governing_status,
                "source_hash": record.source_hash,
            }
            for record in getattr(source, "get", lambda *_: [])("document_records", [])
        ]
    return canonical_review_digest(payload)


def validate_approval(
    approval: ReviewApproval | None, output_digest: str
) -> tuple[str, str | None]:
    if approval is None:
        return "pending", "Human approval is required for this review output."
    if not approval.reviewer_id.strip():
        return "invalid", "Reviewer ID must not be blank."
    decision = approval.decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        return "invalid", "Approval decision must be 'approved' or 'rejected'."
    try:
        reviewed_at = datetime.fromisoformat(
            approval.reviewed_at.replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError):
        return "invalid", "Review timestamp must be valid ISO-8601 with a timezone."
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        return "invalid", "Review timestamp must include a timezone."
    if approval.output_digest != output_digest:
        return "stale", "Approval is bound to a different output digest."
    if decision == "rejected":
        return "rejected", "Reviewer rejected this output."
    return "approved", None
