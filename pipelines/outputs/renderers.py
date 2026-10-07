from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any


def _value(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN / INSUFFICIENT INFORMATION"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_value(item) for item in value) or "—"
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_value(item)}" for key, item in value.items())
    return str(value)


def _md(value: Any) -> str:
    return _value(value).replace("|", "\\|").replace("\n", "<br>")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        rows = [["No records"] + [""] * (len(headers) - 1)]
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_md(cell) for cell in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def _as_rows(values: list[Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in values if isinstance(item, dict)]


def render_review_markdown(
    *,
    review_mode: str,
    findings: list[dict[str, Any]],
    outputs: dict[str, list[dict[str, Any]]],
    qa: dict[str, Any],
    coverage: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    analysis_status: str,
    approval_status: str,
    output_digest: str,
    approval_record: dict[str, Any] | None = None,
    finding_registry_reconciliation: dict[str, Any] | None = None,
    finding_qualification_gate: dict[str, Any] | None = None,
    qualification_trace: list[dict[str, Any]] | None = None,
) -> str:
    critical = sum(item.get("risk_level") == "critical" for item in findings)
    high = sum(item.get("risk_level") == "high" for item in findings)
    needs_review = sum(
        bool(item.get("human_review_required", True)) for item in findings
    )
    reviewed = sum(
        item.get("coverage_status") == "context_retrieved" for item in coverage
    )
    reviewer_line = ""
    if approval_record:
        reviewer_line = (
            f"**Reviewer recorded:** {_md(approval_record.get('reviewer_id'))}  \n"
            f"**Reviewed at:** {_md(approval_record.get('reviewed_at'))}  \n"
        )
    lines = [
        f"# Engineering document review — {review_mode}",
        "",
        f"**Analysis:** {analysis_status}  ",
        f"**QA:** {qa.get('status', 'UNKNOWN')}  ",
        f"**Human approval:** {approval_status}  ",
        reviewer_line.rstrip(),
        f"**Requirements with retrieved context:** {reviewed}/{len(coverage)}  ",
        f"**Findings:** {len(findings)} · **High/Critical:** {high + critical} · **Human review required:** {needs_review}",
        "",
        f"Output digest: `{output_digest}`",
        "",
        "## Findings",
        "",
        _markdown_table(
            [
                "ID",
                "Title / finding",
                "Status",
                "Risk",
                "Evidence IDs",
                "Requirement IDs",
                "Action",
                "Human review",
            ],
            [
                [
                    row.get("finding_id"),
                    row.get("title"),
                    row.get("status"),
                    row.get("risk_level"),
                    row.get("source_evidence_ids"),
                    row.get("requirement_ids"),
                    row.get("required_action") or row.get("recommendation"),
                    "Required" if row.get("human_review_required", True) else "No",
                ]
                for row in findings
            ],
        ),
        "",
        "## Compliance matrix",
        "",
    ]
    if finding_qualification_gate is not None:
        lines[lines.index("## Findings"):lines.index("## Findings")] = [
            "## Finding qualification",
            "",
            f"Gate: **{finding_qualification_gate.get('status', 'UNKNOWN')}**; candidates: {finding_qualification_gate.get('candidate_count', 0)}; qualified: {finding_qualification_gate.get('qualified_count', 0)}; Registry findings: {finding_qualification_gate.get('registry_finding_count', 0)}.",
            "Qualification trace is retained in the review record.",
            "",
        ]
    lines.append(
        _markdown_table(
            ["Finding ID", "Status", "Evidence IDs", "Requirement IDs"],
            [
                [
                    row.get("finding_id"),
                    row.get("status"),
                    row.get("source_evidence_ids"),
                    row.get("requirement_ids"),
                ]
                for row in _as_rows(outputs.get("compliance_matrix", []))
            ],
        )
    )
    lines.extend(["", "## Risk register", ""])
    risk_rows = _as_rows(outputs.get("risk_register", []))
    lines.append(
        _markdown_table(
            [
                "Finding ID",
                "Risk",
                "Description",
                "Consequence",
                "Mitigation",
                "Evidence IDs",
            ],
            [
                [
                    row.get("finding_id"),
                    row.get("data", {}).get("risk_level"),
                    row.get("data", {}).get("description"),
                    row.get("data", {}).get("consequence"),
                    row.get("data", {}).get("mitigation"),
                    row.get("source_evidence_ids"),
                ]
                for row in risk_rows
            ],
        )
    )
    lines.extend(["", "## Evidence coverage", ""])
    lines.append(
        _markdown_table(
            ["Requirement ID", "Coverage", "Supporting evidence IDs", "Count"],
            [
                [
                    row.get("requirement_id"),
                    row.get("coverage_status"),
                    row.get("supporting_evidence_ids"),
                    row.get("supporting_evidence_count"),
                ]
                for row in coverage
            ],
        )
    )
    lines.extend(["", "## Source evidence", ""])
    lines.append(
        _markdown_table(
            ["Evidence ID", "Document", "Revision", "Location", "Excerpt"],
            [
                [
                    row.get("evidence_id"),
                    row.get("document_number") or row.get("document_id"),
                    row.get("revision"),
                    row.get("locator") or row.get("section"),
                    row.get("text") or row.get("source_text"),
                ]
                for row in evidence
            ],
        )
    )
    registry = finding_registry_reconciliation or {}
    lines.extend(["", "## Project registry reconciliation", ""])
    lines.append(f"Status: **{_md(registry.get('status', 'UNKNOWN'))}**")
    lines.append("Automatic closure: **No**")
    lines.append("")
    lines.append(
        _markdown_table(
            [
                "Finding ID",
                "Match",
                "Existing register entries",
                "Action",
                "Human decision",
            ],
            [
                [
                    row.get("finding_id"),
                    row.get("match_type"),
                    row.get("existing_findings"),
                    row.get("automatic_action"),
                    "Required"
                    if row.get("human_lifecycle_decision_required")
                    else "No",
                ]
                for row in registry.get("matches", [])
            ],
        )
    )
    blockers = qa.get("blockers", [])
    lines.extend(
        [
            "",
            "## QA blockers",
            "",
            "- " + "\n- ".join(map(str, blockers)) if blockers else "None recorded.",
            "",
        ]
    )
    lines.extend(
        [
            "This report is an AI-assisted review record. Engineering, contractual, regulatory, safety, and design conclusions remain subject to competent human review.",
            "",
        ]
    )
    return "\n".join(lines)


def _svg_chart(title: str, values: dict[str, int], *, colour: str) -> str:
    positive = [
        (label, int(value)) for label, value in values.items() if int(value) > 0
    ]
    if not positive:
        positive = [("No records", 0)]
    width, row_height, left, right = 640, 32, 160, 54
    height = 52 + len(positive) * row_height
    maximum = max((value for _, value in positive), default=0) or 1
    bars = []
    for index, (label, value) in enumerate(positive):
        y = 38 + index * row_height
        bar_width = round((width - left - right) * value / maximum)
        bars.append(
            f'<text x="{left - 10}" y="{y + 15}" text-anchor="end">{escape(label)}</text>'
            f'<rect x="{left}" y="{y}" width="{max(bar_width, 0)}" height="20" rx="4" fill="{colour}" />'
            f'<text x="{left + bar_width + 8}" y="{y + 15}">{value}</text>'
        )
    return (
        f'<figure class="chart"><figcaption>{escape(title)}</figcaption>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
        f"<title>{escape(title)}</title><desc>Horizontal bar chart with counts for each category.</desc>"
        + "".join(bars)
        + "</svg></figure>"
    )


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        rows = [["No records"] + [""] * (len(headers) - 1)]
    head = "".join(f'<th scope="col">{escape(header)}</th>' for header in headers)
    body = []
    for row in rows:
        body.append(
            "<tr>"
            + "".join(f"<td>{escape(_value(cell))}</td>" for cell in row)
            + "</tr>"
        )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_review_html(
    *,
    review_mode: str,
    findings: list[dict[str, Any]],
    outputs: dict[str, list[dict[str, Any]]],
    qa: dict[str, Any],
    coverage: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    analysis_status: str,
    approval_status: str,
    output_digest: str,
    approval_record: dict[str, Any] | None = None,
    finding_registry_reconciliation: dict[str, Any] | None = None,
    finding_qualification_gate: dict[str, Any] | None = None,
    qualification_trace: list[dict[str, Any]] | None = None,
) -> str:
    statuses = Counter(str(item.get("status", "unknown")) for item in findings)
    risks = Counter(str(item.get("risk_level", "unknown")) for item in findings)
    coverage_counts = Counter(
        str(item.get("coverage_status", "unknown")) for item in coverage
    )
    reviewed = sum(
        item.get("coverage_status") == "context_retrieved" for item in coverage
    )
    high = risks.get("high", 0) + risks.get("critical", 0)
    review_count = sum(
        bool(item.get("human_review_required", True)) for item in findings
    )
    findings_rows = [
        [
            row.get("finding_id"),
            row.get("title"),
            row.get("status"),
            row.get("risk_level"),
            row.get("finding"),
            row.get("source_evidence_ids"),
            row.get("requirement_ids"),
            row.get("required_action") or row.get("recommendation"),
            "Required" if row.get("human_review_required", True) else "No",
        ]
        for row in findings
    ]
    compliance_rows = [
        [
            row.get("finding_id"),
            row.get("status"),
            row.get("data", {}).get("compliance_status"),
            row.get("requirement_ids"),
            row.get("source_evidence_ids"),
        ]
        for row in _as_rows(outputs.get("compliance_matrix", []))
    ]
    risk_rows = [
        [
            row.get("finding_id"),
            row.get("data", {}).get("risk_level"),
            row.get("data", {}).get("description"),
            row.get("data", {}).get("consequence"),
            row.get("data", {}).get("mitigation"),
            row.get("source_evidence_ids"),
        ]
        for row in _as_rows(outputs.get("risk_register", []))
    ]
    coverage_rows = [
        [
            row.get("requirement_id"),
            row.get("coverage_status"),
            row.get("supporting_evidence_ids"),
            row.get("warnings"),
        ]
        for row in coverage
    ]
    evidence_rows = [
        [
            row.get("evidence_id"),
            row.get("document_number") or row.get("document_id"),
            row.get("revision"),
            row.get("locator") or row.get("section"),
            row.get("text") or row.get("source_text"),
        ]
        for row in evidence
    ]
    registry = finding_registry_reconciliation or {}
    registry_rows = [
        [
            row.get("finding_id"),
            row.get("match_type"),
            row.get("existing_findings"),
            row.get("automatic_action"),
            "Required" if row.get("human_lifecycle_decision_required") else "No",
        ]
        for row in registry.get("matches", [])
    ]
    blocker_html = (
        "<p>None recorded.</p>"
        if not qa.get("blockers")
        else "<ul>"
        + "".join(f"<li>{escape(str(item))}</li>" for item in qa["blockers"])
        + "</ul>"
    )
    status_chart = _svg_chart("Findings by status", dict(statuses), colour="#176b87")
    risk_chart = _svg_chart(
        "Findings by risk level",
        {
            key: risks.get(key, 0)
            for key in ("critical", "high", "medium", "low", "unknown")
        },
        colour="#a63838",
    )
    coverage_chart = _svg_chart(
        "Requirement evidence coverage", dict(coverage_counts), colour="#4f7d55"
    )
    approval_message = (
        "Approval is required before this review can be marked complete."
        if approval_status == "pending"
        else "This report does not authenticate the reviewer identity."
        if approval_status == "approved"
        else "Human approval was not required for this run."
        if approval_status == "not_required"
        else "No human approval was recorded for this run."
    )
    reviewer_message = ""
    if approval_record:
        reviewer_message = (
            f"<p>Reviewer recorded: {escape(str(approval_record.get('reviewer_id', 'UNKNOWN')))}"
            f" · Reviewed at: {escape(str(approval_record.get('reviewed_at', 'UNKNOWN')))}</p>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Engineering review — {escape(review_mode)}</title>
<style>
:root{{color-scheme:light;--ink:#132235;--muted:#526274;--line:#d8e0e8;--blue:#176b87;--red:#a63838;--amber:#8a5a00;--paper:#fff;--wash:#f3f6f8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--wash);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1280px;margin:auto;padding:28px}}h1{{margin:0 0 4px;font-size:28px}}h2{{margin:30px 0 10px;font-size:20px}}p{{margin:6px 0}}.sub{{color:var(--muted)}}
.banner{{margin:18px 0;padding:14px 16px;border-left:5px solid var(--amber);background:#fff8e7}}.banner.approved{{border-color:var(--blue);background:#eaf5f8}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}}.card{{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:14px}}.card strong{{display:block;font-size:25px}}.card span{{color:var(--muted);font-size:13px}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}.chart{{background:#fff;border:1px solid var(--line);border-radius:9px;padding:14px;margin:0;overflow:hidden}}.chart figcaption{{font-weight:700;margin-bottom:8px}}svg{{width:100%;height:auto}}svg text{{fill:var(--ink);font:13px sans-serif}}
.table-wrap{{overflow-x:auto;background:#fff;border:1px solid var(--line);border-radius:8px}}table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{text-align:left;vertical-align:top;padding:9px 11px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}}th{{background:#e9eff3;font-size:13px}}tbody tr:nth-child(even){{background:#f8fafb}}code{{overflow-wrap:anywhere}}.digest{{padding:10px;background:#edf1f4;border-radius:6px;font:12px ui-monospace,monospace}}footer{{margin-top:36px;border-top:1px solid var(--line);padding-top:12px;color:var(--muted);font-size:13px}}
@media(max-width:640px){{main{{padding:16px}}h1{{font-size:23px}}}}
@media print{{body{{background:#fff}}main{{max-width:none;padding:0}}.table-wrap,.chart,.card{{break-inside:avoid}}}}
</style></head><body><main>
<header><h1>Engineering document review</h1><p class="sub">Review mode: {escape(review_mode)} · Analysis: {escape(analysis_status)} · QA: {escape(str(qa.get("status", "UNKNOWN")))}</p></header>
<section class="banner {"approved" if approval_status == "approved" else ""}" role="status"><strong>Human approval: {escape(approval_status.replace("_", " ").title())}</strong><p>{escape(approval_message)}</p>{reviewer_message}</section>
<section class="cards" aria-label="Review summary">
<div class="card"><strong>{len(findings)}</strong><span>Validated findings</span></div>
<div class="card"><strong>{high}</strong><span>High / critical risks</span></div>
<div class="card"><strong>{review_count}</strong><span>Findings needing human review</span></div>
<div class="card"><strong>{reviewed}/{len(coverage)}</strong><span>Requirements with retrieved context</span></div>
<div class="card"><strong>{len(qa.get("blockers", []))}</strong><span>Engineering QA blockers</span></div>
</section>
<section class="charts" aria-label="Review charts">
{status_chart}
{risk_chart}
{coverage_chart}
</section>
<section><h2>Findings register</h2>{_html_table(["ID", "Title", "Status", "Risk", "Finding", "Evidence IDs", "Requirement IDs", "Action", "Human review"], findings_rows)}</section>
{("<section><h2>Finding qualification</h2><p>Gate: " + escape(str(finding_qualification_gate.get('status', 'UNKNOWN'))) + "; candidates: " + str(finding_qualification_gate.get('candidate_count', 0)) + "; qualified: " + str(finding_qualification_gate.get('qualified_count', 0)) + "; Registry findings: " + str(finding_qualification_gate.get('registry_finding_count', 0)) + ".</p><p>Qualification trace is retained in the review record.</p></section>") if finding_qualification_gate is not None else ""}
<section><h2>Project registry reconciliation</h2><p>Status: <strong>{escape(str(registry.get("status", "UNKNOWN")))}</strong> · Automatic closure: <strong>No</strong></p>{_html_table(["Finding ID", "Match", "Existing register entries", "Proposed action", "Human lifecycle decision"], registry_rows)}</section>
<section><h2>Compliance matrix</h2>{_html_table(["Finding ID", "Finding status", "Compliance status", "Requirement IDs", "Evidence IDs"], compliance_rows)}</section>
<section><h2>Risk register</h2>{_html_table(["Finding ID", "Risk", "Description", "Consequence", "Mitigation", "Evidence IDs"], risk_rows)}</section>
<section><h2>Evidence coverage</h2>{_html_table(["Requirement ID", "Coverage", "Retrieved evidence IDs", "Retrieval notes"], coverage_rows)}</section>
<section><h2>Source evidence</h2>{_html_table(["Evidence ID", "Document", "Revision", "Location", "Source excerpt"], evidence_rows)}</section>
<section><h2>Engineering QA</h2><p>Status: <strong>{escape(str(qa.get("status", "UNKNOWN")))}</strong></p>{blocker_html}</section>
<section><h2>Review record</h2><p>Output digest</p><p class="digest">{escape(output_digest)}</p></section>
<footer>AI-assisted review only. Engineering, contractual, regulatory, safety, and design decisions require competent human review. Counts and charts are derived from the structured review records above.</footer>
</main></body></html>"""
