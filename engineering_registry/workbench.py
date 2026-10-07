"""Accessible local project workbench; Registry services retain all authority."""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from urllib.parse import urlencode

from engineering_document_ai_brain import DocumentAIWorkflow

from .service import RegistryService, digest

VIEWS = {
    "overview": ("Overview", "Your project at a glance"),
    "evidence": ("Evidence", "Follow the source, understand the conclusion"),
    "findings": ("Findings", "Review issues and record your decision"),
    "runs": ("Project runs", "See what is ready, blocked and affected"),
    "revisions": ("Revisions", "Know which technical basis governs"),
    "knowledge": ("Knowledge", "Learn from experience with human validation"),
    "activity": ("Decision history", "Who did what, and why"),
}


def escape(value) -> str:
    return html.escape(str(value if value is not None else "UNKNOWN"), quote=True)


def link(project: str, view: str, record: str = "", **values) -> str:
    return "/?" + urlencode(
        {"project": project, "view": view, "record": record, **values}
    )


def badge(value) -> str:
    status = str(value or "UNKNOWN")
    tone = (
        "good"
        if status in {"accepted", "current", "completed", "aligned", "confirmed"}
        else "warn"
        if status in {"held", "blocked", "superseded", "pending", "stale"}
        else "neutral"
    )
    return f'<span class="badge {tone}">{escape(status.replace("_", " "))}</span>'


def details(label: str, value) -> str:
    return f"<details><summary>{escape(label)}</summary><pre>{escape(json.dumps(value, indent=2, default=str))}</pre></details>"


def empty(title: str, explanation: str) -> str:
    return f'<div class="empty"><span class="empty-mark">○</span><h2>{escape(title)}</h2><p>{escape(explanation)}</p></div>'


def action(project, view, record, operation, label, snapshot, fields="") -> str:
    hidden = {
        "project_id": project,
        "record_id": record,
        "operation": operation,
        "digest": snapshot,
        "return_view": view,
    }
    return (
        '<form class="decision" method="post" action="/action">'
        + "".join(
            f'<input type="hidden" name="{key}" value="{escape(value)}">'
            for key, value in hidden.items()
        )
        + fields
        + '<label>Why are you making this decision?<textarea name="rationale" rows="2" maxlength="2000" required placeholder="Record your reason and any evidence you checked"></textarea></label>'
        + f'<button type="submit">{escape(label)}</button></form>'
    )


def downstream(graph: dict[str, list[str]], changed: str) -> list[str]:
    """Conservative transitive impact preview, including the changed task."""
    if changed not in graph:
        raise ValueError("Select a task in this run")
    affected = {changed}
    while True:
        new = {
            task for task, predecessors in graph.items() if set(predecessors) & affected
        }
        if new <= affected:
            return sorted(affected)
        affected.update(new)


def render_workbench(
    registry: RegistryService,
    *,
    demo: bool,
    view: str = "overview",
    project: str = "",
    search: str = "",
    record: str = "",
    notice: str = "",
    impact: str = "",
    organization_configured: bool = False,
    organization_registry: RegistryService | None = None,
) -> str:
    if view not in VIEWS:
        raise ValueError("Unknown workbench view")
    project = project or next(iter(sorted(registry.principal.project_ids)), "")
    registry.check_access(project)
    if len(search) > 200 or len(record) > 500:
        raise ValueError("Search or record selection exceeds limit")
    nodes = registry.list_records(project)
    by_id = {item["node_id"]: item for item in nodes}
    issues = [item for item in nodes if item["node_type"] == "engineering_issue"]
    if view in {"overview", "findings"}:
        for item in issues:
            item["attributes"] = {
                **item["attributes"],
                **asdict(registry.store.get_issue(project, item["node_id"])),
            }
    runs = registry.list_control_records(project, "project_runs")
    incoming = None

    def incoming_references(node_id):
        nonlocal incoming
        if incoming is None:
            incoming = {}
            for source in nodes:
                for edge in registry.store.get_edges(project, source["node_id"]):
                    incoming.setdefault(edge.target_node_id, []).append(
                        (edge.relationship, source)
                    )
        return incoming.get(node_id, [])

    def selected(items):
        return [
            item
            for item in items
            if (not record or item["node_id"] == record)
            and (
                not search
                or search.casefold() in json.dumps(item, default=str).casefold()
            )
        ]

    def node_link(item):
        target_view = {
            "engineering_issue": "findings",
            "document": "revisions",
            "document_revision": "revisions",
            "lesson": "knowledge",
        }.get(str(item["node_type"]), "evidence")
        return f'<a href="{escape(link(project, target_view, item["node_id"]))}">{escape(item["title"])}</a>'

    def source_card(item):
        attrs = item["attributes"]
        body = (
            '<p class="eyebrow">'
            + escape(str(item["node_type"]).replace("_", " "))
            + " · "
            + escape(item["node_id"])
            + "</p><h3>"
            + node_link(item)
            + "</h3>"
        )
        body += (
            "<p>"
            + badge(attrs.get("governing_status", attrs.get("status", "UNKNOWN")))
            + ' <span class="muted">Revision '
            + escape(attrs.get("revision", "UNKNOWN"))
            + " · "
            + escape(attrs.get("locator", attrs.get("page", "Location unknown")))
            + "</span></p>"
        )
        statement = attrs.get(
            "text",
            attrs.get(
                "source_text", attrs.get("finding", attrs.get("observation", ""))
            ),
        )
        if statement:
            body += "<blockquote>" + escape(statement) + "</blockquote>"
        if item["node_type"] == "evidence":
            review = registry.evidence_review(project, item["node_id"])
            body += "<p>Human source review: " + badge(review["status"]) + "</p>"
            body += action(
                project,
                view,
                item["node_id"],
                "review_evidence",
                "Record evidence review",
                review["review_digest"],
                '<label>Source review<select name="status"><option value="visually_reviewed">I checked this source</option><option value="insufficient">More evidence is needed</option><option value="not_reviewable">Cannot review this source</option></select></label>',
            )
        if item["node_type"] == "document_revision":
            control = registry.document_control(project, item["node_id"])
            body += (
                "<p>Human-controlled authority: " + badge(control["status"]) + "</p>"
            )
            body += action(
                project,
                view,
                item["node_id"],
                "document_control",
                "Record revision authority",
                control["control_digest"],
                '<label>Authority<select name="authority"><option value="unverified">Unverified</option><option value="current">Current</option><option value="governing">Governing</option><option value="superseded">Superseded</option></select></label><label>Supporting evidence IDs (comma separated)<input name="evidence_ids" required placeholder="evidence:EB"></label>',
            )
        edges = registry.store.get_edges(project, item["node_id"])
        linked = [
            (edge.relationship, by_id[edge.target_node_id])
            for edge in edges
            if edge.target_node_id in by_id
        ]
        if linked:
            body += (
                '<ul class="source-links">'
                + "".join(
                    '<li><span class="muted">'
                    + escape(str(relation).replace("_", " "))
                    + "</span> → "
                    + node_link(node)
                    + "</li>"
                    for relation, node in linked
                )
                + "</ul>"
            )
        references = incoming_references(item["node_id"])
        if references:
            body += (
                '<h3>Referenced by</h3><ul class="source-links">'
                + "".join(
                    "<li>"
                    + node_link(source)
                    + ' <span class="muted">('
                    + escape(str(relation).replace("_", " "))
                    + ")</span></li>"
                    for relation, source in references
                )
                + "</ul>"
            )
        return (
            '<article class="card">'
            + body
            + details("Full retained record", item)
            + "</article>"
        )

    parts = []
    if view == "overview":
        active = [
            item
            for item in issues
            if item["attributes"].get("status") not in {"closed", "rejected"}
        ]
        pending = registry.list_proposals(project)
        pending = [
            item for item in pending if item.get("status", "pending") == "pending"
        ]
        schedules = []
        unknown_runs = 0
        from engineering_ai_system.project_run import ProjectRunService

        for run in runs:
            try:
                schedules.extend(
                    ProjectRunService(
                        registry,
                        DocumentAIWorkflow(registry),
                        execution_mode=run.get("execution_mode", "GOVERNED"),
                    ).get_schedule(project, run["run_id"])["tasks"]
                )
            except (ValueError, KeyError):
                unknown_runs += 1
        metrics = [
            (len(active), "Active issues", "findings"),
            (len(pending), "Pending proposals", "findings"),
            (
                sum(
                    item["state"] in {"blocked", "stale", "failed"}
                    for item in schedules
                ),
                "Tasks needing attention",
                "runs",
            ),
            (len(runs), "Project runs", "runs"),
        ]
        parts.append(
            '<div class="metrics">'
            + "".join(
                f'<a class="metric" href="{escape(link(project, target))}"><span>{number}</span><p>{label}</p></a>'
                for number, label, target in metrics
            )
            + "</div>"
        )
        parts.append(
            '<div class="two-column"><section class="card"><p class="eyebrow">Your next steps</p><h2>Review what matters</h2><p>Open a finding, read its linked sources, then record your reason and decision.</p><a class="button" href="'
            + escape(link(project, "findings"))
            + '">Review findings →</a><p class="muted">Authorise execution only after reviewing its scope and required gates.</p></section><section class="card"><p class="eyebrow">Project assurance</p><h2>Human authority stays with you</h2><p>Engineering runs require two independent Reviewer gates. Critical runs also require human plan and outcome decisions. Routine work needs a current human-approved low-risk policy.</p><a href="'
            + escape(link(project, "runs"))
            + '">Check upcoming gates →</a></section></div>'
        )
        if unknown_runs:
            parts.append(
                '<p class="notice">UNKNOWN schedule for '
                + str(unknown_runs)
                + " run(s). Missing or changed records must be reviewed.</p>"
            )
        if runs:
            checkpoints = []
            for run in runs:
                checkpoint = (
                    "Policy and task readiness"
                    if run.get("execution_class") == "routine"
                    else "Reviewer Gate 1 · plan"
                    if not run.get("gate1_review_id")
                    else "Human plan decision"
                    if run.get("execution_class") == "critical"
                    and not run.get("human_plan_approval_id")
                    else "V2 execution and Lead integration"
                    if not run.get("integration_id")
                    else "Reviewer Gate 2 · outcome"
                    if not run.get("gate2_review_id")
                    else "Final outcome decision"
                    if not run.get("outcome_id")
                    else "Recorded outcome · review history"
                )
                checkpoints.append(
                    '<li><a href="'
                    + escape(link(project, "runs", run["run_id"]))
                    + '">'
                    + escape(run["run_id"])
                    + "</a> → "
                    + escape(checkpoint)
                    + "</li>"
                )
            parts.append(
                '<section class="card"><h2>Upcoming checkpoints</h2><p class="muted">Open each run to check whether its gates and sources remain current.</p><ul class="source-links">'
                + "".join(checkpoints)
                + "</ul></section>"
            )
        parts.append(
            "<h2>Issues to review</h2>"
            + (
                "".join(source_card(item) for item in selected(active)[:5])
                or empty(
                    "No active issues",
                    "No active issue is retained in this project. This does not certify engineering correctness.",
                )
            )
        )
        validated, total = registry.store.list_human_confirmed_evidence_page(
            project, limit=3
        )
        parts.append(
            '<h2>Human-confirmed evidence</h2><p class="muted">'
            + str(total)
            + " confirmed source record(s)</p>"
            + (
                "".join(source_card(by_id[item.node_id]) for item in validated)
                or empty(
                    "No confirmed evidence yet",
                    "Evidence remains unconfirmed until its human review is recorded.",
                )
            )
        )
    elif view == "findings":
        proposals = registry.list_proposals(project)
        for item in selected(issues):
            try:
                context = registry.issue_context(project, item["node_id"])
            except ValueError as error:
                parts.append(
                    empty(
                        item["title"],
                        "UNKNOWN / INSUFFICIENT INFORMATION: "
                        + str(error)
                        + ". Review the retained evidence before taking a finding action.",
                    )
                )
                continue
            issue = context["issue"]
            body = (
                '<p class="eyebrow">Finding review · '
                + escape(item["node_id"])
                + "</p><h2>"
                + escape(item["title"])
                + "</h2>"
                + badge(issue.get("status"))
            )
            for label, key in [
                ("Finding", "finding"),
                ("Interpretation", "interpretation"),
                ("Engineering judgement", "engineering_judgement"),
                ("Recommendation", "recommendation"),
                ("Unknowns", "unknowns"),
            ]:
                value = issue.get(key)
                if value:
                    body += "<h3>" + label + "</h3><p>" + escape(value) + "</p>"
            body += '<h3>Source → requirement → action → decision → closure</h3><ul class="source-links">'
            body += "".join(
                "<li>" + badge(source["node_type"]) + " " + node_link(source) + "</li>"
                for source in context["linked_records"]
            )
            body += '</ul><p class="muted">Missing links remain unknown. Closing requires the canonical evidence and decision checks.</p>'
            for proposal in proposals:
                if (
                    proposal.get("issue_id") != item["node_id"]
                    or proposal.get("status") != "pending"
                ):
                    continue
                body += (
                    '<section class="notice"><h3>Pending change: '
                    + escape(proposal["to_status"].replace("_", " "))
                    + "</h3><p>"
                    + escape(proposal["rationale"])
                    + "</p><p>Proposed by "
                    + escape(proposal["proposed_by"])
                    + "</p>"
                )
                for accept, label in [
                    ("yes", "Approve requested change"),
                    ("no", "Reject requested change"),
                ]:
                    body += action(
                        project,
                        view,
                        proposal["proposal_id"],
                        "review_proposal",
                        label,
                        proposal["proposal_digest"],
                        '<input type="hidden" name="accept" value="' + accept + '">',
                    )
                body += "</section>"
            options = [
                ("open", "Open for review"),
                ("under_review", "Request more evidence"),
                ("accepted", "Accept finding"),
                ("held", "Hold decision"),
                ("rejected", "Reject finding"),
                ("closed", "Close with evidence"),
            ]
            body += action(
                project,
                view,
                item["node_id"],
                "transition",
                "Record human decision",
                context["output_digest"],
                '<label>Your decision<select name="status" required><option value="" selected disabled>Choose a decision</option>'
                + "".join(
                    f'<option value="{value}">{label}</option>'
                    for value, label in options
                )
                + "</select></label>",
            )
            from .finding_review import CLASSIFICATIONS

            amendments = sorted(
                (
                    node
                    for node in context["linked_records"]
                    if node["attributes"].get("decision_type") == "human_finding_review"
                ),
                key=lambda node: node["attributes"].get("reviewed_at", ""),
            )
            if amendments:
                amendment = amendments[-1]["attributes"]
                body += (
                    '<section class="notice"><h3>Latest human amendment</h3>'
                    + badge(amendment["classification"])
                    + " "
                    + badge(amendment["severity"])
                    + "<p>"
                    + escape(amendment["statement"])
                    + '</p><p class="muted">'
                    + escape(amendment["reviewed_by"])
                    + " · "
                    + escape(amendment["rationale"])
                    + "</p></section>"
                )
            body += (
                "<details><summary>Amend finding or change classification / severity</summary>"
                + action(
                    project,
                    view,
                    item["node_id"],
                    "review_finding",
                    "Save human amendment",
                    context["output_digest"],
                    '<label>Your revised interpretation<textarea name="statement" maxlength="6000" required></textarea></label><label>Classification<select name="classification" required><option value="" selected disabled>Choose a classification</option>'
                    + "".join(
                        "<option>" + escape(value) + "</option>"
                        for value in CLASSIFICATIONS
                    )
                    + '</select></label><label>Severity<select name="severity" required><option value="" selected disabled>Choose severity</option>'
                    + "".join(
                        "<option>" + value + "</option>"
                        for value in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
                    )
                    + "</select></label>",
                )
                + "</details>"
            )
            body += (
                '<p><a href="/desk">Advanced controls: result verification and execution →</a></p>'
                + details("Issue context and decision history", context)
            )
            parts.append('<article class="card">' + body + "</article>")
        if not parts:
            parts.append(
                empty(
                    "No matching findings",
                    "Try another search or open another project.",
                )
            )
    elif view == "runs":
        from engineering_ai_system.project_run import ProjectRunService

        for run in runs:
            if record and record != run["run_id"]:
                continue
            if search and search.casefold() not in json.dumps(run).casefold():
                continue
            run_class = run.get("execution_class", "UNKNOWN")
            run_service = None
            gate_states = {}
            try:
                run_service = ProjectRunService(
                    registry,
                    DocumentAIWorkflow(registry),
                    execution_mode=run.get("execution_mode", "GOVERNED"),
                )
                gate_states = {
                    pointer: run_service.get_gate_status(project, run["run_id"], phase)
                    for pointer, phase in [
                        ("gate1_review_id", "plan"),
                        ("gate2_review_id", "outcome"),
                    ]
                }
            except (ValueError, KeyError) as exc:
                gate_states = {
                    pointer: {"status": "UNKNOWN", "current": False, "reason": str(exc)}
                    for pointer in ["gate1_review_id", "gate2_review_id"]
                }
            body = (
                '<p class="eyebrow">Project run · '
                + escape(run["run_id"])
                + "</p><h2>"
                + escape(run_class.title())
                + " workflow</h2>"
            )
            for label, pointer in [
                ("Reviewer Gate 1 · plan", "gate1_review_id"),
                ("Human plan", "human_plan_approval_id"),
                ("Reviewer Gate 2 · outcome", "gate2_review_id"),
                ("Human outcome / delegated disposition", "outcome_id"),
            ]:
                if pointer in gate_states:
                    state = gate_states[pointer]
                    value = state["status"]
                    body += '<p class="muted">' + escape(state["reason"]) + "</p>"
                    if state.get("review"):
                        body += details(label + " retained review", state["review"])
                elif label == "Human plan" and run_class != "critical":
                    value = "By authority threshold"
                else:
                    namespace = (
                        "project_run_outcomes"
                        if pointer == "outcome_id"
                        else "project_run_approvals"
                        if pointer == "human_plan_approval_id"
                        else "project_run_reviews"
                    )
                    retained = registry.get_control_record(
                        project, namespace, run.get(pointer) or ""
                    )
                    value = (
                        retained.get(
                            "disposition",
                            retained.get("status", "Recorded; read details"),
                        )
                        if retained
                        else "pending"
                    )
                    if retained:
                        body += details(label + " record", retained)
                body += (
                    '<p class="gate"><strong>'
                    + label
                    + "</strong>"
                    + badge(value)
                    + "</p>"
                )
            try:
                if run_service is None:
                    raise ValueError("Run service unavailable")
                schedule = run_service.get_schedule(project, run["run_id"])
                graph = {
                    item["task_id"]: item["depends_on"] for item in schedule["tasks"]
                }
                body += '<h3>Dependency graph</h3><div class="task-graph">'
                for task in schedule["tasks"]:
                    predecessors = ", ".join(task["depends_on"]) or "Start of workflow"
                    body += (
                        '<div class="task"><p class="muted">'
                        + escape(predecessors)
                        + " →</p><h3>"
                        + escape(task["task_id"])
                        + "</h3>"
                        + badge(task["state"])
                        + "<p>"
                        + escape(
                            ", ".join(task.get("block_reasons", []))
                            or "No schedule blocker recorded"
                        )
                        + '</p><a href="'
                        + escape(
                            link(project, view, run["run_id"], impact=task["task_id"])
                        )
                        + '">Preview rerun impact →</a></div>'
                    )
                    retained_task = run_service.brain.execution.get_task(
                        project, task["task_id"]
                    )
                    if retained_task["state"] == "proposed":
                        body += action(
                            project,
                            view,
                            task["task_id"],
                            "authorize",
                            "Authorise this task",
                            task["task_digest"],
                        )
                    elif task["state"] == "ready":
                        body += action(
                            project,
                            view,
                            task["task_id"],
                            "execute_run_task",
                            "Run this V2 task",
                            task["task_digest"],
                            '<input type="hidden" name="run_id" value="'
                            + escape(run["run_id"])
                            + '">',
                        )
                body += "</div>"
                if impact:
                    affected = downstream(graph, impact)
                    body += (
                        '<div class="notice"><strong>Rerun impact preview</strong><p>Potentially affected: '
                        + escape(", ".join(affected))
                        + "</p><p>A fresh run and new approvals are required. This preview changes no records and starts no execution.</p></div>"
                    )
                body += details(
                    "V2 task schedule and retained plan",
                    {"schedule": schedule, "run": run},
                )
                gate = gate_states.get("gate1_review_id", {})
                if (
                    run_class == "critical"
                    and not run.get("human_plan_approval_id")
                    and gate.get("current")
                    and gate.get("status") == "aligned"
                ):
                    body += action(
                        project,
                        view,
                        run["run_id"],
                        "approve_critical_plan",
                        "Approve Critical plan",
                        run["plan_digest"],
                    )
                if gate_states.get("gate2_review_id", {}).get(
                    "current"
                ) and not run.get("outcome_id"):
                    body += action(
                        project,
                        view,
                        run["run_id"],
                        "decide_run",
                        "Record project outcome",
                        digest(run_service.get_status(project, run["run_id"])),
                        '<label>Outcome<select name="disposition"><option value="hold">Hold</option><option value="rework">Rework</option><option value="accept">Accept</option></select></label><label>Supporting evidence IDs<input name="evidence_ids" required></label>',
                    )
            except (ValueError, KeyError) as exc:
                body += (
                    '<p class="notice">UNKNOWN / INSUFFICIENT INFORMATION: '
                    + escape(str(exc))
                    + "</p>"
                )
            body += '<p><a href="/desk">Open plan approvals and execution controls →</a></p>'
            parts.append('<article class="card">' + body + "</article>")
        if not parts:
            parts.append(
                empty(
                    "No project runs yet",
                    "Create a controlled plan through the existing workflow. Task execution begins only after its required approvals.",
                )
            )
    elif view == "knowledge":
        candidates = registry.list_control_records(project, "knowledge_candidates")
        parts.append(
            '<section class="card"><p class="eyebrow">Controlled learning</p><h2>Experience becomes knowledge through review</h2><p>Project lesson → candidate → AI generalisation → human validation → organisational knowledge. Current project requirements govern every reuse.</p><p class="muted">Organisational promotion requires the separately configured organisational Registry. A project lesson is not validated organisational knowledge.</p></section>'
        )
        lessons = selected([item for item in nodes if item["node_type"] == "lesson"])
        parts.extend(source_card(item) for item in lessons)
        for candidate in candidates:
            if record and candidate.get("candidate_id") != record:
                continue
            if search and search.casefold() not in json.dumps(candidate).casefold():
                continue
            proposal = candidate.get("proposal") or {}
            candidate_body = "".join(
                "<h3>"
                + escape(label)
                + "</h3><p>"
                + escape(proposal.get(key, "UNKNOWN / INSUFFICIENT INFORMATION"))
                + "</p>"
                for label, key in [
                    ("Generalised statement", "validated_statement"),
                    ("Applicability", "applicability"),
                    ("Limitations", "limitations"),
                ]
            )
            candidate_body += (
                "<p>Supporting project: "
                + escape(project)
                + " · lesson "
                + escape(candidate.get("project_lesson_id"))
                + "</p>"
            )
            validation = candidate.get("validation")
            if validation:
                candidate_body += (
                    "<p>Human validation: "
                    + escape(validation.get("validated_by"))
                    + " · "
                    + escape(validation.get("validated_at"))
                    + "</p><p>"
                    + escape(validation.get("rationale"))
                    + "</p><p>Organisational knowledge: "
                    + escape(validation.get("organizational_lesson_id"))
                    + "</p>"
                )
            if candidate.get("status") == "proposed":
                candidate_body += action(
                    project,
                    view,
                    candidate["candidate_id"],
                    "validate_candidate",
                    "Reject candidate",
                    candidate["candidate_digest"],
                    '<input type="hidden" name="accept" value="no">',
                )
                if organization_configured and registry.principal.organization_ids:
                    candidate_body += action(
                        project,
                        view,
                        candidate["candidate_id"],
                        "validate_candidate",
                        "Validate and promote knowledge",
                        candidate["candidate_digest"],
                        '<input type="hidden" name="accept" value="yes"><label>Organisational Registry<select name="organization_id">'
                        + "".join(
                            "<option>" + escape(value) + "</option>"
                            for value in sorted(registry.principal.organization_ids)
                        )
                        + '</select></label><label>New knowledge ID<input name="organization_lesson_id" required></label>',
                    )
                else:
                    candidate_body += '<p class="notice">Promotion is unavailable until the host grants a separate organisational Registry. No organisational approval has been inferred.</p>'
            parts.append(
                '<article class="card"><h2>Candidate '
                + escape(candidate.get("candidate_id", "UNKNOWN"))
                + "</h2>"
                + badge(candidate.get("status", "proposed"))
                + candidate_body
                + details(
                    "Supporting projects, statement, applicability and validation",
                    candidate,
                )
                + "</article>"
            )
        if not lessons and not candidates:
            parts.append(
                empty(
                    "No lessons or candidates yet",
                    "Learning begins with a retained project outcome and supporting evidence.",
                )
            )
        if organization_registry:
            parts.append("<h2>Validated organisational knowledge</h2>")
            for organization_id in sorted(registry.principal.organization_ids):
                scope = "ORG:" + organization_id
                for knowledge in organization_registry.list_records(scope, "lesson"):
                    if (
                        search
                        and search.casefold() not in json.dumps(knowledge).casefold()
                    ):
                        continue
                    attrs = knowledge["attributes"]
                    status = organization_registry.get_control_record(
                        scope, "organizational_lesson_status", knowledge["node_id"]
                    )
                    parts.append(
                        '<article class="card"><p class="eyebrow">Organisational Registry · '
                        + escape(scope)
                        + "</p><h2>"
                        + escape(knowledge["title"])
                        + "</h2>"
                        + badge(status.get("status") if status else attrs.get("status"))
                        + "<blockquote>"
                        + escape(attrs.get("validated_statement"))
                        + "</blockquote><h3>Applicability</h3><p>"
                        + escape(attrs.get("applicability"))
                        + "</p><h3>Limitations</h3><p>"
                        + escape(attrs.get("limitations"))
                        + "</p><p>Validated by "
                        + escape(attrs.get("validated_by"))
                        + " · Review due "
                        + escape(attrs.get("review_due"))
                        + "</p>"
                        + details(
                            "Supporting project snapshots and retained knowledge",
                            knowledge,
                        )
                        + "</article>"
                    )
    elif view == "activity":
        events = registry.list_control_records(project, "events")
        events = sorted(
            events, key=lambda item: item.get("occurred_at", ""), reverse=True
        )
        for event in events:
            if search and search.casefold() not in json.dumps(event).casefold():
                continue
            role = event.get("actor_role", "UNKNOWN")
            parts.append(
                '<article class="card event"><p class="eyebrow">'
                + escape(event.get("occurred_at"))
                + " · "
                + badge(
                    "Human"
                    if role in {"reviewer", "admin", "engineer"}
                    else "AI / service"
                    if role != "UNKNOWN"
                    else "UNKNOWN actor"
                )
                + "</p><h3>"
                + escape(event.get("kind", "Recorded event").replace("_", " "))
                + "</h3><p>"
                + escape(event.get("actor_id"))
                + " · "
                + escape(event.get("record_id"))
                + "</p><p>"
                + escape(event.get("rationale", "No reason retained in this event"))
                + "</p>"
                + details("Identity and event details", event)
                + "</article>"
            )
        if not parts:
            parts.append(
                empty(
                    "No matching history",
                    "Recorded human and service actions appear here.",
                )
            )
    else:
        kinds = (
            {"document", "document_revision"}
            if view == "revisions"
            else {
                "evidence",
                "requirement",
                "finding",
                "action",
                "decision",
                "verification",
                "result",
                "assessment",
                "alignment_review",
                "task",
                "submission",
                "assumption",
                "parameter",
                "risk",
            }
        )
        if view == "revisions":
            parts.append(
                '<section class="card"><h2>Revision lineage and current basis</h2><p>Read supersession links and source authority decisions together. A newer date alone does not establish governing authority.</p></section>'
            )
        for item in selected([item for item in nodes if item["node_type"] in kinds]):
            parts.append(source_card(item))
            if view == "revisions":
                related_evidence = [
                    node
                    for node in nodes
                    if node["node_type"] == "evidence"
                    and node["attributes"].get("document_id")
                    in {
                        item["node_id"],
                        item["attributes"].get("document_id"),
                        item["node_id"].removeprefix("document:"),
                    }
                    and (
                        item["node_type"] != "document_revision"
                        or node["attributes"].get("revision")
                        == item["attributes"].get("revision")
                    )
                ]
                parts.extend(source_card(node) for node in related_evidence)
        if not parts:
            parts.append(
                empty(
                    "No matching records",
                    "Try a different search. Source links appear when evidence has been imported.",
                )
            )

    navigation = "".join(
        f'<a class="nav {"active" if key == view else ""}" href="{escape(link(project, key))}" {"aria-current=page" if key == view else ""}><span class="nav-icon">{index:02}</span>{label}</a>'
        for index, (key, (label, _)) in enumerate(VIEWS.items(), 1)
    )
    selector = (
        '<form method="get" class="project-picker"><input type="hidden" name="view" value="'
        + view
        + '"><label>Current project<select name="project">'
        + "".join(
            f'<option value="{escape(value)}" {"selected" if value == project else ""}>{escape(value)}</option>'
            for value in sorted(registry.principal.project_ids)
        )
        + '</select></label><button class="small" type="submit">Open project</button></form>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + VIEWS[view][0]
        + " · Engineering Workbench</title><style>"
        + CSS
        + '</style></head><body><a class="skip" href="#main">Skip to content</a><aside class="sidebar"><a class="brand" href="'
        + escape(link(project, "overview"))
        + '"><span class="brand-mark">E</span><span>Engineering AI<small>PROJECT WORKBENCH</small></span></a>'
        + selector
        + '<nav aria-label="Main navigation">'
        + navigation
        + '</nav><div class="sidebar-foot"><span class="live-dot"></span> Local supervised workspace<p>Human decisions. Traceable evidence.</p><a href="/desk">Advanced review desk ↗</a></div></aside><main id="main"><header class="topbar"><span>WORKSPACE / '
        + escape(project)
        + '</span><span class="identity">Reviewing as '
        + escape(registry.principal.actor_id)
        + '</span></header><section class="page-head"><p class="eyebrow">ENGINEERING WORKBENCH</p><h1>'
        + VIEWS[view][0]
        + "</h1><p>"
        + VIEWS[view][1]
        + "</p></section>"
        + (
            '<div class="demo">Demonstration project · Invented evidence for exploring the workflow. No real project is approved.</div>'
            if demo
            else ""
        )
        + (
            '<p class="notice" role="status">' + escape(notice) + "</p>"
            if notice
            else ""
        )
        + '<form class="search" method="get"><input type="hidden" name="view" value="'
        + view
        + '"><input type="hidden" name="project" value="'
        + escape(project)
        + '"><label for="search">Search this view</label><div><input id="search" name="q" value="'
        + escape(search)
        + '" maxlength="200" placeholder="Find a source, finding or task…"><button type="submit">Search</button></div></form>'
        + "".join(parts)
        + '<footer>Engineering AI supports professional judgement. Human engineers retain technical authority. <a href="/desk">All controlled workflow actions</a></footer></main></body></html>'
    )


CSS = """
:root{--ink:#183333;--muted:#657979;--line:#dce6e2;--teal:#087d70;--paper:#f5f7f4}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}button,.button{background:var(--teal);border:0;color:white;border-radius:7px;padding:10px 18px;font:600 14px/1.5 inherit;cursor:pointer;display:inline-block}button:hover,.button:hover{background:#065f56;text-decoration:none}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid #e8b54b;outline-offset:3px}.sidebar{width:258px;background:#143737;color:#d0e4de;position:fixed;inset:0 auto 0 0;padding:30px 23px;display:flex;flex-direction:column}.brand{display:flex;gap:12px;align-items:center;color:white;font-size:18px;font-weight:650;margin-bottom:38px}.brand-mark{background:#76c9b4;color:#143737;border-radius:9px;display:grid;place-items:center;width:39px;height:42px;font-size:24px}.brand small{display:block;font-size:9px;letter-spacing:2px;color:#abc5bc}.project-picker{margin-bottom:26px}.project-picker label{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#b1c8c1}.project-picker select{margin:7px 0 10px;background:#204545;border-color:#416460;color:white;width:100%}.small{padding:5px 10px;font-size:12px}.nav{display:flex;gap:14px;padding:12px 13px;margin:4px 0;border-radius:7px;color:#c8d9d4;font-size:14px}.nav.active{background:#2c5550;color:#fff}.nav-icon{color:#87aaa0;font-size:11px;align-self:center}.sidebar-foot{margin-top:auto;padding-top:30px;font-size:11px;color:#a7c0b8}.sidebar-foot a{color:#cde6dc}.live-dot{display:inline-block;width:7px;height:7px;background:#87d3a9;border-radius:50%;margin-right:5px}main{margin-left:258px;padding:0 46px 25px;max-width:1550px}.topbar{display:flex;justify-content:space-between;gap:15px;border-bottom:1px solid var(--line);padding:20px 0;color:var(--muted);font-size:11px;letter-spacing:1px}.identity{letter-spacing:0}.page-head{padding:30px 0 15px}.eyebrow{font-size:10px;letter-spacing:1.6px;text-transform:uppercase;color:var(--muted);margin:0 0 8px}h1{font-size:36px;font-weight:650;letter-spacing:-1px;line-height:1.2;margin:0 0 10px}h2{font-size:21px;letter-spacing:-.4px;margin:8px 0 13px;line-height:1.35}h3{font-size:16px;margin:10px 0}.page-head>p:last-child{color:var(--muted);margin:0}.demo{background:#f4eedc;color:#74603b;border:1px solid #e6dcbe;border-radius:7px;padding:10px 16px;font-size:12px;margin:10px 0 20px}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:25px;margin:18px 0;box-shadow:0 3px 10px #17322c03;overflow-wrap:anywhere}.two-column{display:grid;grid-template-columns:1fr 1fr;gap:20px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin:23px 0}.metric{background:white;padding:20px;border:1px solid var(--line);border-radius:10px;color:var(--ink)}.metric span{font-size:32px;font-weight:600;line-height:1}.metric p{font-size:12px;color:var(--muted);margin:10px 0 0}.badge{display:inline-block;font-size:11px;padding:3px 9px;border-radius:5px;background:#edf1f0;color:#5b6e69}.good{background:#e2f1e8;color:#2b7153}.warn{background:#fbefcf;color:#8a691a}.muted{color:var(--muted);font-size:13px}blockquote{border-left:3px solid #81b7a5;margin:15px 0;padding:12px 17px;background:#f5f8f5;white-space:pre-wrap}.source-links{padding-left:20px;font-size:14px}.source-links li{margin:8px 0}details{margin-top:15px;border-top:1px solid var(--line);padding-top:10px}summary{cursor:pointer;font-size:12px;color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f4;padding:15px;font-size:11px}label{display:block;font-size:13px;font-weight:550}input,select,textarea{font:inherit;background:white;border:1px solid #ccd9d2;border-radius:6px;padding:9px 11px;max-width:100%;color:var(--ink)}textarea{display:block;width:100%;margin:6px 0 12px}.search{margin:0 0 24px}.search>label{font-size:11px;color:var(--muted);margin-bottom:5px}.search>div{display:flex;gap:8px}.search input{width:100%;max-width:510px}.decision{background:#f7faf7;border:1px solid var(--line);border-radius:8px;padding:18px;margin-top:20px}.decision select{display:block;margin:6px 0 13px;width:100%}.gate{display:flex;align-items:center;justify-content:space-between;gap:15px;border-bottom:1px solid var(--line);padding:8px 0;font-size:13px}.task-graph{display:flex;flex-wrap:wrap;gap:15px;margin:18px 0}.task{flex:1 1 220px;border:1px solid #bdd8ce;border-radius:8px;padding:16px;background:#f6faf7;font-size:12px}.notice{background:#fff4d5;border-left:3px solid #c99a32;padding:15px;border-radius:4px}.empty{text-align:center;padding:45px 20px;border:1px dashed #cbd9d1;border-radius:12px;color:var(--muted);margin:20px 0}.empty-mark{font-size:36px;color:#8aada0}.empty p{max-width:500px;margin:12px auto;font-size:14px}footer{font-size:11px;color:var(--muted);padding:30px 0;border-top:1px solid var(--line);margin-top:25px}.skip{position:absolute;left:-9999px}.skip:focus{left:270px;top:4px;background:white;padding:8px;z-index:2}@media(min-width:1550px){main{margin-right:auto}}@media(max-width:1000px){main{padding:0 25px 25px}.sidebar{width:225px;padding:25px 15px}main{margin-left:225px}.metrics{grid-template-columns:repeat(2,1fr)}.two-column{grid-template-columns:1fr}}@media(max-width:650px){.sidebar{position:static;width:100%;padding:18px}.brand{margin-bottom:16px}.sidebar nav{display:flex;flex-wrap:wrap}.nav{font-size:12px;padding:7px 10px}.nav-icon,.sidebar-foot{display:none}.project-picker{margin:0}.project-picker label{display:inline-block}.project-picker select{width:auto;margin:0 8px}.topbar{flex-wrap:wrap}.page-head{padding-top:23px}main{margin:0;padding:0 18px 20px}h1{font-size:30px}.card{padding:19px}.metrics{gap:10px}.metric{padding:16px}.gate{align-items:flex-start}.skip:focus{left:10px}}
"""
