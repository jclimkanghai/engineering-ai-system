"""Structured requirement model and schema adapter.

This module converts deterministic extraction results into a structured
engineering requirement representation.

Design principle:
    Extract evidence first. Interpret second.

No technical, contractual, regulatory, or commercial information should be
invented by this layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_REQUIREMENT_TYPES = {
    "mandatory",
    "conditional",
    "optional",
    "informational",
}

VALID_REQUIREMENT_CATEGORIES = {
    "technical",
    "contractual",
    "commercial",
    "programme",
    "regulatory",
    "hse",
    "quality",
    "environmental",
    "construction",
    "procurement",
    "documentation",
    "interface",
    "testing",
    "commissioning",
    "operation",
    "maintenance",
    "other",
}

VALID_OBLIGATION_TYPES = {
    "design",
    "supply",
    "procure",
    "manufacture",
    "fabricate",
    "transport",
    "install",
    "construct",
    "test",
    "inspect",
    "commission",
    "operate",
    "maintain",
    "submit",
    "obtain_approval",
    "coordinate",
    "provide",
    "verify",
    "demonstrate",
    "report",
    "other",
}

VALID_SCOPE_STATUSES = {
    "in_scope",
    "out_of_scope",
    "by_others",
    "interface",
    "optional",
    "unclear",
}

VALID_EVIDENCE_STATUSES = {
    "confirmed",
    "partially_confirmed",
    "not_confirmed",
    "conflicting",
    "unavailable",
    "not_applicable",
    "unknown",
}

VALID_EVIDENCE_QUALITIES = {"high", "medium", "low", "insufficient", "unknown"}

VALID_COMPLIANCE_STATUSES = {
    "compliant",
    "partially_compliant",
    "non_compliant",
    "pending",
    "not_assessed",
    "not_applicable",
    "unable_to_verify",
    "conflicting_information",
}

VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
VALID_CONFIDENCE_LEVELS = {"low", "medium", "high"}
VALID_REVIEW_STATUSES = {
    "extracted",
    "reviewed",
    "verified",
    "requires_human_review",
    "rejected",
}
VALID_RESPONSIBILITY_BASES = {
    "explicit",
    "implied",
    "inferred",
    "unclear",
    "unknown",
}
VALID_EXTRACTION_METHODS = {
    "text",
    "table",
    "drawing",
    "OCR",
    "layout",
    "manual",
    "other",
}

# The deterministic extractor uses these broad classes before this adapter
# maps them to the schema's action-oriented obligation vocabulary.
EXTRACTOR_OBLIGATION_TYPES = {
    "deliverable",
    "construction",
    "testing",
    "compliance",
}

VALID_EVIDENCE_TYPES = {
    "source_fact",
    "interpretation",
    "assumption",
    "engineering_judgement",
    "recommendation",
    "unknown",
}


# ---------------------------------------------------------------------------
# Source location
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequirementSource:
    """Traceable source location for a requirement."""

    document_id: str
    document_number: str | None = None
    document_title: str | None = None
    revision: str | None = None
    revision_date: str | None = None

    page: int | None = None
    section: str | None = None
    subsection: str | None = None
    paragraph: str | None = None
    table: str | None = None
    figure: str | None = None
    drawing_reference: str | None = None
    source_location: str | None = None


# ---------------------------------------------------------------------------
# Structured requirement
# ---------------------------------------------------------------------------


@dataclass
class RequirementRecord:
    """Structured engineering requirement.

    The fields intentionally mirror the logical structure defined in
    schemas/requirement.schema.yaml.

    Fields that cannot be supported by source evidence remain None, empty,
    or explicitly unknown.
    """

    # Identity
    requirement_id: str
    title: str
    requirement_text: str
    source_text: str

    # Classification
    requirement_type: str
    category: str = "other"
    discipline: tuple[str, ...] = ()
    scope_status: str = "unclear"

    # Obligation
    obligation_type: str = "other"
    action: str | None = None
    deliverable: str | None = None
    acceptance_criteria: tuple[str, ...] = ()

    # Responsibility
    responsible_party: str | None = None
    responsible_role: str | None = None
    supporting_parties: tuple[str, ...] = ()
    responsibility_basis: str | None = None

    # Technical criteria
    parameters: list[dict[str, Any]] = field(default_factory=list)
    codes_and_standards: tuple[str, ...] = ()
    design_life: str | None = None
    performance_criteria: tuple[str, ...] = ()

    # Applicability
    applicable_to: tuple[str, ...] = ()
    applicability_conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()

    # Provenance
    source: RequirementSource | None = None
    extraction_method: str = "text"

    # Revision control
    current: bool = True
    superseded: bool = False
    modified: bool = False
    deleted: bool = False
    clarified: bool = False
    unchanged: bool = False
    disputed: bool = False
    supersedes_requirement_id: tuple[str, ...] = ()
    superseded_by_requirement_id: tuple[str, ...] = ()
    change_reason: str | None = None

    # Evidence
    evidence_status: str = "unknown"
    evidence_references: tuple[str, ...] = ()
    evidence_quality: str = "medium"

    # Interpretation
    evidence_type: str = "source_fact"
    interpretation: str | None = None
    assumptions: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    # Compliance
    compliance_status: str = "not_assessed"
    compliance_basis: str | None = None
    outstanding_actions: tuple[str, ...] = ()

    # Risk
    risk_level: str = "low"
    risk_dimensions: tuple[str, ...] = ()
    risk_description: str | None = None
    consequence: str | None = None
    mitigation: tuple[str, ...] = ()

    # Review
    confidence: str = "medium"
    review_status: str = "requires_human_review"
    reviewer: str | None = None
    review_notes: str | None = None

    def __post_init__(self) -> None:
        """Validate controlled vocabulary values."""

        if self.requirement_type not in VALID_REQUIREMENT_TYPES:
            raise ValueError(f"Invalid requirement_type: {self.requirement_type}")

        if self.obligation_type not in VALID_OBLIGATION_TYPES:
            raise ValueError(f"Invalid obligation_type: {self.obligation_type}")

        if self.evidence_type not in VALID_EVIDENCE_TYPES:
            raise ValueError(f"Invalid evidence_type: {self.evidence_type}")

        controlled_values = {
            "category": VALID_REQUIREMENT_CATEGORIES,
            "scope_status": VALID_SCOPE_STATUSES,
            "responsibility_basis": VALID_RESPONSIBILITY_BASES,
            "extraction_method": VALID_EXTRACTION_METHODS,
            "evidence_status": VALID_EVIDENCE_STATUSES,
            "evidence_quality": VALID_EVIDENCE_QUALITIES,
            "compliance_status": VALID_COMPLIANCE_STATUSES,
            "risk_level": VALID_RISK_LEVELS,
            "confidence": VALID_CONFIDENCE_LEVELS,
            "review_status": VALID_REVIEW_STATUSES,
        }
        for field_name, allowed in controlled_values.items():
            value = getattr(self, field_name)
            if value is not None and value not in allowed:
                raise ValueError(f"Invalid {field_name}: {value}")

    def to_dict(self) -> dict[str, Any]:
        """Convert the requirement into a JSON/YAML-friendly dictionary."""

        errors = validate_requirement(self)
        if errors:
            raise ValueError(
                "Cannot serialize invalid requirement: " + "; ".join(errors)
            )

        source = self.source
        status = next(
            (
                name
                for name in (
                    "superseded",
                    "modified",
                    "deleted",
                    "clarified",
                    "unchanged",
                    "disputed",
                    "current",
                )
                if getattr(self, name)
            ),
            "unknown",
        )

        result: dict[str, Any] = {
            "identity": {
                "requirement_id": self.requirement_id,
                "title": self.title,
                "requirement_text": self.requirement_text,
                "source_text": self.source_text,
            },
            "classification": {
                "requirement_type": self.requirement_type,
                "requirement_category": self.category,
                "discipline": list(self.discipline),
                "scope_status": self.scope_status,
            },
            "obligation": {
                "obligation_type": self.obligation_type,
                "action": self.action,
                "deliverable": self.deliverable,
                "acceptance_criteria": list(self.acceptance_criteria),
            },
            "responsibility": {
                "responsible_party": self.responsible_party,
                "responsible_role": self.responsible_role,
                "supporting_parties": list(self.supporting_parties),
                "responsibility_basis": self.responsibility_basis,
            },
            "technical_criteria": {
                "parameters": self.parameters,
                "codes_and_standards": list(self.codes_and_standards),
                "design_life": self.design_life,
                "performance_criteria": list(self.performance_criteria),
            },
            "applicability": {
                "applicable_to": list(self.applicable_to),
                "applicability_conditions": list(self.applicability_conditions),
                "exclusions": list(self.exclusions),
            },
            "provenance": {
                "source_document_id": source.document_id if source else None,
                "source_document_number": source.document_number if source else None,
                "source_revision": source.revision if source else None,
                "source_date": source.revision_date if source else None,
                "section": source.section if source else None,
                "subsection": source.subsection if source else None,
                "page": source.page if source else None,
                "paragraph": (
                    str(source.paragraph)
                    if source and source.paragraph is not None
                    else None
                ),
                "table": source.table if source else None,
                "figure": source.figure if source else None,
                "drawing_reference": source.drawing_reference if source else None,
                "source_location": source.source_location if source else None,
                "extraction_method": self.extraction_method,
            },
            "revision_control": {
                "status": status,
                "supersedes_requirement_id": list(self.supersedes_requirement_id),
                "superseded_by_requirement_id": list(self.superseded_by_requirement_id),
                "change_reason": self.change_reason,
            },
            "evidence": {
                "evidence_status": self.evidence_status,
                "evidence_references": list(self.evidence_references),
                "evidence_quality": self.evidence_quality,
            },
            "interpretation": {
                "evidence_type": self.evidence_type,
                "interpretation": self.interpretation,
                "assumptions": list(self.assumptions),
                "uncertainties": list(self.uncertainties),
                "conflicts": list(self.conflicts),
            },
            "compliance": {
                "compliance_status": self.compliance_status,
                "compliance_basis": self.compliance_basis,
                "outstanding_actions": list(self.outstanding_actions),
            },
            "risk": {
                "risk_level": self.risk_level,
                "risk_dimensions": list(self.risk_dimensions),
                "risk_description": self.risk_description,
                "consequence": self.consequence,
                "mitigation": list(self.mitigation),
            },
            "review": {
                "confidence": self.confidence,
                "review_status": self.review_status,
                "reviewer": self.reviewer,
                "review_notes": self.review_notes,
            },
        }

        def omit_unset(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: omit_unset(item)
                    for key, item in value.items()
                    if item is not None
                }
            return value

        return omit_unset(result)


# ---------------------------------------------------------------------------
# Requirement ID
# ---------------------------------------------------------------------------


def build_requirement_id(
    source: RequirementSource,
    requirement_text: str,
) -> str:
    """Generate a deterministic requirement identifier.

    The identifier is based on source identity and source text.

    Changing the document revision, source location, or requirement text
    therefore produces a different identifier.
    """

    identity = "|".join(
        [
            source.document_id,
            source.document_number or "",
            source.revision or "",
            str(source.page or ""),
            source.section or "",
            str(source.paragraph or ""),
            requirement_text.strip(),
        ]
    )

    digest = sha256(identity.encode("utf-8")).hexdigest()[:16]

    return f"REQ-{digest}"


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


def build_requirement_title(
    requirement_text: str,
    action: str | None = None,
) -> str:
    """Generate a conservative human-readable requirement title.

    This function does not infer technical meaning. It only uses the source
    wording and, where available, the extracted action.
    """

    text = " ".join(requirement_text.split())

    if not text:
        return "Untitled Requirement"

    if action:
        words = text.rstrip(".").split()

        # Keep the title short without changing the source meaning.
        if len(words) > 12:
            return " ".join(words[:12]) + "..."

    if len(text) <= 120:
        return text.rstrip(".")

    return text[:117].rstrip() + "..."


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def requirement_from_extraction(
    extracted: Any,
    *,
    source: RequirementSource | None = None,
) -> RequirementRecord:
    """Convert an extractor result into a RequirementRecord.

    The adapter reads only attributes already present in the extraction
    result. Missing information remains unset.

    This is intentionally conservative.
    """

    requirement_text = str(getattr(extracted, "requirement_text", "")).strip()

    source_text_value = getattr(extracted, "source_text", requirement_text)
    source_text = str(source_text_value or requirement_text).strip()

    if not requirement_text:
        raise ValueError("Extracted requirement_text cannot be empty.")

    requirement_type = getattr(
        extracted,
        "requirement_type",
        "informational",
    )

    obligation_type = getattr(
        extracted,
        "obligation_type",
        "other",
    )

    evidence_type = getattr(
        extracted,
        "evidence_type",
        "source_fact",
    )

    action = getattr(extracted, "action", None)

    def as_string_tuple(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(str(item) for item in value)

    def schema_obligation_type(value: str, extracted_action: str | None) -> str:
        if value in VALID_OBLIGATION_TYPES:
            return value

        normalised_action = (extracted_action or "").strip().lower().replace(" ", "_")
        if normalised_action in VALID_OBLIGATION_TYPES:
            return normalised_action

        # Do not invent a specific verb from the broad extractor class.
        return "other"

    if requirement_type not in VALID_REQUIREMENT_TYPES:
        raise ValueError(f"Invalid extracted requirement_type: {requirement_type}")

    if obligation_type not in (VALID_OBLIGATION_TYPES | EXTRACTOR_OBLIGATION_TYPES):
        raise ValueError(f"Invalid extracted obligation_type: {obligation_type}")

    if evidence_type not in VALID_EVIDENCE_TYPES:
        raise ValueError(f"Invalid extracted evidence_type: {evidence_type}")

    if source is None:
        extracted_source = getattr(extracted, "source_location", None)

        if extracted_source is not None:
            source = RequirementSource(
                document_id=getattr(
                    extracted_source,
                    "document_id",
                    "",
                ),
                document_number=getattr(
                    extracted_source,
                    "document_number",
                    None,
                ),
                revision=getattr(
                    extracted_source,
                    "revision",
                    None,
                ),
                page=getattr(
                    extracted_source,
                    "page",
                    None,
                ),
                paragraph=getattr(extracted_source, "paragraph", None),
                section=getattr(
                    extracted_source,
                    "section",
                    None,
                ),
            )

    if source is None or not source.document_id:
        raise ValueError(
            "A valid source document_id is required for a structured requirement."
        )

    requirement_id = getattr(extracted, "requirement_id", None)

    if not requirement_id:
        requirement_id = build_requirement_id(
            source=source,
            requirement_text=requirement_text,
        )

    assumptions = as_string_tuple(getattr(extracted, "assumptions", ()))
    uncertainties = as_string_tuple(getattr(extracted, "uncertainties", ()))

    return RequirementRecord(
        requirement_id=requirement_id,
        title=build_requirement_title(
            requirement_text,
            action=action,
        ),
        requirement_text=requirement_text,
        source_text=source_text,
        requirement_type=requirement_type,
        obligation_type=schema_obligation_type(obligation_type, action),
        action=action,
        category=getattr(
            extracted,
            "requirement_category",
            getattr(extracted, "category", "other"),
        ),
        discipline=as_string_tuple(getattr(extracted, "discipline", ())),
        scope_status=getattr(extracted, "scope_status", "unclear"),
        deliverable=getattr(extracted, "deliverable", None),
        acceptance_criteria=as_string_tuple(
            getattr(extracted, "acceptance_criteria", ())
        ),
        responsible_party=getattr(extracted, "responsible_party", None),
        responsible_role=getattr(
            extracted,
            "responsible_role",
            getattr(extracted, "role", None),
        ),
        supporting_parties=as_string_tuple(
            getattr(extracted, "supporting_parties", ())
        ),
        responsibility_basis=getattr(extracted, "responsibility_basis", None),
        parameters=list(getattr(extracted, "parameters", ()) or ()),
        codes_and_standards=as_string_tuple(
            getattr(extracted, "codes_and_standards", ())
        ),
        design_life=getattr(extracted, "design_life", None),
        performance_criteria=as_string_tuple(
            getattr(extracted, "performance_criteria", ())
        ),
        applicable_to=as_string_tuple(getattr(extracted, "applicable_to", ())),
        applicability_conditions=as_string_tuple(
            getattr(extracted, "applicability_conditions", ())
        ),
        exclusions=as_string_tuple(getattr(extracted, "exclusions", ())),
        source=source,
        evidence_type=evidence_type,
        assumptions=assumptions,
        uncertainties=uncertainties,
        evidence_status=getattr(extracted, "evidence_status", "unknown"),
        evidence_references=as_string_tuple(
            getattr(extracted, "evidence_references", ())
        ),
        evidence_quality=getattr(extracted, "evidence_quality", "medium"),
        interpretation=getattr(extracted, "interpretation", None),
        conflicts=as_string_tuple(getattr(extracted, "conflicts", ())),
        compliance_status=getattr(extracted, "compliance_status", "not_assessed"),
        compliance_basis=getattr(extracted, "compliance_basis", None),
        outstanding_actions=as_string_tuple(
            getattr(extracted, "outstanding_actions", ())
        ),
        risk_level=getattr(extracted, "risk_level", "low"),
        risk_dimensions=as_string_tuple(getattr(extracted, "risk_dimensions", ())),
        risk_description=getattr(extracted, "risk_description", None),
        consequence=getattr(extracted, "consequence", None),
        mitigation=as_string_tuple(getattr(extracted, "mitigation", ())),
        confidence=getattr(
            extracted,
            "confidence",
            "medium",
        ),
        review_status=getattr(
            extracted,
            "review_status",
            "requires_human_review",
        ),
        reviewer=getattr(extracted, "reviewer", None),
        review_notes=getattr(extracted, "review_notes", None),
    )


# ---------------------------------------------------------------------------
# Batch conversion
# ---------------------------------------------------------------------------


def requirements_from_extractions(
    extracted_requirements: Iterable[Any],
    *,
    source: RequirementSource | None = None,
) -> list[RequirementRecord]:
    """Convert multiple extraction results into structured requirements."""

    return [
        requirement_from_extraction(
            extracted,
            source=source,
        )
        for extracted in extracted_requirements
    ]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_requirement(record: RequirementRecord) -> list[str]:
    """Return validation errors without modifying the requirement."""

    errors: list[str] = []

    if not record.requirement_id:
        errors.append("requirement_id is required.")

    if not record.title:
        errors.append("title is required.")

    if not record.requirement_text:
        errors.append("requirement_text is required.")

    if not record.source_text:
        errors.append("source_text is required.")

    if record.source is None:
        errors.append("source is required.")

    elif not record.source.document_id:
        errors.append("source.document_id is required.")

    if record.requirement_type not in VALID_REQUIREMENT_TYPES:
        errors.append(f"Invalid requirement_type: {record.requirement_type}")

    if record.obligation_type not in VALID_OBLIGATION_TYPES:
        errors.append(f"Invalid obligation_type: {record.obligation_type}")

    if record.evidence_type not in VALID_EVIDENCE_TYPES:
        errors.append(f"Invalid evidence_type: {record.evidence_type}")

    controlled_values = {
        "category": VALID_REQUIREMENT_CATEGORIES,
        "scope_status": VALID_SCOPE_STATUSES,
        "responsibility_basis": VALID_RESPONSIBILITY_BASES,
        "extraction_method": VALID_EXTRACTION_METHODS,
        "evidence_status": VALID_EVIDENCE_STATUSES,
        "evidence_quality": VALID_EVIDENCE_QUALITIES,
        "compliance_status": VALID_COMPLIANCE_STATUSES,
        "risk_level": VALID_RISK_LEVELS,
        "confidence": VALID_CONFIDENCE_LEVELS,
        "review_status": VALID_REVIEW_STATUSES,
    }
    for field_name, allowed in controlled_values.items():
        value = getattr(record, field_name)
        if value is not None and value not in allowed:
            errors.append(f"Invalid {field_name}: {value}")

    list_fields = (
        "discipline",
        "acceptance_criteria",
        "supporting_parties",
        "codes_and_standards",
        "performance_criteria",
        "applicable_to",
        "applicability_conditions",
        "exclusions",
        "supersedes_requirement_id",
        "superseded_by_requirement_id",
        "evidence_references",
        "assumptions",
        "uncertainties",
        "conflicts",
        "outstanding_actions",
        "risk_dimensions",
        "mitigation",
    )
    for field_name in list_fields:
        value = getattr(record, field_name)
        if not isinstance(value, tuple) or any(
            not isinstance(item, str) for item in value
        ):
            errors.append(f"{field_name} must be a sequence of strings.")

    if not isinstance(record.parameters, list) or any(
        not isinstance(parameter, dict)
        or not isinstance(parameter.get("name"), str)
        or not isinstance(parameter.get("value"), str)
        or any(
            key in parameter and not isinstance(parameter[key], str)
            for key in ("unit", "condition", "tolerance")
        )
        for parameter in record.parameters
    ):
        errors.append(
            "parameters must be a list of mappings with string name and value."
        )

    return errors


def is_valid_requirement(record: RequirementRecord) -> bool:
    """Return True when the structured requirement passes validation."""

    return not validate_requirement(record)
