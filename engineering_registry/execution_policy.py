"""Human-approved, project-scoped authority for Routine V2 execution."""

from __future__ import annotations

from datetime import UTC, date, datetime

from .service import RegistryService, digest, text, timestamp


def _definition(value: dict) -> dict:
    required = {
        "allowed_tasks",
        "max_risk_level",
        "max_importance_level",
        "require_evidence",
        "expires_on",
        "rationale",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Invalid Routine execution policy contract")
    if value["max_risk_level"] != "low" or value["max_importance_level"] != "low":
        raise ValueError("Routine policy permits low risk and importance only")
    if value["require_evidence"] is not True:
        raise ValueError("Routine policy requires evidence")
    try:
        due = date.fromisoformat(text(value["expires_on"], "expires_on"))
    except ValueError as exc:
        raise ValueError("Policy expires_on must be an ISO calendar date") from exc
    if due <= datetime.now(UTC).date():
        raise ValueError("Routine policy expiry must be in the future")
    text(value["rationale"], "rationale")
    tasks = value["allowed_tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
        raise ValueError("Routine policy requires 1 to 20 allowed tasks")
    for item in tasks:
        if not isinstance(item, dict) or set(item) != {"tool", "parameters"}:
            raise ValueError("Invalid Routine policy task")
        if item["tool"] not in {
            "compare_revision",
            "validate_traceability",
            "generate_review_pack",
        }:
            raise ValueError("Routine policy tool is not allow-listed")
        parameters = item["parameters"]
        expected = (
            {"base_evidence_id", "head_evidence_id"}
            if item["tool"] == "compare_revision"
            else set()
        )
        if not isinstance(parameters, dict) or set(parameters) != expected:
            raise ValueError("Routine policy task parameters must be bounded")
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 512
            for value in parameters.values()
        ):
            raise ValueError("Routine policy parameters require bounded identifiers")
    if len({digest(item) for item in tasks}) != len(tasks):
        raise ValueError("Duplicate Routine policy task")
    return value


def approve_execution_policy(
    registry: RegistryService, project: str, policy_id: str, definition: dict
) -> dict:
    registry.check_access(project, write=True, human=True)
    text(policy_id, "policy_id")
    _definition(definition)
    record = {
        "policy_id": policy_id,
        "project_id": project,
        "definition": definition,
        "approved_by": registry.principal.actor_id,
        "approved_at": timestamp(),
    }
    record["policy_digest"] = digest(record)
    with registry.store.transaction():
        if registry.store.get_record(project, "execution_policies", policy_id):
            raise ValueError("Execution policy ID already exists")
        registry.store.put_record(project, "execution_policies", policy_id, record)
        registry.store.put_record(
            project,
            "execution_policy_status",
            policy_id,
            {
                "active": True,
                "changed_by": registry.principal.actor_id,
                "changed_at": timestamp(),
            },
            replace=True,
        )
    return record


def revoke_execution_policy(
    registry: RegistryService, project: str, policy_id: str, rationale: str
) -> dict:
    registry.check_access(project, write=True, human=True)
    text(rationale, "rationale")
    policy = registry.store.get_record(project, "execution_policies", policy_id)
    if not policy:
        raise ValueError("Unknown Routine execution policy")
    status = {
        "active": False,
        "changed_by": registry.principal.actor_id,
        "changed_at": timestamp(),
        "rationale": rationale,
    }
    registry.store.put_record(
        project, "execution_policy_status", policy_id, status, replace=True
    )
    return status


def current_execution_policy(
    registry: RegistryService, project: str, policy_id: str | None
) -> dict:
    registry.check_access(project)
    policy = registry.store.get_record(project, "execution_policies", policy_id or "")
    status = registry.store.get_record(
        project, "execution_policy_status", policy_id or ""
    )
    if not policy or not status or not status["active"]:
        raise ValueError("Routine execution policy is missing or revoked")
    if (
        date.fromisoformat(policy["definition"]["expires_on"])
        <= datetime.now(UTC).date()
    ):
        raise ValueError("Routine execution policy expired")
    if (
        digest({k: v for k, v in policy.items() if k != "policy_digest"})
        != policy["policy_digest"]
    ):
        raise ValueError("Routine execution policy changed")
    return policy


def require_policy_tasks(policy: dict, tasks: list[dict]) -> None:
    allowed = {digest(item) for item in policy["definition"]["allowed_tasks"]}
    if any(
        digest({"tool": item["tool"], "parameters": item["parameters"]}) not in allowed
        for item in tasks
    ):
        raise ValueError("Task is outside Routine execution policy")
