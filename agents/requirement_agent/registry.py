"""Process-local registry for structured engineering requirements."""

from __future__ import annotations

from collections.abc import Iterable

from .requirement_model import RequirementRecord, validate_requirement


class RequirementRegistry:
    """Store validated requirements by stable ID for the current process.

    Registering the same record more than once is idempotent. Reusing an ID
    for different content is rejected so conflicting source records are not
    silently overwritten.
    """

    def __init__(self) -> None:
        self._records: dict[str, RequirementRecord] = {}

    def register(self, record: RequirementRecord) -> RequirementRecord:
        """Validate and add one requirement, or return its existing copy."""

        self.register_many((record,))
        return self._records[record.requirement_id]

    def register_many(
        self,
        records: Iterable[RequirementRecord],
    ) -> list[RequirementRecord]:
        """Validate and register a batch atomically.

        No records are added if validation fails or if a batch contains two
        different requirements with the same ID.
        """

        pending: dict[str, RequirementRecord] = {}

        for record in records:
            errors = validate_requirement(record)
            if errors:
                raise ValueError(
                    f"Invalid requirement {record.requirement_id!r}: "
                    + "; ".join(errors)
                )

            requirement_id = record.requirement_id
            existing = pending.get(
                requirement_id,
                self._records.get(requirement_id),
            )
            if existing is not None and existing != record:
                raise ValueError(
                    f"Conflicting requirement already registered: {requirement_id}"
                )

            pending[requirement_id] = record

        self._records.update(pending)
        return list(pending.values())

    def get(self, requirement_id: str) -> RequirementRecord | None:
        """Return a requirement by ID, or None when it is not registered."""

        return self._records.get(requirement_id)

    def all(self) -> list[RequirementRecord]:
        """Return requirements in registration order."""

        return list(self._records.values())

    def for_document(self, document_id: str) -> list[RequirementRecord]:
        """Return requirements whose provenance names the given document."""

        return [
            record
            for record in self._records.values()
            if record.source is not None and record.source.document_id == document_id
        ]

    def __len__(self) -> int:
        return len(self._records)
