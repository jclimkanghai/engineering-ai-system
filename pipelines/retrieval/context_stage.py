from __future__ import annotations

from typing import Any

from .context.engine import EvidenceContextEngine
from .context.selector import SelectionPolicy
from .models import EvidenceChunk, RetrievalHit


def build_selected_context(
    query: str,
    candidates: list[tuple[EvidenceChunk, RetrievalHit]],
    *,
    policy: SelectionPolicy | None = None,
) -> dict[str, Any]:
    return EvidenceContextEngine(policy).build(query, candidates).to_dict()
