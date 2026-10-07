"""Trusted local human review desk, bound to loopback and explicit host grants."""

from __future__ import annotations

import hmac
import html
import json
import secrets
import uuid
import webbrowser
from collections.abc import Callable
from contextlib import ExitStack
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from engineering_document_ai_brain.errors import AnalysisUnavailable
from engineering_execution import ExecutionService

from .models import NodeType
from .service import Principal, RegistryService, digest, text
from .store import GraphIntegrityError, SQLiteGraphStore


def apply_action(
    registry: RegistryService,
    fields: dict[str, str],
    *,
    workflow_factory: Callable[[RegistryService], Any] | None = None,
    organization_registry: RegistryService | None = None,
) -> str:
    project = fields.get("project_id", "")
    registry.check_access(project, write=True, human=True)
    operation = fields.get("operation")
    record_id = text(fields.get("record_id"), "record_id")
    raw_rationale = fields.get("rationale", "")
    if not isinstance(raw_rationale, str):
        raise ValueError("rationale must be text")
    rationale = raw_rationale.strip()
    if operation not in {
        "review_proposal",
        "transition",
        "prepare_submission_task",
        "record_client_brief",
        "record_client_mandate",
    }:
        rationale = text(rationale, "rationale")

    def id_list(value: str) -> list[str]:
        return list(
            dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
        )

    if operation == "review_finding":
        registry.review_finding(
            project,
            record_id,
            fields.get("digest", ""),
            fields.get("statement", ""),
            fields.get("classification", ""),
            fields.get("severity", ""),
            rationale,
        )
        return "Human finding amendment recorded. Original evidence remains retained; workflow gates still apply."

    if operation == "record_client_brief":
        from .client_brief import SECTIONS

        definition_sections = {}
        evidence_ids = set()
        unknowns = []
        for section in sorted(SECTIONS):
            statement = fields.get("brief_" + section, "").strip()
            citations = id_list(fields.get("evidence_" + section, ""))
            if statement:
                definition_sections[section] = [
                    {
                        "statement_id": section + ":" + digest(statement)[:12],
                        "text": statement,
                        "basis": "explicit" if citations else "unknown",
                        "evidence_ids": citations,
                    }
                ]
                evidence_ids.update(citations)
                if not citations:
                    unknowns.append(
                        section + ": statement supplied without source evidence"
                    )
            else:
                definition_sections[section] = [
                    {
                        "statement_id": section + ":not-recorded",
                        "text": "Not provided by the project owner.",
                        "basis": "unknown",
                        "evidence_ids": [],
                    }
                ]
                unknowns.append(section + ": not provided")
        registry.record_client_project_brief(
            project,
            text(fields.get("brief_id"), "brief_id"),
            {
                "title": text(fields.get("brief_title"), "brief_title"),
                "sections": definition_sections,
                "evidence_ids": sorted(evidence_ids),
                "unknowns": unknowns,
            },
        )
        return "Human-approved project brief recorded. Missing sections and uncited statements remain explicit unknowns."

    if operation == "record_client_mandate":
        acceptance = [
            line.strip()
            for line in fields.get("acceptance_criteria", "").splitlines()
            if line.strip()
        ]
        tools = id_list(fields.get("allowed_tools", ""))
        registry.record_client_mandate(
            project,
            text(fields.get("mandate_id"), "mandate_id"),
            record_id,
            {
                "objective": text(fields.get("objective"), "objective"),
                "scope": text(fields.get("scope"), "scope"),
                "acceptance_criteria": acceptance,
                "evidence_ids": id_list(fields.get("mandate_evidence_ids", "")),
                "allowed_tools": tools,
                "risk_level": fields.get("risk_level", "unknown"),
                "importance_level": fields.get("importance_level", "unknown"),
                "simple": fields.get("simple") == "yes",
                "reversible": fields.get("reversible") == "yes",
                "consequence_domains": id_list(fields.get("consequence_domains", "")),
                "unknowns": id_list(fields.get("mandate_unknowns", "")),
            },
        )
        return (
            "Human-approved issue mandate recorded against the current project sources."
        )

    def workflow():
        from engineering_document_ai_brain import DocumentAIWorkflow

        return (
            workflow_factory(registry)
            if workflow_factory
            else DocumentAIWorkflow(registry)
        )

    if operation in {"validate_candidate", "decide_run", "execute_run_task"}:
        from engineering_ai_system.project_run import ProjectRunService

        candidate = (
            registry.get_control_record(project, "knowledge_candidates", record_id)
            if operation == "validate_candidate"
            else None
        )
        run_id = (
            candidate["run_id"]
            if candidate
            else fields.get("run_id", "")
            if operation == "execute_run_task"
            else record_id
        )
        run = registry.get_control_record(project, "project_runs", run_id)
        if not run:
            raise ValueError("Unknown project run")
        service = ProjectRunService(
            registry,
            workflow(),
            execution_mode=run.get("execution_mode", "GOVERNED"),
            organization_registry=organization_registry,
        )
        if operation == "execute_run_task":
            task = service.brain.execution.get_task(project, record_id)
            if fields.get("digest") != task["task_digest"]:
                raise ValueError("Task changed; refresh before execution")
            service.execute_task(project, run_id, record_id)
            return "V2 run task completed or retained diagnostics. Review the output and assessment before deciding."
        if operation == "validate_candidate":
            if fields.get("accept") not in {"yes", "no"}:
                raise ValueError("Choose approve or reject")
            if fields["accept"] == "yes" and organization_registry is None:
                raise ValueError(
                    "A separate organisational Registry must be configured before promotion"
                )
            service.validate_candidate(
                registry.principal,
                project,
                record_id,
                fields.get("digest", ""),
                fields["accept"] == "yes",
                rationale,
                organization_id=fields.get("organization_id"),
                organization_lesson_id=fields.get("organization_lesson_id"),
            )
            return "Human knowledge validation recorded. Approved knowledge is retained in the configured organisational Registry."
        if fields.get("digest") != digest(service.get_status(project, run_id)):
            raise ValueError("Project run changed; refresh before deciding")
        service.decide(
            registry.principal,
            project,
            run_id,
            fields.get("disposition", ""),
            id_list(fields.get("evidence_ids", "")),
            rationale,
        )
        return "Human project outcome decision recorded."

    if operation in {
        "specialist_plan_decision",
        "confirm_specialist_plan_conditions",
        "approve_critical_plan",
    }:
        from engineering_ai_system.project_run import ProjectRunService

        run = registry.get_control_record(project, "project_runs", record_id)
        if not run or fields.get("digest") != run.get("plan_digest"):
            raise ValueError("Project run plan changed; refresh before deciding")
        service = ProjectRunService(
            registry,
            workflow(),
            execution_mode=run.get("execution_mode", "GOVERNED"),
        )
        if operation == "specialist_plan_decision":
            conditions = [
                item.strip()
                for item in fields.get("conditions", "").splitlines()
                if item.strip()
            ]
            service.decide_specialist_plan(
                registry.principal,
                project,
                record_id,
                fields.get("digest", ""),
                fields.get("disposition", ""),
                rationale,
                conditions=conditions,
            )
        elif operation == "confirm_specialist_plan_conditions":
            condition_ids = [
                item.strip()
                for item in fields.get("condition_ids", "").split(",")
                if item.strip()
            ]
            service.confirm_specialist_plan_conditions(
                registry.principal,
                project,
                record_id,
                fields.get("digest", ""),
                condition_ids,
                rationale,
            )
        else:
            service.approve_critical_plan(
                registry.principal,
                project,
                record_id,
                fields.get("digest", ""),
                rationale,
            )
        return "Human ProjectRun plan decision recorded against the exact plan digest."

    if operation == "prepare_task":
        with registry.store.transaction():
            current = registry.issue_context(project, record_id)
            if fields.get("digest") != current["output_digest"]:
                raise ValueError(
                    "Issue changed since display. Refresh before preparing a task."
                )
        tool = fields.get("tool", "")
        parameters = (
            {
                "base_evidence_id": fields.get("base", ""),
                "head_evidence_id": fields.get("head", ""),
            }
            if tool == "compare_revision"
            else {}
        )
        workflow().structure_task(
            project, uuid.uuid4().hex, record_id, tool, parameters
        )
        return "Document AI task structure and proposed analysis recorded for authorization."
    if operation == "prepare_submission_task":
        submission_id = text(fields.get("submission_id"), "submission_id")
        current = registry.submission_context(project, submission_id)
        if fields.get("digest") != current["submission_digest"]:
            raise ValueError(
                "Proposal source context changed since display. Refresh before structuring a task."
            )
        if current["submission"]["attributes"]["issue_id"] != record_id:
            raise ValueError("Submission is not linked to the displayed proposal issue")
        workflow().structure_submission_task(
            project, uuid.uuid4().hex, record_id, submission_id
        )
        return "Document AI proposal readiness task recorded for human authorization."
    if operation == "assess_result":
        result = registry.get_record(project, record_id)
        if fields.get("digest") != digest(result):
            raise ValueError("Result changed since display")
        workflow().assess_result(project, result["attributes"]["task"], record_id)
        return "Document AI output assessment recorded for final human review."
    if operation == "execute":
        # The executor must commit its running claim before invoking the tool.
        with registry.store.transaction():
            ExecutionService(registry).get_task(project, record_id)
            registry._event(
                project, record_id, "task_run_requested", rationale=rationale
            )
        outcome = workflow().execute_and_assess(project, record_id)
        result = outcome["result"]
        if outcome.get("decision"):
            return f"V2 output, technical assessment and alignment review recorded. Delegated AI decision: {outcome['decision']['attributes']['disposition']}. Human verification remains separate."
        return (
            "Task failed; diagnostics were retained."
            if result.get("state") == "failed"
            else "V2 output and Document AI assessment recorded. Review both before your final decision."
        )
    authority_execution = ExecutionService(registry)
    if operation in {"authorize", "retry"} and workflow_factory:
        authority_execution = ExecutionService(
            registry, solver_catalog=workflow().execution.solver_catalog
        )
    with registry.store.transaction():
        if operation == "review_evidence":
            registry.review_evidence(
                project,
                record_id,
                fields.get("digest", ""),
                fields.get("status", ""),
                rationale,
            )
        elif operation == "review_requirement_candidate":
            registry.review_requirement_candidate(
                project,
                record_id,
                fields.get("digest", ""),
                fields.get("accept") == "yes",
                rationale,
            )
        elif operation == "document_control":
            registry.review_document_control(
                project,
                record_id,
                fields.get("digest", ""),
                fields.get("authority", ""),
                [
                    e.strip()
                    for e in fields.get("evidence_ids", "").split(",")
                    if e.strip()
                ],
                rationale,
            )
        elif operation == "transition":
            current = registry.issue_context(project, record_id)
            if fields.get("digest") != current["output_digest"]:
                raise ValueError(
                    "Issue changed since display. Refresh and review the latest information."
                )
            status = fields.get("status", "")
            if not rationale and status != "closed":
                raise ValueError("rationale must not be blank")
            evidence_ids = (
                current["issue"]["evidence_ids"] if status == "closed" else []
            )
            proposal = registry.propose_transition(
                project,
                record_id,
                status,
                rationale or "Human closure requested without a recorded reason.",
                evidence_ids,
            )
            registry.review_proposal(
                project,
                proposal["proposal_id"],
                proposal["proposal_digest"],
                accept=True,
                rationale=rationale,
            )
        elif operation == "review_proposal":
            registry.review_proposal(
                project,
                record_id,
                fields.get("digest", ""),
                accept=fields.get("accept") == "yes",
                rationale=rationale,
            )
        elif operation == "review_task_decision":
            registry.review_task_decision(
                project,
                record_id,
                fields.get("digest", ""),
                accept=fields.get("accept") == "yes",
                evidence_ids=[
                    value.strip()
                    for value in fields.get("evidence_ids", "").split(",")
                    if value.strip()
                ],
                rationale=rationale,
                formal_authority_reference=fields.get("formal_authority_reference")
                or None,
            )
        elif operation == "promote_organizational_lesson":
            organization_id = project.removeprefix("ORG:")
            source_lessons = []
            for reference in fields.get("source_lessons", "").split(","):
                reference = reference.strip()
                if not reference:
                    continue
                source_project, separator, source_lesson = reference.partition(":")
                if not separator or not source_lesson.strip():
                    raise ValueError("Source lessons must use project:lesson IDs")
                source_lessons.append((source_project.strip(), source_lesson.strip()))
            registry.promote_organizational_lesson(
                organization_id,
                record_id,
                text(fields.get("title"), "title"),
                source_lessons,
                observation=fields.get("observation", ""),
                result=fields.get("result", ""),
                interpretation=fields.get("interpretation", ""),
                validated_statement=fields.get("validated_statement", ""),
                applicability=fields.get("applicability", ""),
                limitations=fields.get("limitations", ""),
                relevant_standards=[
                    value.strip()
                    for value in fields.get("relevant_standards", "").split(",")
                    if value.strip()
                ],
                review_due=fields.get("review_due", ""),
                rationale=rationale,
            )
        elif operation == "review_organizational_lesson":
            organization_id = project.removeprefix("ORG:")
            status = fields.get("status", "")
            source = registry.get_record(project, record_id)
            source_status = registry.store.get_record(
                project, "organizational_lesson_status", record_id
            )
            if fields.get("digest") != digest(
                {"lesson": source, "status": source_status}
            ):
                raise ValueError(
                    "Organizational lesson changed; refresh before reviewing"
                )
            registry.review_organizational_lesson(
                organization_id, record_id, fields.get("digest", ""), status, rationale
            )
        elif operation == "import_organizational_lesson":
            organization_id = text(fields.get("organization_id"), "organization_id")
            source_scope = "ORG:" + organization_id
            source = registry.get_record(source_scope, record_id)
            source_status = registry.store.get_record(
                source_scope, "organizational_lesson_status", record_id
            )
            if fields.get("digest") != digest(
                {"lesson": source, "status": source_status}
            ):
                raise ValueError(
                    "Organizational lesson changed; refresh before importing"
                )
            registry.import_organizational_lesson(
                project,
                organization_id,
                record_id,
                fields.get("import_id") or f"org-import:{organization_id}:{record_id}",
                rationale,
                digest({"lesson": source, "status": source_status}),
            )
        elif operation == "authorize":
            authority_execution.authorize_task(
                project, record_id, fields.get("digest", ""), rationale
            )
        elif operation == "cancel":
            ExecutionService(registry).cancel(project, record_id, rationale)
        elif operation == "retry":
            authority_execution.retry(project, record_id, rationale)
        elif operation == "recover":
            ExecutionService(registry).recover(project, record_id, rationale)
        elif operation in {"verify", "decide_result"}:
            result = registry.get_record(project, record_id)
            registry.decide_result(
                project,
                record_id,
                fields.get("digest", ""),
                fields.get("assessment_id", ""),
                fields.get("assessment_digest", ""),
                fields.get("disposition", "accept"),
                result["attributes"]["evidence_ids"],
                rationale,
            )
        else:
            raise ValueError("Unknown review operation")
    return "Recorded with your identity, rationale and the reviewed snapshot."


def render_page(
    registry: RegistryService,
    *,
    demo: bool,
    notice: str = "",
    evidence_offset: int = 0,
) -> str:
    def escaped(value) -> str:
        return html.escape(str(value), quote=True)

    def details(label, value):
        return (
            "<details><summary>"
            + escaped(label)
            + "</summary><pre>"
            + escaped(json.dumps(value, indent=2, ensure_ascii=False))
            + "</pre></details>"
        )

    def form(project, record, operation, label, snapshot="", extras=""):
        hidden = {
            "project_id": project,
            "record_id": record,
            "operation": operation,
            "digest": snapshot,
        }
        return (
            "<form method='post' action='/action'>"
            + "".join(
                "<input type='hidden' name='" + k + "' value='" + escaped(v) + "'>"
                for k, v in hidden.items()
            )
            + extras
            + (
                "<label>Reason for this action <input name='rationale' maxlength='2000' placeholder='Explain what you checked; blank is permitted only for closure without verified reasoning'></label>"
                "<button>" + escaped(label) + "</button></form>"
            )
        )

    def navigation(page, offset, label):
        links = []
        if offset:
            links.append(
                "<a href='?evidence_offset="
                + escaped(max(0, offset - 100))
                + "'>Previous "
                + escaped(label)
                + "</a>"
            )
        if page["next_offset"] is not None:
            links.append(
                "<a href='?evidence_offset="
                + escaped(page["next_offset"])
                + "'>Next "
                + escaped(label)
                + "</a>"
            )
        return "<p class='pagination'>" + " · ".join(links) + "</p>" if links else ""

    sections = []
    for project in sorted(registry.principal.project_ids):
        sections.append("<h2>Project " + escaped(project) + "</h2>")
        runs = registry.store.list_records(project, "project_runs")
        for run in runs:
            run_class = run.get("execution_class", "engineering")
            gates = (
                "Policy-bound V2 and Lead assessment"
                if run_class == "routine"
                else "Reviewer Gates 1 and 2 plus human plan and outcome decisions"
                if run_class == "critical"
                else "Reviewer Gates 1 and 2; decision by authority threshold"
            )
            approval = "approved" if run.get("human_plan_approval_id") else "pending"
            policy = (
                registry.store.get_record(
                    project, "execution_policy_status", run.get("policy_id")
                )
                if run.get("policy_id")
                else None
            )
            specialists = run.get("plan", {}).get("discipline_specialists", [])
            if len(specialists) > 3:
                if run_class == "critical":
                    decision = registry.store.get_record(
                        project,
                        "project_run_approvals",
                        run.get("human_plan_approval_id", ""),
                    )
                else:
                    decision = registry.store.get_record(
                        project,
                        "project_run_specialist_decisions",
                        run.get("specialist_plan_decision_id", ""),
                    )
                sections.append(
                    details(
                        "Discipline specialist plan — human decision required",
                        {
                            "specialist_count": len(specialists),
                            "approval_threshold": 3,
                            "decision_status": decision.get("disposition", "pending")
                            if decision
                            else "pending",
                            "plan_digest": run.get("plan_digest"),
                            "specialists": specialists,
                            "decision_record": decision,
                        },
                    )
                )
                if run_class == "critical":
                    if not decision:
                        sections.append(
                            form(
                                project,
                                run["run_id"],
                                "approve_critical_plan",
                                "Approve Critical plan and specialist roster",
                                run.get("plan_digest", ""),
                            )
                        )
                elif not decision or decision.get("disposition") in {
                    "hold",
                    "revise_and_resubmit",
                }:
                    sections.append(
                        form(
                            project,
                            run["run_id"],
                            "specialist_plan_decision",
                            "Record discipline specialist plan decision",
                            run.get("plan_digest", ""),
                            "<label>Decision <select name='disposition' required>"
                            "<option value='approve'>Approve</option>"
                            "<option value='approve_with_conditions'>Approve with conditions</option>"
                            "<option value='hold'>Hold</option>"
                            "<option value='revise_and_resubmit'>Revise and resubmit</option>"
                            "</select></label>"
                            "<label>Conditions (one per line; required for conditional approval) "
                            "<textarea name='conditions' rows='3' maxlength='4000'></textarea></label>",
                        )
                    )
                elif decision.get("disposition") == "approve_with_conditions":
                    sections.append(
                        form(
                            project,
                            run["run_id"],
                            "confirm_specialist_plan_conditions",
                            "Confirm all approval conditions are satisfied",
                            run.get("plan_digest", ""),
                            "<input type='hidden' name='condition_ids' value='"
                            + escaped(",".join(decision.get("condition_ids", [])))
                            + "'>",
                        )
                    )
            sections.append(
                "<article><h3>"
                + escaped(run_class.title())
                + " run "
                + escaped(run.get("run_id", "unknown"))
                + "</h3><p>Required gates: "
                + escaped(gates)
                + "</p>"
                + (
                    "<p>Human plan approval: " + escaped(approval) + "</p>"
                    if run_class == "critical"
                    else ""
                )
                + (
                    "<p>Routine policy: "
                    + escaped(
                        "active"
                        if policy and policy.get("active")
                        else "missing or revoked"
                    )
                    + "</p>"
                    if run_class == "routine"
                    else ""
                )
                + details("Classification reasons and run state", run)
                + "</article>"
            )
        logs = registry.store.list_records(project, "execution_logs")
        if logs:
            sections.append(details("Execution log (micro-decisions)", logs))
        run_reviews = registry.store.list_records(project, "project_run_reviews")
        for review in run_reviews:
            report = review.get("report", {})
            if report.get("material_comments"):
                sections.append(
                    details("Material Reviewer comment", report["material_comments"])
                )
            if report.get("information_gaps"):
                sections.append(
                    details(
                        "Safety or regulatory information gap",
                        report["information_gaps"],
                    )
                )
        sections.append(
            "<details><summary>Review source authority</summary><p>Imported labels are source observations. Record your judgement for a specific revision, with supporting evidence and a reason. This does not establish engineering adequacy.</p>"
        )
        revisions = registry.records_page(project, "document_revision", limit=100)[
            "records"
        ]
        sources = revisions + [
            n
            for n in registry.records_page(project, "document", limit=100)["records"]
            if not any(
                e.relationship == "has_revision"
                for e in registry.store.get_edges(project, n["node_id"])
            )
        ]
        for source in sources:
            control = registry.document_control(project, source["node_id"])
            source_pages = registry.evidence_page(
                project,
                document_id=source["attributes"].get("document_id"),
                revision=source["attributes"].get("revision"),
                limit=100,
            )["records"]
            basis_options = "".join(
                "<option value='"
                + escaped(n["node_id"])
                + "'>"
                + escaped(
                    str(n["attributes"].get("document_id", n["title"]))
                    + " — revision "
                    + str(n["attributes"].get("revision", "unknown"))
                    + " — page "
                    + str(n["attributes"].get("page", "unknown"))
                )
                + "</option>"
                for n in source_pages
            )
            sections.append(
                "<h3>"
                + escaped(source["title"])
                + "</h3><p>Human-reviewed status: <strong>"
                + escaped(control["status"])
                + "</strong></p>"
            )
            sections.append(
                details("Read source and retained authority decision", control)
            )
            sections.append(
                form(
                    project,
                    source["node_id"],
                    "document_control",
                    "Record source authority review",
                    control["control_digest"],
                    "<label>Status <select name='authority'><option value='unverified'>Unverified / hold</option><option value='current'>Current</option><option value='governing'>Governing for this project</option><option value='superseded'>Superseded</option></select></label>"
                    "<label>Supporting source page <select name='evidence_ids' required><option value=''>Choose the source you checked</option>"
                    + basis_options
                    + "</select></label>",
                )
            )
        sections.append("</details>")
        evidence_review_page = registry.evidence_page(
            project, missing_text=True, limit=100, offset=evidence_offset
        )
        evidence_needing_review = evidence_review_page["records"]
        if evidence_needing_review:
            sections.append(
                "<h3>Evidence requiring human review</h3><p>These pages have no imported text. Review the original source before recording whether it was readable, insufficient or unavailable. This records your observation; it does not establish technical adequacy.</p>"
            )
            for evidence in evidence_needing_review:
                review = registry.evidence_review(project, evidence["node_id"])
                sections.append(details("Evidence without imported text", evidence))
                sections.append(
                    form(
                        project,
                        evidence["node_id"],
                        "review_evidence",
                        "Record evidence review",
                        review["review_digest"],
                        "<label>Status <select name='status'><option value='visually_reviewed'>Visually reviewed</option><option value='insufficient'>Insufficient information</option><option value='not_reviewable'>Not reviewable</option></select></label>",
                    )
                )
            sections.append(
                navigation(
                    evidence_review_page,
                    evidence_offset,
                    "evidence requiring review",
                )
            )
        candidates = registry.list_requirement_candidates(project)
        pending_candidates = [c for c in candidates if c["status"] == "pending_review"]
        if pending_candidates:
            sections.append(
                "<h3>Proposed requirements awaiting review</h3><p>These preserve source wording and are not accepted requirements yet. Check the source, authority context and scope before recording your decision.</p>"
            )
            for candidate in pending_candidates:
                sections.append(details("Candidate requirement", candidate))
                for accepted, label in (
                    ("yes", "Accept extracted requirement"),
                    ("no", "Reject extracted requirement"),
                ):
                    sections.append(
                        form(
                            project,
                            candidate["candidate_id"],
                            "review_requirement_candidate",
                            label,
                            candidate["candidate_digest"],
                            "<input type='hidden' name='accept' value='"
                            + accepted
                            + "'>",
                        )
                    )
        brief_view = registry.client_project_brief_view(project)
        if brief_view["brief"] is not None:
            sections.append(
                details(
                    "Client project brief: overall objectives, context and current/stale status",
                    brief_view,
                )
            )
        if brief_view["status"] != "current":
            brief_fields = {
                "purpose": "Project purpose",
                "success_criteria": "Success criteria",
                "project_context": "Project context",
                "stakeholders_interfaces": "Stakeholders and interfaces",
                "scope": "Project scope",
                "exclusions": "Exclusions",
                "constraints": "Constraints",
                "priorities": "Priorities",
                "approved_decisions": "Approved decisions",
            }
            extras = "<p>Record the client's overall intent first. Cite Registry evidence IDs for each statement; uncited or blank items stay marked unknown.</p>"
            extras += "<label>Brief version ID <input name='brief_id' required placeholder='For example: brief-001'></label><label>Brief title <input name='brief_title' required></label>"
            for key, label in brief_fields.items():
                extras += (
                    "<label>"
                    + escaped(label)
                    + " <textarea name='brief_"
                    + key
                    + "' maxlength='4000'></textarea></label>"
                    "<label>Evidence IDs for this statement, comma-separated <input name='evidence_"
                    + key
                    + "' placeholder='evidence:...'></label>"
                )
            sections.append(
                form(
                    project,
                    project,
                    "record_client_brief",
                    "Save human-approved project brief",
                    extras=extras,
                )
            )
        issues = registry.records_page(project, "engineering_issue", limit=100)[
            "records"
        ]
        verifications = registry.records_page(project, "verification", limit=100)[
            "records"
        ]
        assessments = registry.records_page(project, "assessment", limit=100)["records"]
        alignment_reviews = registry.records_page(
            project, "alignment_review", limit=100
        )["records"]
        for review in alignment_reviews:
            attributes = review.get("attributes", {})
            if attributes.get("material_comments"):
                sections.append(
                    details(
                        "Material Reviewer comment", attributes["material_comments"]
                    )
                )
            if attributes.get("information_gaps"):
                sections.append(
                    details(
                        "Safety or regulatory information gap",
                        attributes["information_gaps"],
                    )
                )
        all_decisions = registry.records_page(project, "decision", limit=100)["records"]
        reviewed_task_decision_ids = {
            record["attributes"].get("source_decision_id")
            for record in all_decisions
            if record["attributes"].get("decision_type") == "human_task_review"
        }
        pending_task_decisions = [
            record
            for record in all_decisions
            if record["attributes"].get("decision_type") == "task"
            and record["node_id"] not in reviewed_task_decision_ids
            and record["attributes"].get("human_review_required")
        ]
        for decision in pending_task_decisions:
            attrs = decision["attributes"]
            sections.append(
                "<article><h3>Proposed Engineering AI task decision</h3>"
                + details("Decision, authority level and source basis", decision)
            )
            evidence_value = ",".join(attrs.get("evidence_ids", []))
            evidence_input = (
                "<label>Evidence IDs you checked <input name='evidence_ids' required value='"
                + escaped(evidence_value)
                + "'></label>"
            )
            formal_input = (
                "<label>Formal authority record ID <input name='formal_authority_reference' required></label>"
                if attrs.get("formal_authority_required")
                else ""
            )
            for accepted, label in (
                ("yes", "Accept task decision"),
                ("no", "Reject task decision"),
            ):
                sections.append(
                    form(
                        project,
                        decision["node_id"],
                        "review_task_decision",
                        label,
                        digest(decision),
                        "<input type='hidden' name='accept' value='"
                        + accepted
                        + "'>"
                        + evidence_input
                        + (formal_input if accepted == "yes" else ""),
                    )
                )
            sections.append("</article>")
        decisions = [
            node for node in all_decisions if "result_id" in node["attributes"]
        ]
        if not issues:
            sections.append("<p>No issues have been imported into this project.</p>")
        for node in issues:
            iid = node["node_id"]
            context = registry.issue_context(project, iid)
            status = context["issue"]["status"]
            proposal_readiness_issue = (
                context["issue"].get("attributes", {}).get("work_type")
                == "proposal_readiness"
            )
            sections.append(
                "<article><h3>"
                + escaped(node["title"])
                + "</h3><p>Status: <strong>"
                + escaped(status)
                + "</strong></p>"
            )
            sections.append(
                details(
                    "Read the issue, source evidence, requirements and decision history",
                    context,
                )
            )
            try:
                mandate_view = registry.client_mandate(project, iid)
            except (ValueError, KeyError, GraphIntegrityError):
                mandate_view = None
            if mandate_view is not None:
                sections.append(
                    details(
                        "Human-approved client mandate for this issue", mandate_view
                    )
                )
            if brief_view["status"] == "current" and mandate_view is None:
                mandate_extras = (
                    "<p>Define the original client objective and acceptance conditions for this issue. This is required before either automated reviewer gate can run.</p>"
                    "<label>Mandate version ID <input name='mandate_id' required placeholder='For example: mandate-001'></label>"
                    "<label>Client objective <textarea name='objective' required maxlength='4000'></textarea></label>"
                    "<label>Agreed scope <textarea name='scope' required maxlength='4000'></textarea></label>"
                    "<label>Acceptance criteria, one per line <textarea name='acceptance_criteria' required></textarea></label>"
                    "<label>Source evidence IDs, comma-separated <input name='mandate_evidence_ids' required></label>"
                    "<label>Allowed tool <select name='allowed_tools'><option>compare_revision</option><option>validate_traceability</option><option>generate_review_pack</option><option>validate_proposal_readiness</option><option>external_solver</option></select></label>"
                    "<label>Risk <select name='risk_level'><option>unknown</option><option>low</option><option>medium</option><option>high</option><option>critical</option></select></label>"
                    "<label>Importance <select name='importance_level'><option>unknown</option><option>low</option><option>medium</option><option>high</option><option>critical</option></select></label>"
                    "<label>Simple? <select name='simple'><option value='no'>No / not established</option><option value='yes'>Yes</option></select></label>"
                    "<label>Reversible? <select name='reversible'><option value='no'>No / not established</option><option value='yes'>Yes</option></select></label>"
                    "<label>Consequence domains, comma-separated <input name='consequence_domains' placeholder='safety, regulatory, contractual'></label>"
                    "<label>Unknowns to preserve, comma-separated <input name='mandate_unknowns'></label>"
                )
                sections.append(
                    form(
                        project,
                        iid,
                        "record_client_mandate",
                        "Save human-approved issue mandate",
                        extras=mandate_extras,
                    )
                )
            submissions = [
                record
                for record in context["linked_records"]
                if record["node_type"] == NodeType.SUBMISSION
                and record["attributes"].get("issue_id") == iid
            ]
            tasks = ExecutionService(registry).list_tasks(project)
            for task in tasks:
                if task.get("issue_id") != iid:
                    continue
                for conflict in task.get("brain_plan", {}).get(
                    "knowledge_conflicts", []
                ):
                    sections.append(
                        "<section class='notice'><strong>KNOWLEDGE CONFLICT — Project Requirement</strong>"
                        "<p>Task "
                        + escaped(task["task_id"])
                        + "</p><pre>"
                        + escaped(conflict["message"])
                        + "</pre></section>"
                    )
            for submission in submissions:
                sub_attrs = submission["attributes"]
                sub_id = submission["node_id"]
                sections.append(
                    "<section class='proposal-readiness'><h4>Proposal readiness: "
                    + escaped(submission["title"])
                    + "</h4><p>Candidate revision: <strong>"
                    + escaped(sub_attrs.get("candidate_revision_id", "UNKNOWN"))
                    + "</strong><br>Template revision: <strong>"
                    + escaped(sub_attrs.get("template_revision_id", "UNKNOWN"))
                    + "</strong></p>"
                )
                sections.append(
                    details("Proposal scope matrix", sub_attrs.get("scope_matrix", []))
                )
                sections.append(
                    details(
                        "Protected template sections",
                        sub_attrs.get("protected_sections", []),
                    )
                )
                submission_results = sorted(
                    (
                        result
                        for result in context["results"]
                        if result["attributes"].get("method")
                        == "validate_proposal_readiness"
                        and result["attributes"].get("outputs", {}).get("submission_id")
                        == sub_id
                    ),
                    key=lambda result: result["node_id"],
                )
                current_digest = None
                try:
                    current_digest = registry.submission_context(project, sub_id)[
                        "submission_digest"
                    ]
                except (ValueError, KeyError, GraphIntegrityError):
                    pass
                matching_tasks = [
                    task
                    for task in tasks
                    if task.get("tool") == "validate_proposal_readiness"
                    and task.get("issue_id") == iid
                    and task.get("parameters", {}).get("submission_id") == sub_id
                ]
                latest_result = submission_results[-1] if submission_results else None
                latest_task = next(
                    (
                        task
                        for task in matching_tasks
                        if latest_result
                        and task.get("result_id") == latest_result["node_id"]
                    ),
                    matching_tasks[-1] if matching_tasks else None,
                )
                task_is_current = bool(
                    latest_task
                    and current_digest
                    and latest_task.get("brain_plan", {}).get("submission_digest")
                    == current_digest
                )
                if latest_result:
                    readiness = latest_result["attributes"]["outputs"].get(
                        "ready_for_human_review"
                    )
                    status_text = (
                        "Ready for human review"
                        if readiness is True and task_is_current
                        else "Not ready for human review"
                        if readiness is False
                        else "UNKNOWN / INSUFFICIENT INFORMATION — stale or unbound result"
                    )
                    sections.append(
                        "<p>Readiness status: <strong>"
                        + escaped(status_text)
                        + "</strong></p>"
                    )
                    output = latest_result["attributes"].get("outputs", {})
                    sections.append(
                        details(
                            "Proposal source checks", output.get("source_checks", [])
                        )
                    )
                    sections.append(
                        details(
                            "Protected-section comparison",
                            output.get("protected_section_checks", []),
                        )
                    )
                    sections.append(
                        details(
                            "Requirement coverage",
                            output.get("requirement_coverage", {}),
                        )
                    )
                    sections.append(
                        details(
                            "Readiness warnings and unknowns",
                            output.get("warnings", []),
                        )
                    )
                    if not task_is_current:
                        sections.append(
                            "<p class='notice'>Sources or task binding changed. This result is not current.</p>"
                        )
                    assessed = [
                        record
                        for record in assessments
                        if record["attributes"].get("result_id")
                        == latest_result["node_id"]
                    ]
                    for assessment in assessed:
                        sections.append(
                            details("Document AI proposal assessment", assessment)
                        )
                else:
                    sections.append(
                        "<p>Readiness status: <strong>Awaiting V2 checks and Document AI assessment</strong></p>"
                    )
                if not task_is_current and current_digest:
                    sections.append(
                        form(
                            project,
                            iid,
                            "prepare_submission_task",
                            "Document AI: prepare proposal readiness task",
                            current_digest,
                            "<input type='hidden' name='submission_id' value='"
                            + escaped(sub_id)
                            + "'>",
                        )
                    )
                sections.append("</section>")
            next_status = {
                "proposed": ("open", "Open issue for review"),
                "open": ("accepted", "Accept issue for action"),
                "accepted": ("closed", "Close issue after checking evidence"),
            }.get(status)
            if proposal_readiness_issue:
                next_status = None
            if next_status:
                sections.append(
                    form(
                        project,
                        iid,
                        "transition",
                        next_status[1],
                        context["output_digest"],
                        "<input type='hidden' name='status' value='"
                        + next_status[0]
                        + "'>",
                    )
                )
            options = "".join(
                "<option value='" + escaped(eid) + "'>" + escaped(eid) + "</option>"
                for eid in context["issue"]["evidence_ids"]
            )
            options_head = "".join(
                "<option value='"
                + escaped(eid)
                + "'"
                + (" selected" if eid == context["issue"]["evidence_ids"][-1] else "")
                + ">"
                + escaped(eid)
                + "</option>"
                for eid in context["issue"]["evidence_ids"]
            )
            sections.append(
                form(
                    project,
                    iid,
                    "prepare_task",
                    "Document AI: structure task for authorisation",
                    context["output_digest"],
                    "<label>Task <select name='tool'><option value='compare_revision'>Compare revisions</option>"
                    "<option value='validate_traceability'>Check evidence traceability</option><option value='generate_review_pack'>Generate review pack</option></select></label>"
                    "<label>Earlier source <select name='base'>"
                    + options
                    + "</select></label><label>Later source <select name='head'>"
                    + options_head
                    + "</select></label>",
                )
            )
            for proposal in context["pending_proposals"]:
                sections.append(
                    details(
                        "Pending lifecycle request: " + proposal["to_status"], proposal
                    )
                )
                for accepted, label in (
                    ("yes", "Approve requested change"),
                    ("no", "Reject requested change"),
                ):
                    sections.append(
                        form(
                            project,
                            proposal["proposal_id"],
                            "review_proposal",
                            label,
                            proposal["proposal_digest"],
                            "<input type='hidden' name='accept' value='"
                            + accepted
                            + "'>",
                        )
                    )
            plan_reviews = [
                review
                for review in alignment_reviews
                if review["attributes"].get("issue_id") == iid
                and review["attributes"].get("phase") == "plan"
            ]
            if plan_reviews:
                sections.append(
                    details(
                        "AI plan alignment review: client perspective before V2 execution",
                        plan_reviews,
                    )
                )
                for review in plan_reviews:
                    for comment in review["attributes"].get("review_comments", []):
                        sections.append(
                            "<p class='notice'>" + escaped(comment) + "</p>"
                        )
            for result in context["results"]:
                sections.append(
                    details(
                        "Execution result: read the inputs, method and outputs", result
                    )
                )
                reviews = [
                    review
                    for review in alignment_reviews
                    if review["attributes"].get("result_id") == result["node_id"]
                ]
                if reviews:
                    sections.append(
                        details(
                            "AI Reviewer Gate 1 plan alignment and Gate 2 project assurance",
                            reviews,
                        )
                    )
                    for review in reviews:
                        if review["attributes"].get("phase") != "outcome":
                            continue
                        for comment in review["attributes"].get("review_comments", []):
                            sections.append(
                                "<p class='notice'>" + escaped(comment) + "</p>"
                            )
                reviewed = [
                    n
                    for n in verifications
                    if n["attributes"]["result_id"] == result["node_id"]
                ]
                if reviewed:
                    sections.append(
                        "<p>Human verification recorded.</p>"
                        + details("Verification record", reviewed)
                    )
                assessed = sorted(
                    (
                        n
                        for n in assessments
                        if n["attributes"].get("result_id") == result["node_id"]
                    ),
                    key=lambda n: n["attributes"]["produced_at"],
                )
                if not assessed:
                    sections.append(
                        form(
                            project,
                            result["node_id"],
                            "assess_result",
                            "Document AI: assess output before final review",
                            digest(result),
                        )
                    )
                    continue
                assessment = assessed[-1]
                sections.append(
                    details(
                        "Document AI assessment: read checks and unknowns", assessment
                    )
                )
                outcomes = sorted(
                    (
                        d
                        for d in decisions
                        if d["attributes"]["result_id"] == result["node_id"]
                    ),
                    key=lambda d: d["attributes"]["reviewed_at"],
                )
                if outcomes:
                    human_outcomes = [
                        outcome
                        for outcome in outcomes
                        if outcome["attributes"].get("authority") != "ai_delegated"
                    ]
                    ai_outcomes = [
                        outcome
                        for outcome in outcomes
                        if outcome["attributes"].get("authority") == "ai_delegated"
                    ]
                    if human_outcomes:
                        sections.append(
                            details("Recorded human decisions", human_outcomes)
                        )
                    if ai_outcomes:
                        sections.append(
                            details(
                                "Delegated AI decisions: policy, evidence and rationale",
                                ai_outcomes,
                            )
                        )
                if result["attributes"].get("method") == "validate_proposal_readiness":
                    sections.append(
                        "<p>This AI result is a readiness report for your human review; it does not approve or issue the proposal.</p>"
                    )
                    continue
                for disposition, label in (
                    ("accept", "Verify and accept output"),
                    ("reject", "Reject output"),
                    ("rework", "Request rework"),
                    ("hold", "Hold pending information"),
                ):
                    sections.append(
                        form(
                            project,
                            result["node_id"],
                            "decide_result",
                            label,
                            digest(result),
                            "<input type='hidden' name='assessment_id' value='"
                            + escaped(assessment["node_id"])
                            + "'>"
                            "<input type='hidden' name='assessment_digest' value='"
                            + digest(assessment)
                            + "'>"
                            "<input type='hidden' name='disposition' value='"
                            + disposition
                            + "'>",
                        )
                    )
            sections.append("</article>")
        for task in ExecutionService(registry).list_tasks(project):
            sections.append(
                "<article><h3>Task: "
                + escaped(task["tool"])
                + " — "
                + escaped(task["state"])
                + "</h3>"
            )
            sections.append(
                details("Read the exact task scope, inputs and execution history", task)
            )
            operation = {
                "proposed": ("authorize", "Authorise this exact task"),
                "queued": ("execute", "Run authorised task"),
                "failed": ("retry", "Authorise another attempt"),
                "running": ("recover", "Record interrupted execution"),
            }.get(task["state"])
            if operation:
                sections.append(
                    form(
                        project,
                        task["task_id"],
                        operation[0],
                        operation[1],
                        task["task_digest"],
                    )
                )
            if task["state"] in {"proposed", "queued", "running"}:
                sections.append(form(project, task["task_id"], "cancel", "Cancel task"))
            sections.append("</article>")
    for organization_id in sorted(registry.principal.organization_ids):
        scope = "ORG:" + organization_id
        sections.append(
            "<h2>Organisational knowledge — " + escaped(organization_id) + "</h2>"
        )
        lessons = registry.records_page(scope, "lesson", limit=100)["records"]
        for lesson in lessons:
            status = registry.store.get_record(
                scope, "organizational_lesson_status", lesson["node_id"]
            )
            if status and status.get("status") == "active":
                sections.append(
                    details(
                        "Validated organizational lesson and source history", lesson
                    )
                )
                sections.append(
                    form(
                        scope,
                        lesson["node_id"],
                        "review_organizational_lesson",
                        "Place under review, retire or supersede",
                        digest({"lesson": lesson, "status": status}),
                        "<label>Status <select name='status'><option value='under_review'>Under review</option><option value='retired'>Retired</option><option value='superseded'>Superseded</option></select></label>",
                    )
                )
                for project in sorted(registry.principal.project_ids):
                    import_id = f"org-import:{organization_id}:{lesson['node_id']}"
                    if registry.store.get_node(project, import_id):
                        continue
                    sections.append(
                        form(
                            project,
                            lesson["node_id"],
                            "import_organizational_lesson",
                            "Review and import to project " + project,
                            digest({"lesson": lesson, "status": status}),
                            "<input type='hidden' name='organization_id' value='"
                            + escaped(organization_id)
                            + "'><input type='hidden' name='import_id' value='"
                            + escaped(import_id)
                            + "'>",
                        )
                    )
        sections.append(
            "<details><summary>Promote a reviewed project lesson into organisational knowledge</summary>"
            + form(
                scope,
                "new-organizational-lesson",
                "promote_organizational_lesson",
                "Validate and promote lesson",
                "",
                "<label>Lesson ID <input name='record_id' required></label>"
                "<label>Title <input name='title' required></label>"
                "<label>Source project:lesson IDs, comma separated <input name='source_lessons' required></label>"
                "<label>Observation <input name='observation' required></label>"
                "<label>Observed result <input name='result' required></label>"
                "<label>Interpretation <input name='interpretation' required></label>"
                "<label>Validated statement and conditions <input name='validated_statement' required></label>"
                "<label>Applicability <input name='applicability' required></label>"
                "<label>Limitations <input name='limitations' required></label>"
                "<label>Relevant standards/revisions, comma separated <input name='relevant_standards'></label>"
                "<label>Review due (YYYY-MM-DD) <input name='review_due' required></label>",
            )
            + "</details>"
        )
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Engineering Registry — Review Desk</title><style>
body{font:16px system-ui;color:#182537;background:#f4f7fa;max-width:1050px;margin:30px auto;padding:0 20px}
h1,h2,h3{line-height:1.25}article{background:white;padding:24px;border:1px solid #d8e2ea;border-radius:12px;margin:20px 0}
.notice{background:#fff3cc;border-left:5px solid #c28e00;padding:14px}button{background:#1558ae;color:white;border:0;border-radius:6px;padding:10px 15px;cursor:pointer}
input,select{font:inherit;padding:8px;border:1px solid #c6d1dc;border-radius:5px;max-width:95%}form{border-top:1px solid #e3e8ed;margin-top:16px;padding-top:16px}
label{display:block;margin:10px 0}input[name=rationale]{width:70%;display:block}pre{white-space:pre-wrap;word-break:break-word;background:#f0f4f8;padding:16px;font-size:13px}
summary{cursor:pointer;padding:8px 0}a{color:#1558ae}</style></head><body><p><a href='/'>← Return to Engineering Workbench</a></p><h1>Engineering Registry</h1>
<p>Document AI brain · Registry memory and control · V2 execution · MCP connection</p>"""
        + (
            "<p class='notice'><strong>Demonstration project.</strong> These are invented source excerpts. No real project has been imported or approved.</p>"
            if demo
            else ""
        )
        + (
            "<p>Reviewing as <strong>"
            + escaped(registry.principal.actor_id)
            + "</strong>. Read the source and result details before recording a decision.</p>"
            "<p>Human records client project brief and issue mandate → Document AI classifies and plans the run → Routine follows an active human-approved policy without Reviewer gates; Engineering uses both Reviewer gates and humans Authorise execution; Critical adds mandatory human plan and outcome decisions → Registry retains the full history.</p>"
            "<p>Execution performs document checks. It does not establish design adequacy or perform engineering calculations.</p>"
            + ("<p class='notice'>" + escaped(notice) + "</p>" if notice else "")
            + "".join(sections)
            + "<p>Your records are saved locally in SQLite. Close the launcher's Terminal window to stop this desk.</p></body></html>"
        )
    )


def make_server(
    database: str | Path,
    projects: set[str],
    actor: str,
    *,
    demo: bool = False,
    organization_ids: set[str] | None = None,
    workflow_factory: Callable[[RegistryService], Any] | None = None,
    organization_database: str | Path | None = None,
) -> tuple[HTTPServer, str]:
    if (
        organization_database
        and Path(organization_database).resolve() == Path(database).resolve()
    ):
        raise ValueError(
            "Organisational and project Registries must be separate databases"
        )
    if organization_database:
        org_path = Path(organization_database)
        if not org_path.is_file():
            raise ValueError("Configure an existing organisational Registry database")
        if Path(database).exists() and org_path.samefile(database):
            raise ValueError("Organisational Registry must be physically separate")
        if not organization_ids:
            raise ValueError(
                "Organisational Registry requires explicit organisation grants"
            )
        with SQLiteGraphStore(org_path) as org_store:
            scopes = org_store.project_ids() | (
                {org_store.bound_scope} if org_store.bound_scope else set()
            )
            if any(not scope.startswith("ORG:") for scope in scopes):
                raise ValueError(
                    "Organisational Registry must not contain project records"
                )
    if workflow_factory is None and not demo:
        from pipelines.review.execution_workflow import build_automated_workflow

        workflow_factory = build_automated_workflow
    principal = Principal(
        actor,
        frozenset(projects),
        "reviewer",
        organization_ids=frozenset(organization_ids or set()),
    )
    token = secrets.token_urlsafe(32)

    def workbench_page(registry, **options):
        from .workbench import render_workbench

        with ExitStack() as connections:
            org = None
            if organization_database:
                org = RegistryService(
                    connections.enter_context(SQLiteGraphStore(organization_database)),
                    principal,
                )
            return render_workbench(
                registry,
                organization_configured=bool(org),
                organization_registry=org,
                **options,
            )

    class Handler(BaseHTTPRequestHandler):
        server: HTTPServer

        def log_message(self, *_args):
            pass  # Do not log session URLs or record content.

        def send(self, status: int, body: str, headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
            )
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def authenticated(self) -> bool:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                found = cookie.get("engineering_session")
                return found is not None and hmac.compare_digest(found.value, token)
            except (ValueError, TypeError):
                return False

        def host_valid(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.host_valid():
                return self.send(403, "Local host required.")
            parsed = urlsplit(self.path)
            if parsed.path not in {"/", "/desk"}:
                return self.send(404, "Page not found.")
            supplied = parse_qs(parsed.query).get("session", [""])[0]
            if supplied and hmac.compare_digest(supplied, token):
                return self.send(
                    303,
                    "Opening review desk.",
                    {
                        "Location": "/",
                        "Set-Cookie": "engineering_session="
                        + token
                        + "; HttpOnly; SameSite=Strict; Path=/",
                    },
                )
            if not self.authenticated():
                return self.send(403, "Open the review desk using the launcher's link.")
            try:
                values = parse_qs(parsed.query, max_num_fields=8)
                if any(len(value) != 1 for value in values.values()):
                    raise ValueError
                if set(values) - {
                    "project",
                    "view",
                    "record",
                    "q",
                    "impact",
                    "evidence_offset",
                }:
                    raise ValueError
                offsets = values.get("evidence_offset", ["0"])
                if len(offsets) != 1:
                    raise ValueError
                evidence_offset = int(offsets[0])
                if not 0 <= evidence_offset <= 1_000_000 or evidence_offset % 100:
                    raise ValueError
            except ValueError:
                return self.send(400, "Evidence page offset must be a valid page.")
            with SQLiteGraphStore(database) as store:
                registry = RegistryService(store, principal)
                try:
                    if parsed.path == "/desk":
                        page = render_page(
                            registry, demo=demo, evidence_offset=evidence_offset
                        )
                    else:
                        page = workbench_page(
                            registry,
                            demo=demo,
                            view=values.get("view", ["overview"])[0],
                            project=values.get("project", [""])[0],
                            search=values.get("q", [""])[0],
                            record=values.get("record", [""])[0],
                            impact=values.get("impact", [""])[0],
                        )
                    self.send(200, page)
                except (ValueError, PermissionError) as exc:
                    self.send(400, html.escape(str(exc)))

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                not self.host_valid()
                or not self.authenticated()
                or self.headers.get("Origin") != origin
            ):
                return self.send(403, "Local review session and same origin required.")
            if self.path != "/action":
                return self.send(404, "Unknown action route.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100_000:
                    raise ValueError("Request exceeds limit")
                values = parse_qs(
                    self.rfile.read(length).decode("utf-8"), max_num_fields=40
                )
                if any(len(v) != 1 for v in values.values()):
                    raise ValueError("Ambiguous duplicate fields")
                fields = {k: v[0] for k, v in values.items()}
                from .workbench import VIEWS

                return_view = fields.get("return_view")
                if return_view is not None and return_view not in VIEWS:
                    raise ValueError("Unknown workbench view")
                with SQLiteGraphStore(database) as store:
                    with ExitStack() as connections:
                        organization_registry = None
                        if organization_database:
                            org_store = connections.enter_context(
                                SQLiteGraphStore(organization_database)
                            )
                            organization_registry = RegistryService(
                                org_store, principal
                            )
                        notice = apply_action(
                            RegistryService(store, principal),
                            fields,
                            workflow_factory=workflow_factory,
                            organization_registry=organization_registry,
                        )
                    registry = RegistryService(store, principal)
                    page = (
                        workbench_page(
                            registry,
                            demo=demo,
                            notice=notice,
                            view=return_view,
                            project=fields.get("project_id", ""),
                        )
                        if return_view
                        else render_page(registry, demo=demo, notice=notice)
                    )
                return self.send(200, page)
            except AnalysisUnavailable:
                return self.send(
                    503,
                    "<p>Document AI analysis is unavailable. Any completed V2 output remains unverified. Retry assessment from the latest records.</p><a href='/'>Return to the latest records</a>",
                )
            except (ValueError, PermissionError, UnicodeError) as exc:
                return self.send(
                    400,
                    "<p>Action was not recorded: "
                    + html.escape(str(exc))
                    + "</p><a href='/'>Return to the latest records</a>",
                )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    return server, f"http://127.0.0.1:{server.server_port}/?session={token}"


def serve(
    database: str | Path,
    projects: set[str],
    actor: str,
    *,
    demo: bool = False,
    organization_ids: set[str] | None = None,
    open_browser: bool = False,
    workflow_factory: Callable[[RegistryService], Any] | None = None,
    organization_database: str | Path | None = None,
) -> None:
    server, url = make_server(
        database,
        projects,
        actor,
        demo=demo,
        organization_ids=organization_ids,
        workflow_factory=workflow_factory,
        organization_database=organization_database,
    )
    print(
        "Engineering Workbench is running locally. Close this Terminal or press Control-C to stop."
    )
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
