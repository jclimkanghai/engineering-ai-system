from __future__ import annotations

from dataclasses import dataclass

from .graph import DocumentControlGraph
from .models import ControlAssessment, ControlRelationType


@dataclass(frozen=True)
class RevisionPolicy:
    latest_is_not_governing: bool = True
    preserve_superseded_when_conflict: bool = True


class RevisionIntelligence:
    """Assess only explicit control metadata and relations."""

    def __init__(
        self,
        graph: DocumentControlGraph,
        policy: RevisionPolicy | None = None,
    ) -> None:
        self.graph = graph
        self.policy = policy or RevisionPolicy()

    def assess(self, document_id: str) -> ControlAssessment:
        record = self.graph.get(document_id)
        if record is None:
            return ControlAssessment(
                document_id=document_id,
                governing_status="unknown",
                confidence=0.0,
                warnings=("Document is not present in the control graph.",),
                conflict=True,
            )

        superseded = bool(
            self.graph.incoming(
                document_id,
                ControlRelationType.SUPERSEDES,
            )
            or self.graph.incoming(
                document_id,
                ControlRelationType.REPLACES,
            )
        )
        conflict = bool(
            self.graph.outgoing(document_id, ControlRelationType.SUPERSEDES)
            and record.governing_status in {"governing", "current", "approved"}
        )
        warnings: list[str] = []
        if record.governing_status == "unknown":
            warnings.append("Governing status is not established by the record.")
        if superseded:
            warnings.append(
                "An explicit control relation marks the document as superseded."
            )
        if conflict:
            warnings.append(
                "Control relation and governing status require human review."
            )

        confidence = 1.0 if record.governing_status != "unknown" else 0.0
        return ControlAssessment(
            document_id=document_id,
            governing_status=record.governing_status,
            confidence=confidence,
            warnings=tuple(warnings),
            active_revision=record.revision,
            superseded=superseded,
            conflict=conflict,
        )

    def assess_many(self, document_ids: list[str]) -> dict[str, ControlAssessment]:
        return {document_id: self.assess(document_id) for document_id in document_ids}
