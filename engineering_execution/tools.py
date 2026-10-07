"""Allowlisted deterministic review tools. No shell, filesystem or model calls."""

from __future__ import annotations

import difflib
from typing import Any

TOOLS = {
    "compare_revision": "1",
    "validate_traceability": "1",
    "generate_review_pack": "1",
    "validate_proposal_readiness": "1",
    "external_solver": "1",
    "fem_linear_static": "1",
}
MAX_TEXT = 100_000


def validate_parameters(tool: str, parameters: dict[str, Any]) -> None:
    if tool not in TOOLS:
        raise ValueError("Unknown execution tool")
    if not isinstance(parameters, dict):
        raise ValueError("Parameters must be an object")
    if tool == "external_solver":
        if (
            set(parameters) != {"solver_id", "quantities", "source_evidence_ids"}
            or not isinstance(parameters["solver_id"], str)
            or not 1 <= len(parameters["solver_id"]) <= 128
            or not isinstance(parameters["quantities"], dict)
            or not 1 <= len(parameters["quantities"]) <= 50
            or not isinstance(parameters["source_evidence_ids"], dict)
            or set(parameters["quantities"]) != set(parameters["source_evidence_ids"])
        ):
            raise ValueError("Invalid external solver parameters")
        return
    if tool == "fem_linear_static":
        from .fem import validate_fem_job

        if set(parameters) != {"job"} or not isinstance(parameters["job"], dict):
            raise ValueError("FEM task requires one declarative job")
        validate_fem_job(parameters["job"])
        return
    expected = (
        {"base_evidence_id", "head_evidence_id"}
        if tool == "compare_revision"
        else {"submission_id"}
        if tool == "validate_proposal_readiness"
        else set()
    )
    if set(parameters) != expected:
        raise ValueError("Invalid parameters for this tool")
    if any(
        not isinstance(v, str) or not v.strip() or len(v) > 512
        for v in parameters.values()
    ):
        raise ValueError("Identifiers must be bounded nonblank strings")


def run_tool(
    tool: str, snapshot: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    validate_parameters(tool, parameters)
    if tool in {"external_solver", "fem_linear_static"}:
        raise ValueError(
            "External solver requires an explicit validated host catalogue"
        )
    evidence = [n for n in snapshot["records"] if n["node_type"] == "evidence"]
    if tool == "validate_proposal_readiness":
        return _validate_proposal_readiness(snapshot, parameters["submission_id"])
    if tool == "compare_revision":
        by_id = {n["node_id"]: n for n in evidence}
        base = by_id[parameters["base_evidence_id"]]
        head = by_id[parameters["head_evidence_id"]]
        texts = [n["attributes"].get("text") for n in (base, head)]
        if any(not isinstance(s, str) or not s.strip() for s in texts):
            raise ValueError(
                "UNKNOWN / INSUFFICIENT INFORMATION: source text is missing"
            )
        if any(len(s) > MAX_TEXT for s in texts):
            raise ValueError("Source text exceeds the bounded comparison limit")
        changes = list(difflib.ndiff(texts[0].splitlines(), texts[1].splitlines()))
        return {
            "base_evidence_id": base["node_id"],
            "head_evidence_id": head["node_id"],
            "base_revision": base["attributes"].get("revision"),
            "head_revision": head["attributes"].get("revision"),
            "removed_lines": [s[2:] for s in changes if s.startswith("- ")],
            "added_lines": [s[2:] for s in changes if s.startswith("+ ")],
            "warnings": [
                "Text differences do not establish engineering adequacy or source authority."
            ],
        }
    if tool == "validate_traceability":
        return {
            "evidence_count": len(evidence),
            "missing_text": [
                n["node_id"] for n in evidence if not n["attributes"].get("text")
            ],
            "missing_locators": [
                n["node_id"]
                for n in evidence
                if not any(
                    n["attributes"].get(k) for k in ("page", "section", "locator")
                )
            ],
            "requirement_status": "linked"
            if snapshot.get(
                "source_context_requirement_ids", snapshot["issue"]["requirement_ids"]
            )
            else "UNKNOWN / INSUFFICIENT INFORMATION: no requirements linked",
            "warnings": [
                "Traceability checks confirm recorded links, not technical compliance."
            ],
        }
    return {
        "schema_version": 1,
        "issue": snapshot["issue"],
        "records": snapshot["records"],
        "human_review_required": True,
        "warnings": [
            "Unverified review pack; technical conclusions require human review."
        ],
    }


def _validate_proposal_readiness(
    snapshot: dict[str, Any], submission_id: str
) -> dict[str, Any]:
    """Compare a Registry-bound proposal snapshot without making an adequacy decision."""
    from engineering_registry.service import digest

    records = snapshot.get("records", [])
    records = records if isinstance(records, list) and len(records) <= 200 else []
    nodes = [record for record in records if isinstance(record, dict)]
    by_id: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for record in nodes:
        node_id = record.get("node_id")
        if isinstance(node_id, str):
            if node_id in by_id:
                duplicates.add(node_id)
            by_id[node_id] = record
    submission_matches = [
        item
        for item in nodes
        if item.get("node_id") == submission_id
        and item.get("node_type") == "submission"
    ]
    warnings = ["This deterministic check does not establish contractual acceptance."]
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("issue"), dict):
        issue: dict[str, Any] = {}
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: proposal issue context is missing."
        )
    else:
        issue = snapshot["issue"]
    if len(submission_matches) != 1:
        submission: dict[str, Any] = {}
        attrs: dict[str, Any] = {}
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: exactly one linked submission record is required."
        )
    else:
        submission = submission_matches[0]
        attrs = (
            submission.get("attributes", {})
            if isinstance(submission.get("attributes"), dict)
            else {}
        )
    integrity_ok = len(submission_matches) == 1 and submission_id not in duplicates
    if (
        len(str(snapshot)) > 500_000
        or not isinstance(snapshot.get("records"), list)
        or len(snapshot.get("records", [])) > 200
    ):
        integrity_ok = False
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: proposal snapshot exceeds its bounded record or size limit."
        )
    if attrs:
        stored_content_digest = attrs.get("content_digest")
        content = {
            key: value for key, value in attrs.items() if key != "content_digest"
        }
        if not isinstance(
            stored_content_digest, str
        ) or stored_content_digest != digest(content):
            integrity_ok = False
            warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: immutable submission content digest does not match."
            )

    def rows(name: str) -> list[dict[str, Any]]:
        value = attrs.get(name, [])
        return (
            [row for row in value if isinstance(row, dict)]
            if isinstance(value, list)
            else []
        )

    protected = rows("protected_sections")
    matrix = rows("scope_matrix")
    issues_evidence = (
        issue.get("evidence_ids", [])
        if isinstance(issue.get("evidence_ids", []), list)
        else []
    )
    requirements = attrs.get("source_requirement_ids", [])
    requirements = requirements if isinstance(requirements, list) else []
    expected_requirement_ids = snapshot.get("source_context_requirement_ids")
    if not isinstance(expected_requirement_ids, list):
        expected_requirement_ids = issue.get("requirement_ids", [])
    expected_requirement_ids = sorted(
        set(r for r in expected_requirement_ids if isinstance(r, str))
    )
    row_requirement_ids: list[str] = [
        req_id
        for row in matrix
        if isinstance((req_id := row.get("requirement_id")), str)
    ]
    row_counts = {
        req_id: row_requirement_ids.count(req_id) for req_id in set(row_requirement_ids)
    }
    duplicate_requirement_ids = sorted(
        req_id for req_id, count in row_counts.items() if count > 1
    )
    represented = sorted(set(row_requirement_ids))
    omitted = sorted(set(expected_requirement_ids) - set(row_requirement_ids))
    unexpected = sorted(set(row_requirement_ids) - set(expected_requirement_ids))
    accepted_nodes: list[dict[str, Any] | None] = [
        by_id.get(req_id) for req_id in expected_requirement_ids
    ]
    accepted_records_valid = all(
        node is not None
        and node.get("node_type") == "requirement"
        and isinstance(node.get("attributes"), dict)
        and node["attributes"].get("review_status") == "reviewed"
        and bool(node["attributes"].get("review_decision_id"))
        for node in accepted_nodes
    )
    exact_coverage = (
        bool(expected_requirement_ids)
        and set(requirements) == set(expected_requirement_ids)
        and not duplicate_requirement_ids
        and not omitted
        and not unexpected
        and accepted_records_valid
    )
    requirement_coverage = {
        "accepted_requirement_ids": expected_requirement_ids,
        "represented_requirement_ids": represented,
        "omitted_requirement_ids": omitted,
        "duplicate_requirement_ids": duplicate_requirement_ids,
        "unexpected_requirement_ids": unexpected,
        "exact_coverage": exact_coverage,
    }
    if not accepted_records_valid:
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: an accepted same-project requirement record is missing or unreviewed."
        )
    if not exact_coverage:
        warnings.append(
            "Requirement-to-scope matrix does not exactly cover the linked accepted requirements."
        )

    requirement_evidence: set[str] = set()
    for requirement_id in requirements:
        requirement_node = by_id.get(requirement_id)
        if requirement_node and isinstance(requirement_node.get("attributes"), dict):
            values = requirement_node["attributes"].get("source_evidence_ids", [])
            if isinstance(values, list):
                requirement_evidence.update(
                    value for value in values if isinstance(value, str)
                )
    derived_source_ids: set[str] = {
        item for item in issues_evidence if isinstance(item, str)
    } | requirement_evidence
    derived_source_ids.update(
        value
        for row in protected
        for value in (row.get("template_evidence_id"), row.get("candidate_evidence_id"))
        if isinstance(value, str)
    )
    derived_source_ids.update(
        value
        for row in matrix
        for value in row.get("proposal_evidence_ids", [])
        if isinstance(value, str)
    )
    declared_source_ids = attrs.get("source_evidence_ids", [])
    declared_source_ids = (
        set(declared_source_ids) if isinstance(declared_source_ids, list) else set()
    )
    if derived_source_ids != declared_source_ids:
        integrity_ok = False
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: declared source set differs from linked proposal references."
        )
    controls = snapshot.get("source_control", [])
    reviews = snapshot.get("evidence_review", [])
    control_by_id = (
        {item.get("evidence_id"): item for item in controls if isinstance(item, dict)}
        if isinstance(controls, list)
        else {}
    )
    review_by_id = (
        {item.get("evidence_id"): item for item in reviews if isinstance(item, dict)}
        if isinstance(reviews, list)
        else {}
    )
    source_checks = []
    for evidence_id in sorted(declared_source_ids | derived_source_ids):
        source_record = by_id.get(evidence_id)
        evidence_attrs = (
            source_record.get("attributes", {})
            if source_record and isinstance(source_record.get("attributes"), dict)
            else {}
        )
        control = control_by_id.get(evidence_id, {})
        review = review_by_id.get(evidence_id, {})
        text_value = evidence_attrs.get("text")
        has_text = (
            isinstance(text_value, str)
            and bool(text_value.strip())
            and len(text_value) <= MAX_TEXT
        )
        has_locator = any(
            evidence_attrs.get(key) not in (None, "", [])
            for key in ("page", "section", "locator")
        )
        authority = control.get("status", "unverified")
        authority_source_id = control.get("source_id")
        authority_source = (
            by_id.get(authority_source_id)
            if isinstance(authority_source_id, str)
            else None
        )
        valid_authority_source = (
            authority_source is not None
            and authority_source.get("node_type") in {"document", "document_revision"}
            and authority_source.get("project_id") == issue.get("project_id")
        )
        review_status = review.get(
            "status",
            "unreviewed_text_missing" if not has_text else "unreviewed_text_available",
        )
        valid_type = (
            source_record is not None and source_record.get("node_type") == "evidence"
        )
        passed = (
            valid_type
            and source_record is not None
            and source_record.get("project_id") == issue.get("project_id")
            and has_text
            and has_locator
            and authority in {"current", "governing"}
            and valid_authority_source
            and review_status == "visually_reviewed"
            and evidence_attrs.get("governing_status") != "superseded"
            and evidence_id not in duplicates
        )
        check_warnings = []
        if not valid_type:
            check_warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source evidence is missing or has the wrong record type."
            )
        if not has_text or not has_locator:
            check_warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source text or locator is missing."
            )
        if authority not in {"current", "governing"}:
            check_warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source authority is not human-confirmed as current or governing."
            )
        if not valid_authority_source:
            check_warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source authority does not point to a same-project controlled document revision."
            )
        if review_status != "visually_reviewed":
            check_warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: source evidence has not passed human evidence review."
            )
        if evidence_attrs.get("governing_status") == "superseded":
            check_warnings.append("Source evidence is superseded.")
        warnings.extend(check_warnings)
        source_checks.append(
            {
                "evidence_id": evidence_id,
                "authority_status": authority,
                "evidence_review_status": review_status,
                "has_text": has_text,
                "has_locator": has_locator,
                "passed": passed,
                "warnings": check_warnings,
            }
        )

    def belongs_to_revision(evidence_id: str, revision_id: str) -> bool:
        evidence_node = by_id.get(evidence_id)
        revision_node = by_id.get(revision_id)
        if (
            not evidence_node
            or not revision_node
            or evidence_node.get("node_type") != "evidence"
            or revision_node.get("node_type") != "document_revision"
        ):
            return False
        evidence_attributes = evidence_node.get("attributes", {})
        revision_attributes = revision_node.get("attributes", {})
        return bool(
            evidence_attributes.get("document_id")
            == revision_attributes.get("document_id")
            and evidence_attributes.get("revision")
            == revision_attributes.get("revision")
        )

    protected_section_checks = []
    for pair in protected:
        template_id = pair.get("template_evidence_id")
        candidate_id = pair.get("candidate_evidence_id")
        template_node = by_id.get(template_id) if isinstance(template_id, str) else None
        candidate_node = (
            by_id.get(candidate_id) if isinstance(candidate_id, str) else None
        )
        template_text = (
            template_node.get("attributes", {}).get("text") if template_node else None
        )
        candidate_text = (
            candidate_node.get("attributes", {}).get("text") if candidate_node else None
        )
        known_text = all(
            isinstance(source_text, str)
            and source_text.strip()
            and len(source_text) <= MAX_TEXT
            for source_text in (template_text, candidate_text)
        )
        if known_text:
            assert isinstance(template_text, str) and isinstance(candidate_text, str)
            diff = list(
                difflib.ndiff(template_text.splitlines(), candidate_text.splitlines())
            )
            removed = [line[2:] for line in diff if line.startswith("- ")][:50]
            added = [line[2:] for line in diff if line.startswith("+ ")][:50]
            unchanged = not removed and not added
        else:
            removed, added, unchanged = [], [], False
            warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: protected section evidence or text is missing."
            )
        revision_ok = (
            (
                belongs_to_revision(template_id, attrs.get("template_revision_id", ""))
                and belongs_to_revision(
                    candidate_id, attrs.get("candidate_revision_id", "")
                )
            )
            if isinstance(template_id, str) and isinstance(candidate_id, str)
            else False
        )
        if not revision_ok:
            warnings.append(
                "UNKNOWN / INSUFFICIENT INFORMATION: protected evidence does not match the declared template/candidate revisions."
            )
        protected_section_checks.append(
            {
                "label": pair.get("label"),
                "template_evidence_id": template_id,
                "candidate_evidence_id": candidate_id,
                "unchanged": unchanged and revision_ok,
                "removed_lines": removed,
                "added_lines": added,
            }
        )

    unresolved_rows = sorted(
        matrix_id
        for row in matrix
        if row.get("disposition") == "unresolved"
        and isinstance((matrix_id := row.get("matrix_id")), str)
    )
    revisions_present = all(
        isinstance(attrs.get(key), str)
        and (node := by_id.get(attrs[key])) is not None
        and node.get("node_type") == "document_revision"
        and node.get("project_id") == issue.get("project_id")
        for key in ("candidate_revision_id", "template_revision_id")
    )
    if not revisions_present:
        integrity_ok = False
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: candidate or template revision is missing or outside the project snapshot."
        )
    context_digests = snapshot.get("submission_context_digests", {})
    submission_digest = (
        context_digests.get(submission_id)
        if isinstance(context_digests, dict)
        else None
    )
    if not isinstance(submission_digest, str) or not submission_digest:
        integrity_ok = False
        warnings.append(
            "UNKNOWN / INSUFFICIENT INFORMATION: current Registry submission context digest is missing."
        )
    ready = (
        integrity_ok
        and revisions_present
        and accepted_records_valid
        and exact_coverage
        and bool(source_checks)
        and all(check["passed"] for check in source_checks)
        and bool(protected_section_checks)
        and all(check["unchanged"] for check in protected_section_checks)
        and not unresolved_rows
    )
    return {
        "submission_id": submission_id,
        "submission_digest": submission_digest,
        "candidate_revision_id": attrs.get("candidate_revision_id"),
        "template_revision_id": attrs.get("template_revision_id"),
        "source_checks": source_checks,
        "protected_section_checks": protected_section_checks,
        "requirement_coverage": requirement_coverage,
        "unresolved_matrix_rows": unresolved_rows,
        "ready_for_human_review": ready,
        "warnings": list(dict.fromkeys(warnings)),
    }
