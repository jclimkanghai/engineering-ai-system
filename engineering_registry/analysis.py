"""Bounded proposed-analysis provenance, independent of provider/pipeline code."""

from __future__ import annotations

import json

from .service import text


def validate_analysis(
    analysis: dict,
    phase: str,
    evidence_ids: list[str],
    requirement_ids: list[str],
    record_ids: list[str] | None = None,
) -> None:
    fields = {
        "producer",
        "phase",
        "model",
        "response_id",
        "request_digest",
        "findings",
        "usage",
        "lesson_context",
        "human_review_required",
    }
    if (
        not isinstance(analysis, dict)
        or set(analysis) - fields - {"task_decisions", "lesson_reviews"}
        or not fields.issubset(analysis)
    ):
        raise ValueError("Invalid proposed analysis contract")
    if (
        analysis["producer"] != "engineering_document_ai"
        or analysis["phase"] != phase
        or analysis["human_review_required"] is not True
    ):
        raise ValueError(
            "Analysis is proposed Document AI reasoning, not human approval"
        )
    for key in ("model", "request_digest"):
        text(analysis[key], key)
    if analysis["response_id"] is not None:
        text(analysis["response_id"], "response_id")
    findings = analysis["findings"]
    if not isinstance(findings, list) or len(findings) > 100:
        raise ValueError("Analysis findings must be bounded")
    finding_ids: set[str] = set()
    for item in findings:
        if not isinstance(item, dict) or item.get("human_review_required") is not True:
            raise ValueError("Analysis findings require human review")
        for key in ("finding_id", "title", "finding"):
            text(item.get(key), key)
        if item["finding_id"] in finding_ids:
            raise ValueError("Duplicate proposed finding identifier")
        finding_ids.add(item["finding_id"])
        if item.get("risk_level") not in {
            "low",
            "medium",
            "high",
            "critical",
            "unknown",
        }:
            raise ValueError("Invalid proposed finding risk level")
        if item.get("status") not in {
            "confirmed",
            "potential",
            "unverified",
            "disputed",
            "requires_human_review",
        }:
            raise ValueError("Invalid proposed finding status")
        for key, allowed in (
            ("source_evidence_ids", evidence_ids),
            ("requirement_ids", requirement_ids),
        ):
            values = item.get(key)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or not set(values).issubset(allowed)
            ):
                raise ValueError("Unknown evidence or requirement in proposed analysis")
        if not item["source_evidence_ids"]:
            raise ValueError("Proposed analysis requires source evidence")
        for key in ("assumptions", "uncertainties", "conflicts"):
            values = item.get(key, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError("Invalid analysis assumptions/uncertainties")
    decisions = analysis.get("task_decisions", [])
    if not isinstance(decisions, list) or len(decisions) > 30:
        raise ValueError("Task decision proposals must be a bounded list")
    if phase != "execution_planning" and decisions:
        raise ValueError("Task decisions can only be proposed during planning")
    seen_decisions: set[str] = set()
    allowed_records = set(record_ids or [])
    for decision in decisions:
        expected = {
            "decision_id",
            "statement",
            "rationale",
            "decision_level",
            "evidence_ids",
            "requirement_ids",
            "related_record_ids",
            "alternatives",
            "assumptions",
            "impact",
        }
        if not isinstance(decision, dict) or set(decision) != expected:
            raise ValueError("Invalid proposed task decision contract")
        for key in ("decision_id", "statement", "rationale"):
            text(decision[key], key)
        if decision["decision_id"] in seen_decisions:
            raise ValueError("Duplicate proposed task decision identifier")
        seen_decisions.add(decision["decision_id"])
        if decision["decision_level"] not in {"D0", "D1", "D2", "D3", "D4"}:
            raise ValueError("Invalid proposed task decision level")
        for key, allowed_ids in (
            ("evidence_ids", set(evidence_ids)),
            ("requirement_ids", set(requirement_ids)),
            ("related_record_ids", allowed_records),
        ):
            values = decision[key]
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values))
                or not set(values).issubset(allowed_ids)
            ):
                raise ValueError("Unknown or duplicate task decision reference")
        if not decision["evidence_ids"]:
            raise ValueError("Proposed task decisions require source evidence")
        for key in ("alternatives", "assumptions"):
            values = decision[key]
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError("Invalid task decision alternatives or assumptions")
        if decision["impact"] is not None:
            text(decision["impact"], "impact")
    if (
        not isinstance(analysis["usage"], dict)
        or not isinstance(analysis["lesson_context"], list)
        or len(analysis["lesson_context"]) > 100
    ):
        raise ValueError("Invalid analysis provenance/context")
    lesson_ids = {
        lesson["node_id"]
        for lesson in analysis["lesson_context"]
        if isinstance(lesson, dict) and isinstance(lesson.get("attributes"), dict)
    }
    reviews = analysis.get("lesson_reviews", [])
    if not isinstance(reviews, list) or len(reviews) != len(lesson_ids):
        raise ValueError("Every reused knowledge item requires a review")
    reviewed_ids: set[str] = set()
    for review in reviews:
        base_fields = {
            "lesson_id",
            "status",
            "rationale",
            "requirement_ids",
            "evidence_ids",
            "requirements_check",
            "limitations",
            "resolution",
            "lead_action",
            "conflict_interpretation",
            "requirement_concern",
        }
        if not isinstance(review, dict) or set(review) != base_fields:
            raise ValueError("Invalid organisational lesson review contract")
        lesson_id = text(review["lesson_id"], "lesson_id")
        if lesson_id not in lesson_ids or lesson_id in reviewed_ids:
            raise ValueError("Unknown or duplicate knowledge item review")
        reviewed_ids.add(lesson_id)
        if review["status"] not in {
            "not_relevant",
            "not_applicable",
            "applicable",
            "partial",
            "conflict",
            "uncertain",
        }:
            raise ValueError("Invalid organisational lesson review status")
        text(review["rationale"], "rationale")
        limits = review["limitations"]
        if not isinstance(limits, list) or any(
            not isinstance(item, str) or not item.strip() for item in limits
        ):
            raise ValueError("Invalid organisational lesson limitations")
        if review["status"] == "partial" and not limits:
            raise ValueError(
                "Organisational lesson partial applicability requires limitations"
            )
        for key, allowed_ids in (
            ("requirement_ids", set(requirement_ids)),
            ("evidence_ids", set(evidence_ids)),
        ):
            values = review[key]
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values))
                or not set(values).issubset(allowed_ids)
            ):
                raise ValueError("Unknown or duplicate organisational lesson citation")
        if review["status"] == "conflict" and (
            not review["requirement_ids"] or not review["evidence_ids"]
        ):
            raise ValueError(
                "Organisational lesson conflict requires current project citations"
            )
        if review["status"] == "conflict":
            if review["resolution"] not in {
                "compliant_alternative",
                "deviation_proposed",
                "unresolved",
            }:
                raise ValueError("Invalid knowledge conflict resolution")
            text(review["lead_action"], "lead_action")
            if review["conflict_interpretation"] not in {
                "project_requirement_controls",
                "possible_requirement_problem",
            }:
                raise ValueError("Knowledge conflict requires an interpretation")
            if review["conflict_interpretation"] == "possible_requirement_problem":
                try:
                    text(review["requirement_concern"], "requirement_concern")
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Possible requirement concern requires an explanation"
                    ) from exc
            elif review["requirement_concern"] is not None:
                raise ValueError(
                    "Requirement concern needs a possible-problem interpretation"
                )
        elif any(
            review[key] is not None
            for key in (
                "resolution",
                "lead_action",
                "conflict_interpretation",
                "requirement_concern",
            )
        ):
            raise ValueError(
                "Only knowledge conflicts may propose a conflict interpretation or Lead action"
            )
        if review["status"] == "applicable" and not review["evidence_ids"]:
            raise ValueError(
                "Applicable organisational lesson requires current evidence"
            )
        required_check = {
            "not_relevant": "not_applicable",
            "not_applicable": "not_applicable",
            "applicable": "complies",
            "partial": "complies",
            "conflict": "conflict",
            "uncertain": "unknown",
        }[review["status"]]
        if review["requirements_check"] != required_check:
            raise ValueError(
                "Organisational lesson current project requirements check is inconsistent"
            )
        if review["status"] in {"applicable", "partial"} and (
            not review["requirement_ids"] or not review["evidence_ids"]
        ):
            raise ValueError(
                "Reusable organisational lesson requires current project citations"
            )
    if len(json.dumps(analysis, ensure_ascii=False, allow_nan=False)) > 500_000:
        raise ValueError("Proposed analysis exceeds bounded context limit")
