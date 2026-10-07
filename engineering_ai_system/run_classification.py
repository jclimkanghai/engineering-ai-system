"""Conservative, evidence-bound execution class selection."""

from __future__ import annotations

from engineering_registry.execution_policy import (
    current_execution_policy,
    require_policy_tasks,
)
from engineering_registry.service import RegistryService, digest

_RANK = {"routine": 0, "engineering": 1, "critical": 2}
_CRITICAL_DOMAINS = {
    "safety",
    "regulatory",
    "contractual",
    "major_design",
    "client_decision",
}


def classify_run(
    registry: RegistryService,
    project: str,
    issue_id: str,
    requested_class: str,
    tasks: list[dict],
    policy_id: str | None = None,
    plans: list[dict] | None = None,
) -> dict:
    """Raise the requested class to the floor established by retained context."""
    registry.check_access(project)
    if requested_class not in _RANK:
        raise ValueError("Unknown execution class")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Execution classification requires bounded tasks")
    brief = registry.client_project_brief(project)
    mandate = registry.client_mandate(project, issue_id)
    issue_context = registry.issue_context(project, issue_id)
    issue = issue_context["issue"]
    reasons: list[str] = []
    floor = "routine"
    hold = False
    if not brief or not mandate:
        floor, hold = "critical", True
        reasons.append("Client brief or mandate is missing")
    else:
        definition = mandate["definition"]
        if (
            definition["risk_level"] == "unknown"
            or definition["importance_level"] == "unknown"
            or definition["unknowns"]
            or brief["unknowns"]
            or issue["risk_level"] == "unknown"
            or issue["unknowns"]
        ):
            floor, hold = "critical", True
            reasons.append("Risk, importance or project context is uncertain")
        elif (
            definition["risk_level"] in {"high", "critical"}
            or definition["importance_level"] in {"high", "critical"}
            or issue["risk_level"] in {"high", "critical"}
            or set(definition["consequence_domains"] + issue["risk_dimensions"])
            & _CRITICAL_DOMAINS
        ):
            floor = "critical"
            reasons.append("High consequence or governing authority requires Critical")
        elif (
            definition["risk_level"] != "low"
            or definition["importance_level"] != "low"
            or issue["risk_level"] != "low"
            or not definition["simple"]
            or not definition["reversible"]
            or definition["consequence_domains"]
            or issue["risk_dimensions"]
        ):
            floor = "engineering"
            reasons.append("Task is outside the low-risk Routine boundary")
        if any(item["tool"] not in definition["allowed_tools"] for item in tasks):
            floor, hold = "critical", True
            reasons.append("Tool is outside the human client mandate")
    for record in issue_context["linked_records"]:
        attrs = record.get("attributes", {})
        if record["node_type"] == "requirement" and (
            attrs.get("uncertainties")
            or attrs.get("authority_status") in {"unverified", "superseded", "unknown"}
        ):
            floor, hold = "critical", True
            reasons.append(
                "Linked project requirement has unresolved authority or uncertainty"
            )
        if record["node_type"] == "evidence" and not attrs.get("text"):
            floor, hold = "critical", True
            reasons.append("Linked source evidence has no reviewable text")
    for plan in plans or []:
        if plan.get("unknowns") or plan.get("knowledge_conflicts"):
            floor, hold = "critical", True
            reasons.append("Lead plan has unresolved uncertainty or knowledge conflict")
        findings = (plan.get("analysis") or {}).get("findings", [])
        decisions = (plan.get("analysis") or {}).get("task_decisions", [])
        if decisions:
            if any(item.get("decision_level") in {"D3", "D4"} for item in decisions):
                floor = "critical"
                reasons.append(
                    "Lead plan proposes a human-controlled material decision"
                )
            elif floor == "routine":
                floor = "engineering"
                reasons.append("Lead plan proposes a material project decision")
        if any(
            finding.get("risk_level") in {"high", "critical", "unknown"}
            or set(finding.get("risk_dimensions", [])) & _CRITICAL_DOMAINS
            for finding in findings
        ):
            floor = "critical"
            reasons.append("Lead analysis identifies material consequence")
    selected = max((requested_class, floor), key=_RANK.__getitem__)
    policy = None
    if selected == "routine":
        policy = current_execution_policy(registry, project, policy_id)
        require_policy_tasks(policy, tasks)
        reasons.append("Active human-approved low-risk execution policy")
    source = {"issue": issue, "brief": brief, "mandate": mandate, "plans": plans or []}
    return {
        "execution_class": selected,
        "status": "hold" if hold else "ready",
        "reasons": reasons or ["Requested class satisfies project consequence floor"],
        "policy_id": policy_id if policy else None,
        "policy_digest": policy["policy_digest"] if policy else None,
        "source_digest": digest(source),
    }
