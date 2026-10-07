from __future__ import annotations

from copy import deepcopy

import pytest

from engineering_ai_reviewer import ReviewerUnavailable, build_alignment_reviewer
from tests.support.reviewer_harness import _Analyzer, _context, _finding


def test_plan_review_requests_and_returns_only_plan_alignment() -> None:
    analyzer = _Analyzer([_finding("plan_alignment")])
    review = build_alignment_reviewer(analyzer)(_context("plan"))

    assert analyzer.request is not None
    assert analyzer.request["mode"] == "client_alignment"
    assert analyzer.request["project_id"] == "PROJECT-1"
    assert analyzer.request["document_ids"] == []
    assert analyzer.request["evidence"][0]["evidence_class"] == "source_fact"
    assert analyzer.request["instructions"].endswith(
        "Do not approve, change risk or grant authority."
    )
    assert [item["evidence_id"] for item in analyzer.request["evidence"]] == [
        "evidence:source-1"
    ]
    assert {item["criterion"] for item in review["checks"]} == {"plan_alignment"}
    assert review["status"] == "aligned"
    assert review["evidence_ids"] == ["evidence:source-1"]
    assert review["request_digest"] and review["provider_request_digest"]


def test_outcome_review_requires_all_three_assurance_criteria() -> None:
    criteria = ["plan_alignment", "execution_direction", "outcome_alignment"]
    analyzer = _Analyzer([_finding(criterion) for criterion in criteria])
    review = build_alignment_reviewer(analyzer)(_context("outcome"))

    assert analyzer.request is not None
    assert (
        analyzer.request["context"]["document_ai_assessment"]["node_id"]
        == "assessment:1"
    )
    assert {item["criterion"] for item in review["checks"]} == set(criteria)
    assert len(review["checks"]) == 3
    assert review["status"] == "aligned"


def test_outcome_reviewer_requires_evidence_bound_candidate_disproof():
    context = _context("outcome")
    context["document_ai_assessment"] = {
        "assessment": {
            "findings": [
                {
                    "finding_id": "AI-F1",
                    "finding": "A candidate discrepancy requiring review.",
                    "source_evidence_ids": ["evidence:source-1"],
                }
            ]
        }
    }

    class DisprovingAnalyzer(_Analyzer):
        def analyse(self, request):
            self.request = deepcopy(request)
            return {
                "findings": deepcopy(self.findings),
                "finding_disproofs": [
                    {
                        "candidate_finding_id": "AI-F1",
                        "review_disposition": "disproved",
                        "rationale": "The strongest compliant interpretation resolves the apparent difference.",
                        "proposed_qualification": "no_issue",
                        "counter_evidence_ids": ["evidence:source-1"],
                    }
                ],
                "model": "offline-test-model",
                "response_id": "response:review-1",
                "usage": {"input_tokens": 10},
            }

    analyzer = DisprovingAnalyzer(
        [_finding(criterion) for criterion in ["plan_alignment", "execution_direction", "outcome_alignment"]]
    )
    review = build_alignment_reviewer(analyzer)(context)

    assert analyzer.request["candidate_findings"][0]["finding_id"] == "AI-F1"
    assert review["finding_disproofs"][0]["review_disposition"] == "disproved"
    assert review["status"] == "misaligned"
    assert review["review_comments"]


def test_reviewer_rejects_citations_outside_supplied_context() -> None:
    analyzer = _Analyzer([_finding("plan_alignment", "evidence:other-project")])

    with pytest.raises(ValueError, match="unknown source citations"):
        build_alignment_reviewer(analyzer)(_context("plan"))


def test_provider_unavailability_is_retryable_and_reviewer_does_not_persist() -> None:
    calls = 0

    class UnavailableAnalyzer:
        def analyse(self, request: dict) -> dict:
            nonlocal calls
            calls += 1
            raise ReviewerUnavailable("review provider unavailable")

    reviewer = build_alignment_reviewer(UnavailableAnalyzer())
    with pytest.raises(ReviewerUnavailable, match="provider unavailable"):
        reviewer(_context("plan"))

    assert calls == 1
