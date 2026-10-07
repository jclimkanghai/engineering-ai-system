"""Formal Reviewer comments need materiality and project evidence."""

from copy import deepcopy

import pytest

from engineering_ai_reviewer import build_alignment_reviewer
from engineering_registry.alignment import alignment_context
from engineering_registry.service import Principal, RegistryService
from tests.support.project_run_harness import alignment_report, workflow_for
from tests.support.reviewer_harness import _Analyzer, _context, _finding


@pytest.fixture
def services(configured):
    return configured


def material_comment():
    return {
        "what": "Load criterion may conflict",
        "evidence_ids": ["evidence:source-1"],
        "requirement_or_objective_ids": ["requirement:1"],
        "why_it_matters": "The governing safety basis may be wrong",
        "impact": "Potential underdesign",
        "required_response": "Verify the governing load",
    }


def test_reviewer_returns_only_evidence_bound_material_comment():
    finding = _finding("plan_alignment")
    finding["risk_dimensions"] = ["alignment:misaligned"]
    analyzer = _Analyzer([finding])
    original = analyzer.analyse

    def analyse(request):
        result = original(request)
        result["material_comments"] = [material_comment()]
        return result

    analyzer.analyse = analyse
    report = build_alignment_reviewer(analyzer)(_context("plan"))
    assert report["status"] == "misaligned"
    assert (
        report["material_comments"][0]["required_response"]
        == "Verify the governing load"
    )
    assert "mandatory safety" in analyzer.request["instructions"].lower()
    assert analyzer.request["instructions"].index(
        "technical integrity"
    ) < analyzer.request["instructions"].index("cost/programme")


def test_material_comment_cannot_pass_as_aligned():
    analyzer = _Analyzer([_finding("plan_alignment")])
    original = analyzer.analyse
    analyzer.analyse = lambda request: {
        **original(request),
        "material_comments": [material_comment()],
    }
    with pytest.raises(ValueError, match="Material Reviewer concern"):
        build_alignment_reviewer(analyzer)(_context("plan"))


def test_uncited_material_comment_is_rejected():
    analyzer = _Analyzer([_finding("plan_alignment")])
    original = analyzer.analyse
    bad = material_comment()
    bad["evidence_ids"] = ["evidence:other-project"]
    analyzer.analyse = lambda request: {**original(request), "material_comments": [bad]}
    with pytest.raises(ValueError, match="material|evidence"):
        build_alignment_reviewer(analyzer)(_context("plan"))


def test_immaterial_observation_stays_in_review_trace():
    analyzer = _Analyzer([_finding("plan_alignment")])
    report = build_alignment_reviewer(analyzer)(_context("plan"))
    assert report["checks"][0]["detail"]
    assert report["material_comments"] == []


def test_safety_information_gap_forces_insufficient_information():
    analyzer = _Analyzer([_finding("plan_alignment")])
    original = analyzer.analyse
    gap = {
        "concern": "Governing safety load may be missing",
        "domain": "safety",
        "requirement_or_objective_ids": ["requirement:1"],
        "potential_consequence": "Potential underdesign",
        "required_information": "Confirm current statutory load",
    }
    analyzer.analyse = lambda request: {**original(request), "information_gaps": [gap]}
    report = build_alignment_reviewer(analyzer)(_context("plan"))
    assert report["status"] == "insufficient_information"
    assert report["information_gaps"] == [gap]
    assert report["unknowns"]


def test_registry_rejects_uncited_formal_comment(services):
    _, agent, _ = services
    brain = workflow_for(services)
    brain.structure_task(
        "DEMO",
        "MATERIAL-T",
        "issue:F-LOAD",
        "compare_revision",
        {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
    )
    context = alignment_context(agent, "DEMO", "MATERIAL-T", phase="plan")
    report = alignment_report(context)
    comment = material_comment()
    comment["evidence_ids"] = ["evidence:outside"]
    comment["requirement_or_objective_ids"] = ["requirement:R-LOAD"]
    report["material_comments"] = [comment]
    reviewer = RegistryService(
        agent.store, Principal("reviewer-ai", frozenset({"DEMO"}), "ai_reviewer")
    )
    with pytest.raises(ValueError, match="material|evidence"):
        reviewer.record_alignment_review("DEMO", "MATERIAL-T", None, deepcopy(report))
