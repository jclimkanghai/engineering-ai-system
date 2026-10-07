"""Human-defined authority and explicitly attributed AI result decisions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .models import GraphEdge, GraphNode, NodeType, RelationshipType

if TYPE_CHECKING:
    from .service import RegistryService

ALLOWED_TOOLS = {
    "compare_revision",
    "validate_traceability",
    "generate_review_pack",
    "validate_proposal_readiness",
    "external_solver",
}
DISPOSITIONS = {"accept", "reject", "rework", "hold"}
LEVELS = {"low", "medium", "high", "critical", "unknown"}


def string_list(value: Any, name: str, *, nonempty: bool = False) -> list[str]:
    from .service import text

    if not isinstance(value, list) or len(value) > 200 or (nonempty and not value):
        raise ValueError(f"{name} requires a bounded list")
    for item in value:
        text(item, name)
        if len(item) > 4000:
            raise ValueError(f"{name} item exceeds limit")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} contains duplicates")
    return value


def record_client_mandate(
    registry: RegistryService,
    project: str,
    mandate_id: str,
    issue_id: str,
    definition: dict,
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True, human=True)
    text(mandate_id, "mandate_id")
    fields = {
        "objective",
        "scope",
        "acceptance_criteria",
        "evidence_ids",
        "allowed_tools",
        "risk_level",
        "importance_level",
        "simple",
        "reversible",
        "consequence_domains",
        "unknowns",
    }
    if not isinstance(definition, dict) or set(definition) != fields:
        raise ValueError("Invalid client mandate contract")
    for key in ("objective", "scope"):
        text(definition[key], key)
    for key in ("acceptance_criteria", "evidence_ids", "allowed_tools"):
        string_list(definition[key], key, nonempty=True)
    for key in ("consequence_domains", "unknowns"):
        string_list(definition[key], key)
    if not set(definition["allowed_tools"]).issubset(ALLOWED_TOOLS):
        raise ValueError("Unknown mandate tool")
    if any(definition[k] not in LEVELS for k in ("risk_level", "importance_level")):
        raise ValueError("Unknown risk or importance level")
    if any(type(definition[k]) is not bool for k in ("simple", "reversible")):
        raise ValueError("Simplicity and reversibility must be explicit booleans")
    if len(str(definition)) > 50_000 or len(mandate_id) > 512:
        raise ValueError("Client mandate exceeds limit")
    with registry.store.transaction():
        context = registry.task_context(project, issue_id)
        if not set(definition["evidence_ids"]).issubset(context["evidence_ids"]):
            raise ValueError("Client mandate evidence must belong to the issue")
        source_digest = digest(context["execution_snapshot"])
        project_brief = registry.client_project_brief(project)
        previous = registry.store.get_record(project, "client_mandates", mandate_id)
        if previous:
            if (
                previous["issue_id"] != issue_id
                or previous["definition"] != definition
                or previous["source_digest"] != source_digest
                or previous.get("client_project_brief_digest")
                != (project_brief["brief_digest"] if project_brief else None)
            ):
                raise ValueError("Client mandate is immutable; use a new ID")
            return previous
        record = {
            "schema_version": 1,
            "project_id": project,
            "mandate_id": mandate_id,
            "issue_id": issue_id,
            "definition": deepcopy(definition),
            "source_digest": source_digest,
            **(
                {"client_project_brief_digest": project_brief["brief_digest"]}
                if project_brief
                else {}
            ),
            "approved_by": registry.principal.actor_id,
            "approved_at": timestamp(),
        }
        record["mandate_digest"] = digest(record)
        registry.store.put_record(project, "client_mandates", mandate_id, record)
        registry.store.put_record(
            project,
            "active_client_mandates",
            issue_id,
            {"mandate_id": mandate_id},
            replace=True,
        )
        registry._event(
            project, issue_id, "client_mandate_recorded", mandate_id=mandate_id
        )
        return record


def client_mandate(
    registry: RegistryService,
    project: str,
    issue_id: str,
    *,
    exclude_task_id: str | None = None,
) -> dict | None:
    from .service import digest

    registry.check_access(project)
    pointer = registry.store.get_record(project, "active_client_mandates", issue_id)
    if not pointer:
        return None
    record = registry.store.get_record(
        project, "client_mandates", pointer["mandate_id"]
    )
    if not record or record["source_digest"] != digest(
        registry.task_context(project, issue_id, exclude_task_id=exclude_task_id)[
            "execution_snapshot"
        ]
    ):
        raise ValueError(
            "Client mandate is stale; review the changed issue/source context"
        )
    project_brief = registry.client_project_brief(project)
    if record.get("client_project_brief_digest") != (
        project_brief["brief_digest"] if project_brief else None
    ):
        raise ValueError("Client project brief changed; refresh the task mandate")
    return record


def approve_delegation_policy(
    registry: RegistryService,
    project: str,
    policy_id: str,
    definition: dict,
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True, human=True)
    text(policy_id, "policy_id")
    fields = {
        "allowed_tools",
        "allowed_dispositions",
        "allowed_solver_ids",
        "rationale",
    }
    if not isinstance(definition, dict) or set(definition) != fields:
        raise ValueError("Invalid delegation policy contract")
    text(definition["rationale"], "rationale")
    for key in ("allowed_tools", "allowed_dispositions", "allowed_solver_ids"):
        string_list(definition[key], key, nonempty=key != "allowed_solver_ids")
    if not set(definition["allowed_tools"]).issubset(ALLOWED_TOOLS) or not set(
        definition["allowed_dispositions"]
    ).issubset(DISPOSITIONS):
        raise ValueError("Invalid policy tool or disposition")
    if len(str(definition)) > 50_000 or len(policy_id) > 512:
        raise ValueError("Policy exceeds limit")
    with registry.store.transaction():
        previous = registry.store.get_record(project, "delegation_policies", policy_id)
        if previous:
            if previous["definition"] != definition:
                raise ValueError("Delegation policy is immutable; use a new ID")
            return previous
        record = {
            "schema_version": 1,
            "project_id": project,
            "policy_id": policy_id,
            "definition": deepcopy(definition),
            "approved_by": registry.principal.actor_id,
            "approved_at": timestamp(),
        }
        record["policy_digest"] = digest(record)
        registry.store.put_record(project, "delegation_policies", policy_id, record)
        registry.store.put_record(
            project, "delegation_policy_status", policy_id, {"active": True}
        )
        registry._event(
            project,
            policy_id,
            "delegation_policy_approved",
            policy_digest=record["policy_digest"],
        )
        return record


def delegation_policy(registry: RegistryService, project: str, policy_id: str) -> dict:
    registry.check_access(project)
    record = registry.store.get_record(project, "delegation_policies", policy_id)
    status = registry.store.get_record(project, "delegation_policy_status", policy_id)
    if not record or not status:
        raise ValueError("Delegation policy does not exist in this project")
    return {**record, **status}


def revoke_delegation_policy(
    registry: RegistryService,
    project: str,
    policy_id: str,
    rationale: str,
) -> None:
    from .service import text, timestamp

    registry.check_access(project, write=True, human=True)
    rationale = text(rationale, "rationale")
    with registry.store.transaction():
        delegation_policy(registry, project, policy_id)
        registry.store.put_record(
            project,
            "delegation_policy_status",
            policy_id,
            {
                "active": False,
                "revoked_by": registry.principal.actor_id,
                "revoked_at": timestamp(),
                "rationale": rationale,
            },
            replace=True,
        )
        registry._event(
            project, policy_id, "delegation_policy_revoked", rationale=rationale
        )


def decide_delegated_result(
    registry: RegistryService, project: str, decision: dict
) -> dict:
    from .alignment import alignment_context
    from .outcomes import validated_assessment
    from .service import digest, text, timestamp

    registry.check_access(project, write=True)
    if registry.principal.role != "delegate":
        raise PermissionError("Host-configured delegated decision route required")
    if registry.execution_validator is None:
        raise ValueError(
            "Delegated decision requires a trusted execution freshness validator"
        )
    fields = {
        "task_id",
        "policy_id",
        "assessment_id",
        "assessment_digest",
        "alignment_review_id",
        "alignment_review_digest",
        "disposition",
        "rationale",
        "evidence_ids",
    }
    if not isinstance(decision, dict) or set(decision) != fields:
        raise ValueError("Invalid delegated decision contract")
    for key in fields - {"evidence_ids"}:
        text(decision[key], key)
    string_list(decision["evidence_ids"], "evidence_ids", nonempty=True)
    with registry.store.transaction():
        context = alignment_context(registry, project, decision["task_id"])
        registry.execution_validator(context["task"])
        task, result, mandate = (
            context["task"],
            context["execution_result"],
            context["client_mandate"],
        )
        policy = delegation_policy(registry, project, decision["policy_id"])
        definition = mandate["definition"]
        project_brief = context["client_project_brief"]
        if not policy["active"]:
            raise ValueError("Delegation policy is revoked")
        if (
            definition["risk_level"] != "low"
            or definition["importance_level"] != "low"
            or definition["simple"] is not True
            or definition["reversible"] is not True
            or definition["consequence_domains"]
        ):
            raise ValueError("Risk, importance or consequences require human decision")
        issue = context["source_snapshot"]["issue"]
        if issue.get("risk_level") in {"medium", "high", "critical"} or set(
            issue.get("risk_dimensions", [])
        ) & {"safety", "design", "contractual", "regulatory", "commercial"}:
            raise ValueError("Issue risk conflicts with low-risk delegation")
        if (
            task["tool"] not in definition["allowed_tools"]
            or task["tool"] not in policy["definition"]["allowed_tools"]
            or decision["disposition"]
            not in policy["definition"]["allowed_dispositions"]
        ):
            raise ValueError("Task or disposition is outside delegated scope")
        if (
            task["tool"] == "external_solver"
            and task["parameters"]["solver_id"]
            not in policy["definition"]["allowed_solver_ids"]
        ):
            raise ValueError("Solver is outside delegated scope")
        # Proposal readiness cannot delegate contractual acceptance even if misclassified.
        if task["tool"] == "validate_proposal_readiness":
            raise ValueError("Proposal readiness requires human decision")
        assessment = validated_assessment(
            registry,
            project,
            result,
            decision["assessment_id"],
            decision["assessment_digest"],
        )
        review = registry.get_record(project, decision["alignment_review_id"])
        review_attrs = review["attributes"]
        if (
            review["node_type"] != NodeType.ALIGNMENT_REVIEW
            or digest(review) != decision["alignment_review_digest"]
            or review_attrs.get("request_digest") != digest(context)
            or review_attrs.get("review_authority") != "host_configured_ai_reviewer"
        ):
            raise ValueError("Alignment review is stale or belongs to a different task")
        evidence = decision["evidence_ids"]
        allowed_evidence = set(result["attributes"]["evidence_ids"]) | set(
            project_brief["definition"]["evidence_ids"]
        )
        if not set(evidence).issubset(allowed_evidence):
            raise ValueError(
                "Decision evidence must belong to the exact result or bound client project brief"
            )
        registry._evidence(project, evidence)
        all_findings = [
            *task.get("brain_plan", {}).get("analysis", {}).get("findings", []),
            *assessment["attributes"].get("analysis", {}).get("findings", []),
        ]
        if any(
            f["risk_level"] in {"medium", "high", "critical", "unknown"}
            or f["status"] == "requires_human_review"
            or set(f.get("risk_dimensions", []))
            & {"safety", "design", "contractual", "regulatory", "commercial"}
            for f in all_findings
        ):
            raise ValueError(
                "Document AI reasoning risk or importance requires human decision"
            )
        if decision["disposition"] == "accept":
            attrs = assessment["attributes"]
            if (
                not all(c["passed"] for c in attrs["checks"])
                or attrs["recommendation"] != "human_review"
                or attrs["unknowns"]
                or definition["unknowns"]
                or project_brief["unknowns"]
                or review_attrs["status"] != "aligned"
                or review_attrs["unknowns"]
            ):
                raise ValueError(
                    "Failed checks, misalignment or unknowns prevent automatic acceptance"
                )
            findings = all_findings
            if any(
                f["status"] != "confirmed"
                or f["risk_level"] != "low"
                or f.get("uncertainties")
                or f.get("conflicts")
                or f.get("assumptions")
                for f in findings
            ):
                raise ValueError("Unresolved reasoning findings require human review")
        payload = {
            **deepcopy(decision),
            "authority": "ai_delegated",
            "human_verified": False,
            "reviewed_by": registry.principal.actor_id,
            "issue_id": task["issue_id"],
            "result_id": result["node_id"],
            "result_digest": digest(result),
            "task_digest": task["task_digest"],
            "policy_digest": policy["policy_digest"],
            "policy_snapshot": {k: v for k, v in policy.items() if k != "active"},
            "client_project_brief_digest": project_brief["brief_digest"],
            "client_project_brief": project_brief,
            "client_mandate_digest": mandate["mandate_digest"],
            "client_mandate": mandate,
            "reasoning_evidence_status": "ai_rationale_recorded",
        }
        did = "decision:delegated:" + digest(payload)
        previous = registry.store.get_node(project, did)
        if previous:
            return asdict(previous)
        node = GraphNode(
            did,
            project,
            NodeType.DECISION,
            decision["rationale"],
            {**payload, "reviewed_at": timestamp()},
        )
        targets = [result["node_id"], assessment["node_id"], review["node_id"]]
        registry.store.add_subgraph(
            [node],
            [
                GraphEdge(
                    did + ":" + target,
                    project,
                    did,
                    RelationshipType.RELATES_TO,
                    target,
                )
                for target in targets
            ]
            + [
                GraphEdge(
                    did + ":issue",
                    project,
                    task["issue_id"],
                    RelationshipType.DECIDED_BY,
                    did,
                )
            ]
            + [
                GraphEdge(
                    did + ":evidence:" + eid,
                    project,
                    did,
                    RelationshipType.SUPPORTED_BY,
                    eid,
                    [eid],
                )
                for eid in evidence
            ],
        )
        registry._event(
            project,
            task["issue_id"],
            "output_decided_under_delegation",
            decision_id=did,
            result_id=result["node_id"],
            authority="ai_delegated",
            disposition=decision["disposition"],
            policy_digest=policy["policy_digest"],
        )
        return asdict(node)
