from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceContextPacket:
    query: str
    evidence: list[dict[str, Any]]
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    instructions: tuple[str, ...] = (
        "Use only supplied evidence IDs for source claims.",
        "Do not treat the latest revision as governing unless governing status is established by evidence.",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "evidence": self.evidence,
            "exclusions": self.exclusions,
            "warnings": self.warnings,
            "instructions": list(self.instructions),
        }
