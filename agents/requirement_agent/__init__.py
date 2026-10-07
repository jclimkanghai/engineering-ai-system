"""Public API for deterministic requirement extraction and structuring."""

from __future__ import annotations

from collections.abc import Iterable

from .extractor import (
    RequirementCandidate,
    SourceLocation,
    extract_requirements,
    extract_requirements_from_paragraphs,
)
from .registry import RequirementRegistry
from .requirement_model import (
    RequirementRecord,
    RequirementSource,
    build_requirement_id,
    build_requirement_title,
    is_valid_requirement,
    requirement_from_extraction,
    requirements_from_extractions,
    validate_requirement,
)


def extract_structured_requirements(
    text: str,
    source_location: SourceLocation,
) -> list[RequirementRecord]:
    """Extract requirement candidates and convert them to structured records."""

    candidates = extract_requirements(text, source_location)
    return requirements_from_extractions(candidates)


def extract_structured_requirements_from_paragraphs(
    paragraphs: Iterable[str],
    source_location: SourceLocation,
) -> list[RequirementRecord]:
    """Extract and structure requirement candidates from document paragraphs."""

    candidates = extract_requirements_from_paragraphs(paragraphs, source_location)
    return requirements_from_extractions(candidates)


__all__ = [
    "RequirementCandidate",
    "RequirementRecord",
    "RequirementRegistry",
    "RequirementSource",
    "SourceLocation",
    "build_requirement_id",
    "build_requirement_title",
    "extract_requirements",
    "extract_requirements_from_paragraphs",
    "extract_structured_requirements",
    "extract_structured_requirements_from_paragraphs",
    "is_valid_requirement",
    "requirement_from_extraction",
    "requirements_from_extractions",
    "validate_requirement",
]
