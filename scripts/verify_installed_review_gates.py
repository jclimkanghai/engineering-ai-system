"""Exercise both installed AI Reviewer gates with synthetic offline adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.alignment import current_review
from engineering_registry.demo import seed_demo
from engineering_registry.extended_demo import synthetic_client_project_brief
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore
from pipelines.llm.models import AnalysisRequest, LLMResponse
from pipelines.review.execution_workflow import build_reasoning_workflow


def _finding(
    criterion: str, evidence_ids: list[str], requirement_ids: list[str]
) -> dict:
    return {
        "finding_id": criterion,
        "title": "Synthetic alignment check",
        "finding": "The retained synthetic source supports this alignment check.",
        "status": "confirmed",
        "finding_class": "tender_observation",
        "qualification": "observation",
        "qualification_rationale": "This is a synthetic alignment observation, not a formal engineering finding.",
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
        "source_evidence_ids": evidence_ids,
        "requirement_ids": requirement_ids,
        "interpretation": "Synthetic fixture only.",
        "steelman": None,
        "critic": None,
        "gap": None,
        "impact": None,
        "risk_level": "low",
        "risk_dimensions": ["alignment:aligned"],
        "recommendation": None,
        "human_review_required": True,
        "human_review_reason": "Human verification and decision remain required.",
        "confidence": "high",
        "assumptions": [],
        "uncertainties": [],
        "conflicts": [],
    }


class _BrainAdapter:
    def analyse(self, request: AnalysisRequest) -> LLMResponse:
        return LLMResponse("offline-brain-fixture", request.mode, [], {})


class _ReviewerAdapter:
    def __init__(self) -> None:
        self.phases: list[str] = []

    def analyse(self, request: AnalysisRequest) -> LLMResponse:
        context = request.context
        phase = context["phase"]
        self.phases.append(phase)
        evidence_ids = [item["evidence_id"] for item in request.evidence]
        requirement_ids = [item["node_id"] for item in request.requirements]
        criteria = (
            ["plan_alignment"]
            if phase == "plan"
            else ["plan_alignment", "execution_direction", "outcome_alignment"]
        )
        findings = [
            _finding(criterion, evidence_ids, requirement_ids) for criterion in criteria
        ]
        return LLMResponse("offline-reviewer-fixture", phase, findings, {})


def _mandate() -> dict[str, Any]:
    return {
        "objective": "Identify the literal changes between the two supplied excerpts.",
        "scope": "Text comparison only; no design acceptance or source hierarchy conclusion.",
        "acceptance_criteria": [
            "Show removed and added wording with both source identities."
        ],
        "evidence_ids": ["evidence:EA", "evidence:EB"],
        "allowed_tools": ["compare_revision"],
        "risk_level": "low",
        "importance_level": "low",
        "simple": True,
        "reversible": True,
        "consequence_domains": [],
        "unknowns": [],
    }


def verify(destination: Path) -> dict[str, Any]:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a fresh destination for the installed gate smoke")
    destination.mkdir(parents=True, exist_ok=True)
    database = destination / "review-gates.sqlite"
    seed_demo(database)
    reviewer_adapter = _ReviewerAdapter()

    with SQLiteGraphStore(database) as store:
        document_ai = RegistryService(
            store, Principal("synthetic-document-ai", frozenset({"DEMO"}))
        )
        human = RegistryService(
            store,
            Principal("synthetic-human", frozenset({"DEMO"}), "reviewer"),
        )
        for revision, evidence, status in (
            ("document_revision:BASIS-A%3AA", "evidence:EA", "superseded"),
            ("document_revision:BASIS-B%3AB", "evidence:EB", "current"),
        ):
            source = human.document_control("DEMO", revision)
            human.review_document_control(
                "DEMO",
                revision,
                source["control_digest"],
                status,
                [evidence],
                "Synthetic source-authority fixture",
            )
        human.record_client_project_brief(
            "DEMO",
            "synthetic-project-brief",
            synthetic_client_project_brief(["evidence:EA", "evidence:EB"]),
        )
        human.record_client_mandate(
            "DEMO", "synthetic-client-mandate", "issue:F-LOAD", _mandate()
        )
        workflow: DocumentAIWorkflow = build_reasoning_workflow(
            document_ai,
            analysis_adapter=_BrainAdapter(),
            alignment_adapter=reviewer_adapter,
        )
        task = workflow.structure_task(
            "DEMO",
            "installed-review-smoke",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )
        gate1 = current_review(document_ai, "DEMO", task["task_id"], phase="plan")
        if not gate1 or gate1["attributes"]["status"] != "aligned":
            raise RuntimeError(
                "Installed AI Reviewer Gate 1 did not persist an aligned review"
            )
        if task["state"] != "proposed":
            raise RuntimeError("Gate 1 must not authorize or execute the task")

        ExecutionService(human).authorize_task(
            "DEMO",
            task["task_id"],
            task["task_digest"],
            "Synthetic human execution authorization",
        )
        outcome = workflow.execute_and_assess("DEMO", task["task_id"])
        assessment = outcome["assessment"]
        gate2 = outcome["alignment_review"]
        if not assessment or not gate2:
            raise RuntimeError("Installed AI Reviewer Gate 2 did not complete")
        if gate2["attributes"]["document_ai_assessment_id"] != assessment["node_id"]:
            raise RuntimeError("Gate 2 did not retain the exact Document AI assessment")
        if gate2["attributes"]["result_id"] != outcome["result"]["node_id"]:
            raise RuntimeError("Gate 2 did not retain the exact V2 result")
        if outcome.get("decision") is not None:
            raise RuntimeError("An AI review must not create a human decision")
        if human.list_records("DEMO", "verification"):
            raise RuntimeError("An AI review must not create human verification")
        if reviewer_adapter.phases != ["plan", "outcome"]:
            raise RuntimeError(f"Unexpected reviewer phases: {reviewer_adapter.phases}")
        result = {
            "gate1_status": gate1["attributes"]["status"],
            "gate2_status": gate2["attributes"]["status"],
            "gate1_phase": gate1["attributes"]["phase"],
            "gate2_phase": gate2["attributes"]["phase"],
            "result_id": outcome["result"]["node_id"],
            "assessment_id": assessment["node_id"],
            "gate2_assessment_id": gate2["attributes"]["document_ai_assessment_id"],
            "human_verification_count": 0,
            "human_decision": None,
            "synthetic_data_only": True,
        }
    (destination / "review-gates.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps({"installed_reviewer_gates": "passed", **result}, sort_keys=True))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_installed_review_gates.py DESTINATION")
    verify(Path(sys.argv[1]))
