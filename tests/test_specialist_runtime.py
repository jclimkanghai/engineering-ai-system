from __future__ import annotations

import pytest

from engineering_ai_system.specialists import (
    SpecialistSkillCatalog,
    validate_specialist_proposal,
)
from engineering_registry.service import digest


def test_skill_catalog_returns_content_bound_to_digest(tmp_path):
    skill_file = tmp_path / "structural-review" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("# Structural review\nCheck evidence and state limits.\n")
    catalog = SpecialistSkillCatalog(tmp_path)

    skill = catalog.load("structural-review")

    assert skill == {
        "skill_id": "structural-review",
        "content": "# Structural review\nCheck evidence and state limits.\n",
        "skill_digest": digest(
            "# Structural review\nCheck evidence and state limits.\n"
        ),
        "manifest": None,
        "validation_status": "unclassified",
    }


def test_skill_catalog_rejects_path_traversal(tmp_path):
    catalog = SpecialistSkillCatalog(tmp_path)

    with pytest.raises(ValueError, match="Unknown specialist skill"):
        catalog.load("../secret")


def test_specialist_skill_must_match_discipline_and_tool(tmp_path):
    import json

    skill_root = tmp_path / "structural-review"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "# Structural review\nReview evidence only; calculations require an approved method.\n"
    )
    (skill_root / "specialist.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skill_id": "structural-review",
                "discipline": "structural",
                "validation_status": "draft",
                "validated_by": None,
                "validation_reference": None,
                "allowed_tools": ["compare_revision", "validate_traceability"],
            }
        )
    )
    catalog = SpecialistSkillCatalog(tmp_path)

    skill = catalog.load_for_assignment(
        "structural-review", "structural", "compare_revision"
    )

    assert skill["validation_status"] == "draft"
    assert skill["skill_digest"] != digest(skill["content"])
    with pytest.raises(ValueError, match="discipline does not match"):
        catalog.load_for_assignment(
            "structural-review", "geotechnical", "compare_revision"
        )
    with pytest.raises(ValueError, match="tool is not allowed"):
        catalog.load_for_assignment(
            "structural-review", "structural", "external_solver"
        )

    manifest = json.loads((skill_root / "specialist.json").read_text())
    manifest.update(
        {
            "validation_status": "validated",
            "validated_by": None,
            "validation_reference": None,
        }
    )
    (skill_root / "specialist.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="require reviewer provenance"):
        catalog.load_for_assignment(
            "structural-review", "structural", "compare_revision"
        )


def test_all_bundled_discipline_skills_have_scoped_draft_manifests():
    assignments = {
        "structural-review": ("structural", "compare_revision"),
        "geotechnical-review": ("geotechnical", "validate_traceability"),
        "marine-coastal-review": ("marine and coastal", "generate_review_pack"),
        "hydraulic-environmental-review": (
            "hydraulic and environmental",
            "compare_revision",
        ),
        "civil-review": ("civil", "validate_traceability"),
        "mechanical-review": ("mechanical", "compare_revision"),
        "electrical-review": ("electrical", "generate_review_pack"),
        "quantity-surveyor-review": (
            "quantity surveyor",
            "validate_proposal_readiness",
        ),
        "risk-specialist-review": (
            "risk specialist",
            "validate_traceability",
        ),
    }
    catalog = SpecialistSkillCatalog()

    for skill_id, (discipline, tool) in assignments.items():
        skill = catalog.load_for_assignment(skill_id, discipline, tool)
        assert skill["validation_status"] == "draft"
        assert skill["manifest"]["validated_by"] is None


def test_specialist_proposal_requires_cited_sources_and_explicit_unknowns():
    proposal = {
        "summary": "Review identified a calculation check.",
        "recommendation": "Verify the governing load case.",
        "evidence_ids": ["evidence:EA"],
        "assumptions": [],
        "unknowns": ["The calculation package was not supplied."],
    }

    checked = validate_specialist_proposal(proposal, {"evidence:EA"})

    assert checked == proposal


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_ids": ["evidence:OTHER"]},
        {"unknowns": "none"},
        {"summary": " "},
        {"summary": "x" * 3001},
        {"assumptions": ["too many"] * 11},
    ],
)
def test_specialist_proposal_rejects_untraceable_or_malformed_output(change):
    proposal = {
        "summary": "Review identified a calculation check.",
        "recommendation": "Verify the governing load case.",
        "evidence_ids": ["evidence:EA"],
        "assumptions": [],
        "unknowns": [],
        **change,
    }

    with pytest.raises(ValueError, match="specialist proposal"):
        validate_specialist_proposal(proposal, {"evidence:EA"})
