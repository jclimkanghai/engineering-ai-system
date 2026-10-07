from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .models import (
    ControlRelationType,
    DocumentControlRecord,
    DocumentControlRelation,
)


class DocumentControlGraph:
    """In-memory graph of explicitly supplied document-control evidence."""

    def __init__(self, records: Iterable[DocumentControlRecord] = ()) -> None:
        self.records: dict[str, DocumentControlRecord] = {}
        self.relations: list[DocumentControlRelation] = []
        self._out: dict[str, list[DocumentControlRelation]] = defaultdict(list)
        self._in: dict[str, list[DocumentControlRelation]] = defaultdict(list)
        for record in records:
            self.add_record(record)

    def add_record(self, record: DocumentControlRecord) -> None:
        self.records[record.document_id] = record

    def add_relation(self, relation: DocumentControlRelation) -> None:
        if relation.source_document_id not in self.records:
            raise KeyError(f"Unknown source document: {relation.source_document_id}")
        if relation.target_document_id not in self.records:
            raise KeyError(f"Unknown target document: {relation.target_document_id}")
        self.relations.append(relation)
        self._out[relation.source_document_id].append(relation)
        self._in[relation.target_document_id].append(relation)

    def get(self, document_id: str) -> DocumentControlRecord | None:
        return self.records.get(document_id)

    def outgoing(
        self,
        document_id: str,
        relation_type: ControlRelationType | None = None,
    ) -> list[DocumentControlRelation]:
        relations = self._out.get(document_id, [])
        if relation_type is None:
            return list(relations)
        return [item for item in relations if item.relation_type == relation_type]

    def incoming(
        self,
        document_id: str,
        relation_type: ControlRelationType | None = None,
    ) -> list[DocumentControlRelation]:
        relations = self._in.get(document_id, [])
        if relation_type is None:
            return list(relations)
        return [item for item in relations if item.relation_type == relation_type]
