from __future__ import annotations

from ..models import EvidenceChunk
from .models import EvidenceContextPacket


def assemble_context(
    query: str,
    selected: list[EvidenceChunk],
    exclusions: list[dict[str, str]],
) -> EvidenceContextPacket:
    warnings = (
        [] if selected else ["No evidence satisfied the context selection policy."]
    )
    return EvidenceContextPacket(
        query=query,
        evidence=[chunk.to_dict() for chunk in selected],
        exclusions=exclusions,
        warnings=warnings,
    )
