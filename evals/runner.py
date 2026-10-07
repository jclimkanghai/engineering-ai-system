from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from pipelines.findings import EvidenceRef
from pipelines.llm.adapter import OpenAIAnalysisAdapter, findings_from_response
from pipelines.llm.models import AnalysisRequest, LLMResponse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "cases.json"


def load_cases(path: str | Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload["cases"]
    identifiers = [case["case_id"] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Evaluation case IDs must be unique")
    if any(case.get("synthetic") is not True for case in cases):
        raise ValueError(
            "Every case must explicitly identify its synthetic fixture data"
        )
    return cases


def _as_response(response: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(response, list):
        return {"findings": response}
    if not isinstance(response, dict) or not isinstance(response.get("findings"), list):
        raise ValueError("Response must be an object containing a findings array")
    return response


def evaluate_case(
    case: dict[str, Any],
    response: dict[str, Any] | list[dict[str, Any]],
    *,
    response_present: bool = True,
) -> dict[str, Any]:
    """Apply deterministic structure, citation, abstention, and safety gates.

    Semantic correctness is deliberately left for the attached human rubric.
    """
    response_payload = _as_response(response)
    findings_payload = response_payload["findings"]
    request = case["request"]
    expectations = case.get("expectations", {})
    evidence_rows = request.get("evidence", [])
    known_evidence = {row["evidence_id"] for row in evidence_rows}
    known_requirements = {
        row["requirement_id"] for row in request.get("requirements", [])
    }
    evidence_refs = [
        EvidenceRef(
            evidence_id=row["evidence_id"],
            document_id=row.get("document_id"),
            revision=row.get("revision"),
            locator=row.get("locator"),
            excerpt=row.get("text"),
        )
        for row in evidence_rows
    ]
    accepted, errors, warnings = findings_from_response(
        LLMResponse(model="evaluation", response_id=None, findings=findings_payload),
        evidence_refs,
    )
    gates: dict[str, bool] = {
        "response_present": response_present,
        "response_structure": len(errors) == 0,
        "evidence_ids_known": all(
            evidence_id in known_evidence
            for finding in findings_payload
            for evidence_id in finding.get("source_evidence_ids", [])
        ),
        "requirement_ids_known": all(
            requirement_id in known_requirements
            for finding in findings_payload
            for requirement_id in finding.get("requirement_ids", [])
        ),
        "minimum_findings": len(accepted) >= expectations.get("minimum_findings", 0),
    }
    if expectations.get("require_evidence_for_each_finding"):
        gates["each_finding_has_evidence"] = all(
            bool(finding.get("source_evidence_ids")) for finding in findings_payload
        )
    if expectations.get("require_abstention_without_evidence"):
        gates["abstains_without_evidence"] = bool(evidence_rows) or not findings_payload
        gates["uncertainty_explicit"] = all(
            bool(finding.get("uncertainties"))
            and finding.get("status") not in {"confirmed", "potential"}
            for finding in findings_payload
        )
    if expectations.get("human_review_required"):
        gates["human_review_gate"] = all(
            finding.get("human_review_required") is True for finding in findings_payload
        )
    if "alignment_checks" in expectations:
        expected_checks = expectations["alignment_checks"]
        actual_checks = {}
        for finding in findings_payload:
            markers = [
                value.removeprefix("alignment:")
                for value in finding.get("risk_dimensions", [])
                if isinstance(value, str) and value.startswith("alignment:")
            ]
            if len(markers) == 1:
                actual_checks[finding.get("finding_id")] = markers[0]
        gates["alignment_verdicts"] = (
            len(actual_checks) == len(findings_payload)
            and actual_checks == expected_checks
        )
    if "lesson_statuses" in expectations:
        expected_lessons = expectations["lesson_statuses"]
        reviews = response_payload.get("lesson_reviews")
        reviews = reviews if isinstance(reviews, list) else []
        actual_lessons = {}
        citations_valid = True
        for review in reviews:
            if not isinstance(review, dict):
                citations_valid = False
                continue
            lesson_id = review.get("lesson_id")
            if not isinstance(lesson_id, str) or lesson_id in actual_lessons:
                citations_valid = False
                continue
            actual_lessons[lesson_id] = review.get("status")
            requirement_ids = review.get("requirement_ids")
            evidence_ids = review.get("evidence_ids")
            citations_valid = citations_valid and (
                isinstance(requirement_ids, list)
                and isinstance(evidence_ids, list)
                and set(requirement_ids).issubset(known_requirements)
                and set(evidence_ids).issubset(known_evidence)
                and bool(str(review.get("rationale", "")).strip())
                and (
                    review.get("status") != "conflict"
                    or bool(requirement_ids)
                    and bool(evidence_ids)
                )
            )
        gates["lesson_verdicts"] = actual_lessons == expected_lessons
        gates["lesson_citations"] = citations_valid
        if "conflict" in expected_lessons.values():
            gates["lesson_conflict_finding"] = all(
                any(
                    lesson_id in conflict
                    and set(finding.get("source_evidence_ids", []))
                    & set(review.get("evidence_ids", []))
                    and set(finding.get("requirement_ids", []))
                    & set(review.get("requirement_ids", []))
                    and finding.get("human_review_required") is True
                    for finding in findings_payload
                    for conflict in finding.get("conflicts", [])
                    for review in reviews
                    if isinstance(review, dict) and review.get("lesson_id") == lesson_id
                )
                for lesson_id, status in expected_lessons.items()
                if status == "conflict"
            )

    # The engine normalizes high risk to human review. Check the original result
    # as well so an unsafe model response cannot pass after normalization.
    high_risk_values = {"high", "critical"}
    gates["high_risk_requires_review"] = all(
        finding.get("risk_level") not in high_risk_values
        or finding.get("human_review_required") is True
        for finding in findings_payload
    )

    failed = [name for name, passed in gates.items() if not passed]
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "complexity": case["complexity"],
        "passed": not failed,
        "gates": gates,
        "failed_gates": failed,
        "accepted_finding_count": len(accepted),
        "rejected_finding_count": len(findings_payload) - len(accepted),
        "validation_messages": errors,
        "validation_warnings": warnings,
        "response_model": response_payload.get("model"),
        "human_review": "required_for_semantic_quality",
    }


def score_human_rubric(scores: dict[str, int]) -> dict[str, Any]:
    """Score the 8-dimension engineering rubric (0–2 per dimension)."""
    dimensions = {
        "source_traceability",
        "evidence_classification",
        "revision_control",
        "uncertainty",
        "steelman_quality",
        "completeness",
        "professional_boundary",
        "response_fit",
    }
    if set(scores) != dimensions:
        raise ValueError("Human rubric must score each of the 8 defined dimensions")
    if any(
        type(score) is not int or score not in {0, 1, 2} for score in scores.values()
    ):
        raise ValueError("Each human rubric score must be an integer from 0 to 2")
    total = sum(scores.values())
    critical_pass = all(
        scores[key] >= 1
        for key in ("source_traceability", "evidence_classification", "uncertainty")
    )
    return {
        "scores": scores,
        "total": total,
        "maximum": 16,
        "passed": total >= 12 and critical_pass,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_complexity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_complexity[result["complexity"]].append(result)
        by_category[result.get("category", "unknown")].append(result)

    def summarize_group(
        groups: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "total": len(items),
                "passed": sum(item["passed"] for item in items),
                "failed": sum(not item["passed"] for item in items),
                "pass_rate": sum(item["passed"] for item in items) / len(items),
            }
            for name, items in sorted(groups.items())
        }

    return {
        "total": len(results),
        "passed": all(result["passed"] for result in results),
        "pass_rate": statistics.mean(result["passed"] for result in results)
        if results
        else 0.0,
        "by_complexity": summarize_group(by_complexity),
        "by_category": summarize_group(by_category),
    }


def summarize_human_scores(scores_by_case: dict[str, dict[str, int]]) -> dict[str, Any]:
    reviews = {
        case_id: score_human_rubric(scores)
        for case_id, scores in scores_by_case.items()
    }
    return {
        "reviewed_cases": len(reviews),
        "passed": all(item["passed"] for item in reviews.values()),
        "pass_rate": statistics.mean(item["passed"] for item in reviews.values())
        if reviews
        else None,
        "results": reviews,
    }


def _live_responses(
    cases: list[dict[str, Any]], *, model: str | None
) -> dict[str, dict[str, Any]]:
    adapter = OpenAIAnalysisAdapter(model=model)
    responses = {}
    for case in cases:
        request = case["request"]
        result = adapter.analyse(
            AnalysisRequest(
                mode=case["mode"],
                project_id=request.get("project_id"),
                document_ids=request.get("document_ids", []),
                requirements=request.get("requirements", []),
                evidence=request.get("evidence", []),
                context={**request.get("context", {}), "user_request": case["prompt"]},
                instructions=request.get("instructions"),
            )
        )
        responses[case["case_id"]] = {
            "findings": result.findings,
            "lesson_reviews": result.lesson_reviews,
            "model": result.model,
        }
    return responses


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Engineering Document AI responses."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--responses",
        type=Path,
        help="Offline JSON mapping case IDs to {findings: [...]} responses.",
    )
    parser.add_argument(
        "--human-scores",
        type=Path,
        help="Optional JSON mapping case IDs to the 8 human rubric scores.",
    )
    parser.add_argument(
        "--live-openai",
        action="store_true",
        help="Make live OpenAI API calls; this may incur API charges.",
    )
    parser.add_argument("--model", help="Optional model override for --live-openai.")
    parser.add_argument("--limit", type=int, help="Run only the first N cases.")
    parser.add_argument(
        "--output", type=Path, help="Write the full report to this JSON path."
    )
    args = parser.parse_args()
    if args.live_openai == bool(args.responses):
        parser.error("Choose exactly one of --responses PATH or --live-openai")

    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: max(0, args.limit)]
    responses = (
        _live_responses(cases, model=args.model)
        if args.live_openai
        else json.loads(args.responses.read_text(encoding="utf-8"))
    )
    results = []
    for case in cases:
        response_present = case["case_id"] in responses
        candidate = responses.get(case["case_id"], {"findings": []})
        try:
            result = evaluate_case(case, candidate, response_present=response_present)
        except (KeyError, TypeError, ValueError) as exc:
            result = {
                "case_id": case["case_id"],
                "category": case["category"],
                "complexity": case["complexity"],
                "passed": False,
                "gates": {},
                "failed_gates": ["evaluation_error"],
                "validation_messages": [str(exc)],
                "human_review": "required_for_semantic_quality",
            }
        results.append(result)
    report = {
        "benchmark_version": 1,
        "synthetic_data_only": True,
        "summary": summarize_results(results),
        "human_review": summarize_human_scores(
            json.loads(args.human_scores.read_text(encoding="utf-8"))
        )
        if args.human_scores
        else {"status": "not_scored", "reviewed_cases": 0},
        "results": results,
        "limitations": [
            "The deterministic gates do not judge semantic engineering correctness.",
            "Complete the human rubric in evals/rubric.yaml for semantic quality.",
            "Only cases sent to the selected model are evaluated; live runs require explicit --live-openai.",
        ],
    }
    serialized = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
