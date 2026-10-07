"""Read-only compatibility reader for legacy finding-register JSON.

New findings and lifecycle changes belong in Engineering Registry. Matching
remains conservative: only exact normalized wording is suggested as a match.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from engineering_registry.service import finding_signature

REGISTRY_VERSION = 1


def _safe_project_component(project_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", project_id).strip("_")[:48] or "project"
    suffix = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{suffix}"


def default_finding_registry_path(
    project_id: str, document_registry_path: str | Path
) -> Path:
    base = Path(document_registry_path).expanduser().parent
    return base / "finding_registries" / f"{_safe_project_component(project_id)}.json"


class FindingRegistry:
    """Read-only project finding-register compatibility interface."""

    def __init__(self, path: str | Path, project_id: str) -> None:
        self.path = Path(path)
        self.project_id = project_id

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "registry_version": REGISTRY_VERSION,
                "project_id": self.project_id,
                "entries": [],
                "applied_review_digests": [],
            }
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("project_id") != self.project_id:
            raise ValueError("Finding registry project_id does not match this review")
        if not isinstance(payload.get("entries"), list):
            raise ValueError("Finding registry entries must be a list")
        payload.setdefault("applied_review_digests", [])
        return payload

    def reconcile(self, findings: list[dict[str, Any]]) -> dict[str, Any]:
        payload = self._read()
        by_signature: dict[str, list[dict[str, Any]]] = {}
        for entry in payload["entries"]:
            by_signature.setdefault(entry.get("signature", ""), []).append(entry)
        matches = []
        for finding in findings:
            signature = finding_signature(finding)
            existing = by_signature.get(signature, [])
            matches.append(
                {
                    "finding_id": finding.get("finding_id"),
                    "match_type": "possible_existing_match"
                    if existing
                    else "new_candidate",
                    "existing_finding_ids": [
                        entry.get("finding_id") for entry in existing
                    ],
                    "existing_findings": [
                        {
                            "finding_id": entry.get("finding_id"),
                            "title": entry.get("title"),
                            "lifecycle_status": entry.get("lifecycle_status", "OPEN"),
                        }
                        for entry in existing
                    ],
                    "automatic_action": "append_observation_only"
                    if existing
                    else "create_open_entry_after_approval",
                    "human_lifecycle_decision_required": bool(existing),
                }
            )
        return {
            "project_id": self.project_id,
            "status": "READY",
            "matches": matches,
            "automatic_closure": False,
        }

    def record_approved_review(
        self,
        findings: list[dict[str, Any]],
        *,
        review_digest: str,
        reviewer_id: str,
        document_ids: list[str],
        document_revisions: dict[str, str | None],
        evidence_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        raise ValueError(
            "Legacy finding JSON is read-only; migrate findings into Engineering Registry"
        )
