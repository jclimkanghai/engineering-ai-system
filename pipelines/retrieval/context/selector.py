from __future__ import annotations

from dataclasses import dataclass

from ..models import EvidenceChunk, RetrievalHit


@dataclass(frozen=True)
class SelectionPolicy:
    max_evidence: int = 12
    max_chars: int = 24_000
    max_estimated_tokens: int = 6_000
    min_score: float = 0.0
    remove_near_duplicates: bool = True


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in text.split() if len(token) > 1}


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def select_evidence(
    candidates: list[tuple[EvidenceChunk, RetrievalHit]],
    policy: SelectionPolicy,
) -> tuple[list[EvidenceChunk], list[dict[str, str]]]:
    selected: list[EvidenceChunk] = []
    exclusions: list[dict[str, str]] = []
    chars = 0
    effective_max_chars = min(policy.max_chars, policy.max_estimated_tokens * 4)
    for chunk, hit in sorted(
        candidates, key=lambda item: (-item[1].score, item[0].evidence_id)
    ):
        if hit.score < policy.min_score:
            exclusions.append(
                {"evidence_id": chunk.evidence_id, "reason": "below_score_threshold"}
            )
            continue
        if len(selected) >= policy.max_evidence:
            exclusions.append(
                {"evidence_id": chunk.evidence_id, "reason": "max_evidence"}
            )
            continue
        if policy.remove_near_duplicates and any(
            _jaccard(chunk.text, item.text) >= 0.9 for item in selected
        ):
            exclusions.append(
                {"evidence_id": chunk.evidence_id, "reason": "near_duplicate"}
            )
            continue
        if selected and chars + len(chunk.text) > effective_max_chars:
            exclusions.append(
                {"evidence_id": chunk.evidence_id, "reason": "context_budget"}
            )
            continue
        selected.append(chunk)
        chars += len(chunk.text)
    return selected, exclusions
