"""Document AI deterministic requirement extraction, separated from Registry control."""

from __future__ import annotations

from agents.requirement_agent.extractor import (
    SourceLocation,
    extract_requirements_from_paragraphs,
)
from engineering_registry.service import RegistryService


def extract_requirement_candidates(
    registry: RegistryService, project: str, evidence_ids: list[str]
) -> dict:
    """Propose source-preserving candidates; this function never approves a requirement."""
    registry.check_access(project, write=True)
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or len(evidence_ids) > 100
    ):
        raise ValueError("Requirement extraction needs bounded evidence identifiers")
    created, skipped, candidates = 0, [], []
    for evidence_id in sorted(set(evidence_ids)):
        evidence = registry.get_record(project, evidence_id)
        if evidence["node_type"] != "evidence":
            raise ValueError("Requirement extraction needs evidence records")
        attrs = evidence["attributes"]
        text = attrs.get("text")
        if not isinstance(text, str) or not text.strip():
            skipped.append(
                {
                    "evidence_id": evidence_id,
                    "reason": "UNKNOWN / INSUFFICIENT INFORMATION: no extracted text",
                }
            )
            continue
        location = SourceLocation(
            document_id=str(attrs.get("document_id") or evidence_id),
            document_number=attrs.get("document_number"),
            revision=attrs.get("revision"),
            page=attrs.get("page"),
            section=attrs.get("section") or attrs.get("locator"),
        )
        for item in extract_requirements_from_paragraphs(text.splitlines(), location):
            proposed = registry.propose_requirement_candidate(
                project,
                {
                    "requirement_id": item.requirement_id,
                    "title": item.requirement_text[:160],
                    "source_text": item.source_text,
                    "requirement_type": item.requirement_type,
                    "obligation_type": item.obligation_type,
                    "action": item.action,
                    "source_evidence_id": evidence_id,
                    "confidence": item.confidence,
                    "uncertainties": [
                        "Responsibility, applicability and acceptance criteria require human review."
                    ],
                    "extraction_method": "document_ai_deterministic_text",
                },
            )
            candidates.append(proposed)
            created += proposed["status"] == "pending_review"
    return {
        "project_id": project,
        "created": created,
        "candidates": candidates,
        "skipped": skipped,
        "human_review_required": True,
    }
