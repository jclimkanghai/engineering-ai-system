"""Contracts for evidence-bound project-run integration assessments."""

from __future__ import annotations

from copy import deepcopy

from engineering_registry.delegation import string_list
from engineering_registry.service import text

ASSESSMENT_SCHEMA_VERSION = 1
CROSS_TASK_DOMAINS = (
    "design_basis",
    "shared_assumptions",
    "interfaces",
    "source_revisions",
    "dependencies",
    "contradictions",
    "residual_risks",
)
_ASSESSMENT_FIELDS = {
    "schema_version",
    "status",
    "requirement_coverage",
    "acceptance_coverage",
    "cross_task_checks",
    "assumptions",
    "limitations",
    "unknowns",
    "residual_risks",
}


def validate_integrated_assessment(
    assessment: dict, plan: dict, task_outcomes: list[dict]
) -> dict:
    """Validate coverage, provenance and conservative status for an integrated assessment."""
    if not isinstance(assessment, dict) or set(assessment) != _ASSESSMENT_FIELDS:
        raise ValueError("Invalid integrated assessment contract")
    if assessment["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
        raise ValueError("Unsupported integrated assessment schema version")
    if assessment["status"] not in {"clear", "concerns", "insufficient_information"}:
        raise ValueError("Invalid integrated assessment status")

    tasks = {item["task_id"] for item in task_outcomes}
    requirements = {
        requirement_id
        for item in plan["tasks"]
        for requirement_id in item["brain_plan"].get("requirement_ids", [])
    }
    acceptance = {
        (item["task_id"], criterion)
        for item in plan["tasks"]
        for criterion in item["brain_plan"].get("acceptance_criteria", [])
    }
    evidence = {
        evidence_id
        for item in task_outcomes
        for evidence_id in item["provenance"]["evidence_ids"]
    }

    def validate_coverage(key: str, expected: set, identity_fields: set[str]) -> bool:
        rows = assessment[key]
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ValueError(
                f"Integrated assessment {key} must cover every planned item"
            )
        seen = set()
        has_concern = False
        has_unknown = False
        for row in rows:
            if not isinstance(row, dict) or set(row) != identity_fields | {
                "status",
                "detail",
                "evidence_ids",
            }:
                raise ValueError(f"Invalid integrated assessment {key} row")
            identity = (
                (row["requirement_id"],)
                if "requirement_id" in identity_fields
                else (row["task_id"], row["criterion"])
            )
            if identity not in expected or identity in seen:
                raise ValueError(
                    f"Unknown or duplicate item in integrated assessment {key}"
                )
            seen.add(identity)
            if row["status"] not in {"met", "gap", "unknown"}:
                raise ValueError(f"Invalid integrated assessment {key} status")
            text(row["detail"], "assessment detail")
            string_list(row["evidence_ids"], "assessment evidence")
            if not set(row["evidence_ids"]).issubset(evidence):
                raise ValueError(f"Integrated assessment {key} cites unknown evidence")
            if row["status"] == "met" and not row["evidence_ids"]:
                raise ValueError(f"Met {key} requires supporting evidence")
            has_concern |= row["status"] == "gap"
            has_unknown |= row["status"] == "unknown"
        if seen != expected:
            raise ValueError(f"Integrated assessment {key} is incomplete")
        return has_concern or has_unknown

    requirement_rows = {
        (row.get("requirement_id"),)
        for row in assessment["requirement_coverage"]
        if isinstance(row, dict)
    }
    if requirement_rows != {(item,) for item in requirements}:
        raise ValueError("Integrated assessment must cover every planned requirement")
    acceptance_rows = {
        (row.get("task_id"), row.get("criterion"))
        for row in assessment["acceptance_coverage"]
        if isinstance(row, dict)
    }
    requirement_issue = validate_coverage(
        "requirement_coverage", {(item,) for item in requirements}, {"requirement_id"}
    )
    acceptance_issue = validate_coverage(
        "acceptance_coverage", acceptance, {"task_id", "criterion"}
    )
    if acceptance_rows != acceptance:
        raise ValueError(
            "Integrated assessment acceptance coverage does not match the plan"
        )

    checks = assessment["cross_task_checks"]
    if not isinstance(checks, list) or len(checks) != len(CROSS_TASK_DOMAINS):
        raise ValueError("Integrated assessment must check every cross-task domain")
    by_domain = {}
    concern = False
    unknown = False
    for item in checks:
        if not isinstance(item, dict) or set(item) != {
            "domain",
            "status",
            "detail",
            "task_ids",
            "evidence_ids",
        }:
            raise ValueError("Invalid integrated cross-task check")
        if item["domain"] not in CROSS_TASK_DOMAINS or item["domain"] in by_domain:
            raise ValueError("Unknown or duplicate integrated cross-task domain")
        if item["status"] not in {"consistent", "concern", "unknown"}:
            raise ValueError("Invalid integrated cross-task status")
        text(item["detail"], "cross-task detail")
        string_list(item["task_ids"], "cross-task task IDs")
        string_list(item["evidence_ids"], "cross-task evidence IDs")
        if not set(item["task_ids"]).issubset(tasks) or not set(
            item["evidence_ids"]
        ).issubset(evidence):
            raise ValueError(
                "Cross-task check cites an unknown task or evidence record"
            )
        if item["status"] == "consistent" and not item["evidence_ids"]:
            raise ValueError("Consistent cross-task check requires evidence")
        by_domain[item["domain"]] = item
        concern |= item["status"] == "concern"
        unknown |= item["status"] == "unknown"
    if set(by_domain) != set(CROSS_TASK_DOMAINS):
        raise ValueError("Integrated assessment is missing a cross-task domain")

    assumptions = assessment["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > 200:
        raise ValueError("Integrated assumptions must be a bounded list")
    for item in assumptions:
        if not isinstance(item, dict) or set(item) != {
            "statement",
            "status",
            "task_ids",
            "evidence_ids",
        }:
            raise ValueError("Invalid integrated assumption")
        text(item["statement"], "assumption")
        if item["status"] not in {"consistent", "concern", "unknown"}:
            raise ValueError("Invalid integrated assumption status")
        string_list(item["task_ids"], "assumption task IDs", nonempty=True)
        string_list(item["evidence_ids"], "assumption evidence IDs")
        if not set(item["task_ids"]).issubset(tasks) or not set(
            item["evidence_ids"]
        ).issubset(evidence):
            raise ValueError("Integrated assumption cites an unknown task or evidence")
        concern |= item["status"] == "concern"
        unknown |= item["status"] == "unknown"

    for field in ("limitations", "unknowns", "residual_risks"):
        string_list(assessment[field], field)
    incomplete = any(
        item["state"] != "succeeded" or not item["assessment"] for item in task_outcomes
    )
    unknown |= bool(assessment["unknowns"]) or incomplete
    concern |= bool(assessment["residual_risks"])
    concern |= requirement_issue or acceptance_issue
    expected_status = (
        "insufficient_information" if unknown else "concerns" if concern else "clear"
    )
    rank = {"clear": 0, "concerns": 1, "insufficient_information": 2}
    if rank[assessment["status"]] < rank[expected_status]:
        raise ValueError("Integrated assessment status understates identified concerns")
    return deepcopy(assessment)


def assessment_digest_payload(record: dict) -> dict:
    return {key: value for key, value in record.items() if key != "assessment_digest"}


__all__ = [
    "ASSESSMENT_SCHEMA_VERSION",
    "CROSS_TASK_DOMAINS",
    "assessment_digest_payload",
    "validate_integrated_assessment",
]
