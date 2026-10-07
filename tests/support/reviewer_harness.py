from __future__ import annotations

from copy import deepcopy


def _context(phase: str) -> dict:
    evidence = {
        "node_id": "evidence:source-1",
        "node_type": "evidence",
        "attributes": {
            "document_id": "document:1",
            "revision": "A",
            "locator": "Section 2",
            "text": "The client requires a bounded engineering outcome.",
        },
    }
    requirement = {
        "node_id": "requirement:1",
        "node_type": "requirement",
        "attributes": {"statement": "Provide a bounded engineering outcome."},
    }
    return {
        "phase": phase,
        "project_id": "PROJECT-1",
        "source_snapshot": {"records": [deepcopy(evidence), deepcopy(requirement)]},
        "client_project_brief": {
            "source_basis": {"records": [deepcopy(evidence)]},
            "definition": {"purpose": "Resolve the project requirement."},
        },
        "client_mandate": {"objective": "Provide an evidence-backed solution."},
        "task": {"task_id": "task:1"},
        "brain_plan": {"objective": "Review the stated requirement."},
        "execution_result": {"node_id": "result:1"} if phase == "outcome" else None,
        "document_ai_assessment": (
            {"node_id": "assessment:1"} if phase == "outcome" else None
        ),
    }


def _finding(criterion: str, evidence_id: str = "evidence:source-1") -> dict:
    return {
        "finding_id": criterion,
        "title": "Objective alignment",
        "finding": f"{criterion} is supported by the retained source.",
        "status": "confirmed",
        "finding_class": "tender_observation",
        "qualification": "observation",
        "qualification_rationale": "This is an alignment observation, not a project non-compliance finding.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": None,
            "same_object": "not_applicable",
            "same_object_basis": None,
            "chronology": "current",
            "project_stage": "unknown",
            "materiality": "immaterial",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
        "source_evidence_ids": [evidence_id],
        "requirement_ids": ["requirement:1"],
        "interpretation": None,
        "steelman": None,
        "critic": None,
        "gap": None,
        "impact": None,
        "risk_level": "low",
        "risk_dimensions": ["alignment:aligned"],
        "recommendation": None,
        "human_review_required": True,
        "human_review_reason": "Human decision remains mandatory.",
        "confidence": "high",
        "assumptions": [],
        "uncertainties": [],
        "conflicts": [],
    }


class _Analyzer:
    def __init__(self, findings: list[dict]) -> None:
        self.findings = findings
        self.request: dict | None = None

    def analyse(self, request: dict) -> dict:
        self.request = deepcopy(request)
        return {
            "findings": deepcopy(self.findings),
            "finding_disproofs": [],
            "model": "offline-test-model",
            "response_id": "response:review-1",
            "usage": {"input_tokens": 10},
        }
