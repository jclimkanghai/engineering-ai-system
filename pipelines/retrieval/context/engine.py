from __future__ import annotations

from ..models import EvidenceChunk, RetrievalHit
from .assembler import assemble_context
from .selector import SelectionPolicy, select_evidence


class EvidenceContextEngine:
    def __init__(self, policy: SelectionPolicy | None = None) -> None:
        self.policy = policy or SelectionPolicy()

    def build(
        self,
        query: str,
        candidates: list[tuple[EvidenceChunk, RetrievalHit]],
    ):
        selected, exclusions = select_evidence(candidates, self.policy)
        return assemble_context(query, selected, exclusions)
