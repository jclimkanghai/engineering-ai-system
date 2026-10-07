"""Execution accepts the Document AI task contract without owning its reasoning."""

from engineering_registry.service import text


def validate_brain_plan(plan: dict) -> None:
    fields = {
        "schema_version",
        "producer",
        "objective",
        "scope",
        "expected_outputs",
        "acceptance_criteria",
        "evidence_ids",
        "requirement_ids",
        "lesson_ids",
        "unknowns",
        "analysis_summary",
    }
    proposal_fields = {"submission_id", "submission_digest", "proposal_readiness"}
    if (
        not isinstance(plan, dict)
        or not fields.issubset(plan)
        or set(plan)
        - fields
        - {
            "analysis",
            "source_control",
            "evidence_review",
            "client_mandate",
            "client_project_brief",
            "alignment_review_required",
            "solver_binding",
            "fem_binding",
            "lesson_limitations",
            "knowledge_conflicts",
            "organizational_lesson_ids",
            "execution_class",
            "run_id",
            "execution_policy_id",
            "execution_policy_digest",
        }
        - proposal_fields
        or plan["schema_version"] != 1
    ):
        raise ValueError("Invalid brain task contract")
    if bool(proposal_fields & set(plan)) and not proposal_fields.issubset(plan):
        raise ValueError("Proposal task contract requires its full submission binding")
    if plan["producer"] != "engineering_document_ai":
        raise ValueError("Task structuring belongs to Document AI")
    for key in ("objective", "scope", "analysis_summary"):
        text(plan[key], key)
    for key in (
        "expected_outputs",
        "acceptance_criteria",
        "evidence_ids",
        "requirement_ids",
        "lesson_ids",
        "unknowns",
    ):
        values = plan[key]
        if not isinstance(values, list) or len(values) > 200:
            raise ValueError("Brain task lists must be bounded")
        for value in values:
            text(value, key)
        if len(set(values)) != len(values):
            raise ValueError("Brain task lists must not contain duplicates")
    if not plan["expected_outputs"] or not plan["acceptance_criteria"]:
        raise ValueError("Brain task requires outputs and acceptance criteria")
    if "lesson_limitations" in plan:
        limits = plan["lesson_limitations"]
        if not isinstance(limits, list) or not limits or len(limits) > 100:
            raise ValueError("Lesson limitations must be a bounded list")
        for limit in limits:
            text(limit, "lesson_limitations")
    if "organizational_lesson_ids" in plan:
        ids = plan["organizational_lesson_ids"]
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) != len(set(ids))
            or not set(ids).issubset(plan["lesson_ids"])
        ):
            raise ValueError("Invalid organisational lesson binding")
    if "knowledge_conflicts" in plan:
        conflicts = plan["knowledge_conflicts"]
        if not isinstance(conflicts, list) or not conflicts or len(conflicts) > 100:
            raise ValueError("Knowledge conflicts must be a bounded list")
        for conflict in conflicts:
            if not isinstance(conflict, dict) or set(conflict) != {
                "lesson_id",
                "knowledge_id",
                "requirement_ids",
                "evidence_ids",
                "resolution",
                "lead_action",
                "human_client_decision_required",
                "message",
                "conflict_interpretation",
                "requirement_concern",
                "early_warning",
            }:
                raise ValueError("Invalid knowledge conflict record")
            for key in ("lesson_id", "knowledge_id", "lead_action", "message"):
                text(conflict[key], key)
            if conflict["resolution"] not in {
                "compliant_alternative",
                "deviation_proposed",
                "unresolved",
            } or conflict["human_client_decision_required"] is not (
                conflict["resolution"] == "deviation_proposed"
            ):
                raise ValueError("Invalid knowledge conflict authority state")
            if conflict["conflict_interpretation"] not in {
                "project_requirement_controls",
                "possible_requirement_problem",
            } or conflict["early_warning"] is not (
                conflict["conflict_interpretation"] == "possible_requirement_problem"
            ):
                raise ValueError("Invalid knowledge conflict interpretation")
            if conflict["early_warning"]:
                text(conflict["requirement_concern"], "requirement_concern")
            elif conflict["requirement_concern"] is not None:
                raise ValueError("Requirement concern needs an early warning")
            for key in ("requirement_ids", "evidence_ids"):
                values = conflict[key]
                if (
                    not isinstance(values, list)
                    or not values
                    or not set(values).issubset(plan[key])
                ):
                    raise ValueError("Knowledge conflict requires current citations")
    if "client_mandate" in plan:
        mandate = plan["client_mandate"]
        if not isinstance(mandate, dict) or not mandate.get("mandate_digest"):
            raise ValueError("Invalid client mandate binding")
    if "alignment_review_required" in plan:
        if (
            plan["alignment_review_required"] is not True
            or "client_mandate" not in plan
        ):
            raise ValueError("Alignment review requires a retained client mandate")
        if "client_project_brief" not in plan:
            raise ValueError("Alignment review requires the client project brief")
    if "analysis" in plan:
        from engineering_registry.analysis import validate_analysis

        validate_analysis(
            plan["analysis"],
            "execution_planning",
            plan["evidence_ids"],
            plan["requirement_ids"],
        )
        if "engineering_review" not in plan["acceptance_criteria"]:
            raise ValueError("Reasoning task requires engineering review criterion")
    if proposal_fields.issubset(plan):
        text(plan["submission_id"], "submission_id")
        text(plan["submission_digest"], "submission_digest")
        binding = plan["proposal_readiness"]
        required_binding = {
            "candidate_revision_id",
            "template_revision_id",
            "source_requirement_ids",
            "source_evidence_ids",
        }
        if not isinstance(binding, dict) or set(binding) != required_binding:
            raise ValueError("Invalid proposal readiness task binding")
        for field in ("candidate_revision_id", "template_revision_id"):
            text(binding[field], field)
        for field in ("source_requirement_ids", "source_evidence_ids"):
            values = binding[field]
            if not isinstance(values, list) or not values or len(values) > 200:
                raise ValueError(
                    "Proposal task source bindings must be bounded nonempty lists"
                )
            for value in values:
                text(value, field)
            if len(values) != len(set(values)):
                raise ValueError("Proposal task source bindings must be unique")
        if plan["acceptance_criteria"] != ["proposal_readiness"]:
            raise ValueError(
                "Proposal task must use the proposal readiness acceptance criterion"
            )
        if plan["requirement_ids"] != binding["source_requirement_ids"]:
            raise ValueError(
                "Proposal task requirements must match its submission binding"
            )
        if plan["evidence_ids"] != binding["source_evidence_ids"]:
            raise ValueError("Proposal task evidence must match its submission binding")
