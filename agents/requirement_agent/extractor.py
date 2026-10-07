"""Requirement extraction engine for Engineering Document AI.

This module provides deterministic extraction of candidate engineering
requirements from structured document text.

The extractor does not determine final contractual meaning. It identifies
candidate requirements and preserves the source evidence so that downstream
review agents can classify, verify and interpret them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha256

# ---------------------------------------------------------------------------
# Requirement language
# ---------------------------------------------------------------------------

MANDATORY_PATTERNS = (
    r"\bshall\b",
    r"\bmust\b",
    r"\bis required to\b",
    r"\bare required to\b",
)

CONDITIONAL_PATTERNS = (
    r"\bwhere required\b",
    r"\bwhere applicable\b",
    r"\bif required\b",
    r"\bif applicable\b",
    r"\bsubject to\b",
)

OPTIONAL_PATTERNS = (
    r"\bmay\b",
    r"\bcan\b",
    r"\boptional\b",
)

INFORMATIONAL_PATTERNS = (
    r"\bfor information\b",
    r"\bfor reference\b",
    r"\bnote that\b",
)

ACTION_VERBS = (
    "design",
    "provide",
    "submit",
    "construct",
    "install",
    "test",
    "inspect",
    "verify",
    "demonstrate",
    "comply",
    "maintain",
    "prepare",
    "calculate",
    "coordinate",
    "obtain",
    "complete",
    "supply",
    "perform",
    "carry out",
    "ensure",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceLocation:
    """Location of extracted evidence in the source document."""

    document_id: str
    document_number: str | None = None
    revision: str | None = None
    page: int | None = None
    section: str | None = None
    paragraph: int | None = None


@dataclass(frozen=True)
class RequirementCandidate:
    """A candidate requirement extracted from source text."""

    requirement_id: str
    requirement_text: str
    source_text: str
    requirement_type: str
    obligation_type: str
    action: str | None
    source_location: SourceLocation
    evidence_type: str = "source_fact"
    confidence: str = "medium"
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    uncertainties: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalise_text(text: str) -> str:
    """Normalise whitespace without materially changing source wording."""

    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    """Split text into candidate sentences while preserving sentence content."""

    normalised = normalise_text(text)

    if not normalised:
        return []

    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", normalised)

    return [sentence.strip() for sentence in sentences if sentence.strip()]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _contains_pattern(text: str, patterns: Iterable[str]) -> bool:
    """Return True when any supplied regular-expression pattern matches."""

    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_requirement_type(text: str) -> str:
    """Classify the requirement as mandatory, conditional, optional or informational."""

    if _contains_pattern(text, INFORMATIONAL_PATTERNS):
        return "informational"

    if _contains_pattern(text, CONDITIONAL_PATTERNS):
        return "conditional"

    if _contains_pattern(text, MANDATORY_PATTERNS):
        return "mandatory"

    if _contains_pattern(text, OPTIONAL_PATTERNS):
        return "optional"

    return "informational"


def classify_obligation_type(text: str) -> str:
    """Classify the broad obligation represented by the requirement."""

    lowered = text.lower()

    if any(
        verb in lowered
        for verb in (
            "submit",
            "provide",
            "prepare",
            "supply",
            "obtain",
        )
    ):
        return "deliverable"

    if any(
        verb in lowered
        for verb in (
            "design",
            "calculate",
            "verify",
            "demonstrate",
        )
    ):
        return "design"

    if any(
        verb in lowered
        for verb in (
            "test",
            "inspect",
        )
    ):
        return "testing"

    if any(
        verb in lowered
        for verb in (
            "construct",
            "install",
            "perform",
            "carry out",
        )
    ):
        return "construction"

    if "comply" in lowered:
        return "compliance"

    return "other"


def extract_action(text: str) -> str | None:
    """Extract the first recognised engineering action."""

    lowered = text.lower()

    matches = [
        (match.start(), action)
        for action in ACTION_VERBS
        for match in re.finditer(rf"\b{re.escape(action)}\b", lowered)
    ]
    if matches:
        return min(matches)[1]

    return None


# ---------------------------------------------------------------------------
# Candidate detection
# ---------------------------------------------------------------------------


def is_requirement_candidate(text: str) -> bool:
    """Determine whether a sentence is likely to contain a requirement."""

    normalised = normalise_text(text)

    if not normalised:
        return False

    if _contains_pattern(
        normalised,
        MANDATORY_PATTERNS + CONDITIONAL_PATTERNS + OPTIONAL_PATTERNS,
    ):
        return True

    lowered = normalised.lower()

    return any(
        re.search(rf"\b{re.escape(action)}\b", lowered) for action in ACTION_VERBS
    )


# ---------------------------------------------------------------------------
# Requirement ID
# ---------------------------------------------------------------------------


def build_requirement_id(
    source_location: SourceLocation,
    requirement_text: str,
) -> str:
    """Build a deterministic requirement identifier."""

    identity = "|".join(
        (
            source_location.document_id,
            source_location.document_number or "",
            source_location.revision or "",
            str(source_location.page or ""),
            source_location.section or "",
            str(source_location.paragraph or ""),
            normalise_text(requirement_text),
        )
    )

    digest = sha256(identity.encode("utf-8")).hexdigest()[:12]

    return f"REQ-{digest}"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_requirements(
    text: str,
    source_location: SourceLocation,
) -> list[RequirementCandidate]:
    """Extract candidate requirements from document text.

    The function deliberately preserves the original sentence as source
    evidence. It does not invent missing parameters, responsibilities,
    standards or acceptance criteria.
    """

    candidates: list[RequirementCandidate] = []

    for paragraph_number, sentence in enumerate(
        split_sentences(text),
        start=1,
    ):
        if not is_requirement_candidate(sentence):
            continue

        requirement_text = normalise_text(sentence)

        requirement_id = build_requirement_id(
            source_location=SourceLocation(
                document_id=source_location.document_id,
                document_number=source_location.document_number,
                revision=source_location.revision,
                page=source_location.page,
                section=source_location.section,
                paragraph=paragraph_number,
            ),
            requirement_text=requirement_text,
        )

        location = SourceLocation(
            document_id=source_location.document_id,
            document_number=source_location.document_number,
            revision=source_location.revision,
            page=source_location.page,
            section=source_location.section,
            paragraph=paragraph_number,
        )

        candidates.append(
            RequirementCandidate(
                requirement_id=requirement_id,
                requirement_text=requirement_text,
                source_text=sentence,
                requirement_type=classify_requirement_type(sentence),
                obligation_type=classify_obligation_type(sentence),
                action=extract_action(sentence),
                source_location=location,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def extract_requirements_from_paragraphs(
    paragraphs: Iterable[str],
    source_location: SourceLocation,
) -> list[RequirementCandidate]:
    """Extract requirements from multiple document paragraphs."""

    candidates: list[RequirementCandidate] = []

    for paragraph_number, paragraph in enumerate(paragraphs, start=1):
        paragraph_candidates = extract_requirements(
            text=paragraph,
            source_location=SourceLocation(
                document_id=source_location.document_id,
                document_number=source_location.document_number,
                revision=source_location.revision,
                page=source_location.page,
                section=source_location.section,
                paragraph=paragraph_number,
            ),
        )

        candidates.extend(paragraph_candidates)

    return candidates
