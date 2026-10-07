"""Reusable deterministic analysis adapter for Brain workflow tests."""

from __future__ import annotations

from copy import deepcopy

from pipelines.llm.models import LLMResponse


def finding(
    status="requires_human_review",
    evidence_id="evidence:EB",
    requirement_id="requirement:R-LOAD",
):
    return {
        "finding_id": "MODEL-LOAD",
        "title": "Review increased load",
        "finding": "The updated excerpt states 20 kN rather than 10 kN.",
        "status": status,
        "finding_class": "verification_item",
        "qualification": "verification_required",
        "qualification_rationale": "The available information supports a bounded verification item.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "The evidence and requirement address the same design load.",
            "chronology": "current",
            "project_stage": "detailed_design",
            "materiality": "material",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
        "source_evidence_ids": [evidence_id],
        "requirement_ids": [requirement_id],
        "interpretation": "The stated load changed; adequacy is not established.",
        "risk_level": "unknown",
        "risk_dimensions": ["technical"],
        "recommendation": "Check the change against the source requirement.",
        "human_review_required": True,
        "human_review_reason": "Engineering adequacy requires verification.",
        "confidence": "medium",
        "assumptions": [],
        "uncertainties": ["Design adequacy is unknown."],
        "conflicts": [],
    }


class OfflineAnalysisAdapter:
    def __init__(
        self, planning=None, assessment=None, task_decisions=None, lesson_reviews=None
    ):
        self.calls = []
        self.planning = planning if planning is not None else [finding()]
        self.assessment = assessment if assessment is not None else [finding()]
        self.task_decisions = task_decisions or []
        self.lesson_reviews = lesson_reviews or []
        self.failure = None
        self.before_return = None

    def analyse(self, request):
        self.calls.append(deepcopy(request))
        if self.before_return:
            self.before_return(request)
        if self.failure:
            raise self.failure
        items = (
            self.planning if request.mode == "execution_planning" else self.assessment
        )
        return LLMResponse(
            "offline-analysis-fixture",
            "response-" + request.mode,
            deepcopy(items),
            {"offline_fixture": True},
            task_decisions=(
                deepcopy(self.task_decisions)
                if request.mode == "execution_planning"
                else []
            ),
            lesson_reviews=deepcopy(self.lesson_reviews),
        )
