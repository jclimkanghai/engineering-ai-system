"""Tests for the deterministic requirement extraction engine."""

from pipelines.requirement_agent.extractor import (
    SourceLocation,
    build_requirement_id,
    classify_obligation_type,
    classify_requirement_type,
    extract_action,
    extract_requirements,
    extract_requirements_from_paragraphs,
    is_requirement_candidate,
    normalise_text,
    split_sentences,
)


def make_source_location() -> SourceLocation:
    """Create a standard test source location."""

    return SourceLocation(
        document_id="DOC-TEST-001",
        document_number="RFP-001",
        revision="0",
        page=12,
        section="4.2 Piling",
    )


def test_normalise_text() -> None:
    text = "  The   Contractor   shall   provide piles.  "

    assert normalise_text(text) == "The Contractor shall provide piles."


def test_split_sentences() -> None:
    text = (
        "The Contractor shall provide piles. "
        "The Contractor must submit calculations. "
        "The Works shall be completed within 30 months."
    )

    sentences = split_sentences(text)

    assert len(sentences) == 3
    assert sentences[0] == "The Contractor shall provide piles."
    assert sentences[1] == "The Contractor must submit calculations."


def test_mandatory_requirement_classification() -> None:
    assert (
        classify_requirement_type("The Contractor shall provide steel piles.")
        == "mandatory"
    )


def test_conditional_requirement_classification() -> None:
    assert (
        classify_requirement_type(
            "The Contractor shall provide testing where required."
        )
        == "conditional"
    )


def test_optional_requirement_classification() -> None:
    assert (
        classify_requirement_type("The Contractor may propose an alternative system.")
        == "optional"
    )


def test_informational_classification() -> None:
    assert (
        classify_requirement_type("For information, the existing jetty is 200 m long.")
        == "informational"
    )


def test_obligation_type_deliverable() -> None:
    assert (
        classify_obligation_type("The Contractor shall submit design calculations.")
        == "deliverable"
    )


def test_obligation_type_design() -> None:
    assert (
        classify_obligation_type("The Contractor shall design the jetty structure.")
        == "design"
    )


def test_obligation_type_construction() -> None:
    assert (
        classify_obligation_type("The Contractor shall install the steel piles.")
        == "construction"
    )


def test_obligation_type_testing() -> None:
    assert (
        classify_obligation_type("The Contractor shall perform pile testing.")
        == "testing"
    )


def test_obligation_type_compliance() -> None:
    assert (
        classify_obligation_type(
            "The Contractor shall comply with the applicable standards."
        )
        == "compliance"
    )


def test_extract_action() -> None:
    assert extract_action("The Contractor shall design the jetty.") == "design"


def test_extract_action_returns_none_when_no_action() -> None:
    assert extract_action("The jetty is located within the terminal.") is None


def test_is_requirement_candidate() -> None:
    assert is_requirement_candidate("The Contractor shall provide design drawings.")

    assert is_requirement_candidate("The Contractor must submit calculations.")


def test_non_requirement_is_rejected() -> None:
    assert not is_requirement_candidate(
        "The existing jetty is approximately 200 m long."
    )


def test_build_requirement_id_is_deterministic() -> None:
    source = make_source_location()

    requirement_id_1 = build_requirement_id(
        source_location=source,
        requirement_text="The Contractor shall provide steel piles.",
    )

    requirement_id_2 = build_requirement_id(
        source_location=source,
        requirement_text="The Contractor shall provide steel piles.",
    )

    assert requirement_id_1 == requirement_id_2
    assert requirement_id_1.startswith("REQ-")


def test_build_requirement_id_changes_with_requirement_text() -> None:
    source = make_source_location()

    requirement_id_1 = build_requirement_id(
        source_location=source,
        requirement_text="The Contractor shall provide steel piles.",
    )

    requirement_id_2 = build_requirement_id(
        source_location=source,
        requirement_text="The Contractor shall provide concrete piles.",
    )

    assert requirement_id_1 != requirement_id_2


def test_build_requirement_id_changes_with_source_location() -> None:
    source_1 = make_source_location()

    source_2 = SourceLocation(
        document_id="DOC-TEST-001",
        document_number="RFP-001",
        revision="1",
        page=12,
        section="4.2 Piling",
    )

    requirement_text = "The Contractor shall provide steel piles."

    requirement_id_1 = build_requirement_id(
        source_location=source_1,
        requirement_text=requirement_text,
    )

    requirement_id_2 = build_requirement_id(
        source_location=source_2,
        requirement_text=requirement_text,
    )

    assert requirement_id_1 != requirement_id_2


def test_extract_mandatory_requirement() -> None:
    source = make_source_location()

    text = "The Contractor shall provide steel tubular piles."

    requirements = extract_requirements(
        text=text,
        source_location=source,
    )

    assert len(requirements) == 1

    requirement = requirements[0]

    assert requirement.requirement_text == text
    assert requirement.source_text == text
    assert requirement.requirement_type == "mandatory"
    assert requirement.obligation_type == "deliverable"
    assert requirement.action == "provide"
    assert requirement.evidence_type == "source_fact"
    assert requirement.confidence == "medium"


def test_extract_design_requirement() -> None:
    source = make_source_location()

    text = "The Contractor shall design the jetty for a 60-year service life."

    requirements = extract_requirements(
        text=text,
        source_location=source,
    )

    assert len(requirements) == 1

    requirement = requirements[0]

    assert requirement.requirement_type == "mandatory"
    assert requirement.obligation_type == "design"
    assert requirement.action == "design"


def test_extract_multiple_requirements() -> None:
    source = make_source_location()

    text = (
        "The Contractor shall provide steel piles. "
        "The Contractor shall submit design calculations. "
        "The Contractor shall perform pile testing."
    )

    requirements = extract_requirements(
        text=text,
        source_location=source,
    )

    assert len(requirements) == 3
    assert requirements[0].action == "provide"
    assert requirements[1].action == "submit"
    assert requirements[2].action == "perform"


def test_source_location_is_preserved() -> None:
    source = make_source_location()

    requirements = extract_requirements(
        text="The Contractor shall provide design drawings.",
        source_location=source,
    )

    requirement = requirements[0]

    assert requirement.source_location.document_id == "DOC-TEST-001"
    assert requirement.source_location.document_number == "RFP-001"
    assert requirement.source_location.revision == "0"
    assert requirement.source_location.page == 12
    assert requirement.source_location.section == "4.2 Piling"


def test_paragraph_extraction() -> None:
    source = make_source_location()

    paragraphs = [
        "The Contractor shall provide steel piles.",
        "The Contractor shall submit design calculations.",
        "The existing jetty is approximately 200 m long.",
    ]

    requirements = extract_requirements_from_paragraphs(
        paragraphs=paragraphs,
        source_location=source,
    )

    assert len(requirements) == 2
    assert requirements[0].action == "provide"
    assert requirements[1].action == "submit"


def test_empty_text_returns_no_requirements() -> None:
    source = make_source_location()

    requirements = extract_requirements(
        text="",
        source_location=source,
    )

    assert requirements == []


def test_extractor_does_not_invent_missing_information() -> None:
    source = make_source_location()

    text = "The Contractor shall provide steel piles."

    requirements = extract_requirements(
        text=text,
        source_location=source,
    )

    requirement = requirements[0]

    assert requirement.requirement_text == text
    assert requirement.assumptions == ()
    assert requirement.uncertainties == ()
