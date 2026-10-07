"""Versioned human-approved project intent, distinct from individual task scope."""

from copy import deepcopy
from typing import TYPE_CHECKING

from .delegation import string_list

if TYPE_CHECKING:
    from .service import RegistryService

SECTIONS = {
    "purpose",
    "success_criteria",
    "project_context",
    "stakeholders_interfaces",
    "scope",
    "exclusions",
    "constraints",
    "priorities",
    "approved_decisions",
}
BASES = {"explicit", "inferred", "assumed", "unknown"}


def source_basis(
    registry: "RegistryService", project: str, evidence_ids: list[str]
) -> dict:
    """Resolve only approved brief references, with current control overlays."""
    registry._evidence(project, evidence_ids)
    records = [registry.get_record(project, eid) for eid in sorted(evidence_ids)]
    return {
        "records": records,
        "source_control": registry.source_control(project, sorted(evidence_ids)),
        "evidence_review": registry.evidence_review_context(
            project, sorted(evidence_ids)
        ),
    }


def record_client_project_brief(
    registry: "RegistryService", project: str, brief_id: str, definition: dict
) -> dict:
    from .service import digest, text, timestamp

    registry.check_access(project, write=True, human=True)
    text(brief_id, "brief_id")
    if (
        len(brief_id) > 512
        or not isinstance(definition, dict)
        or set(definition)
        != {
            "title",
            "sections",
            "evidence_ids",
            "unknowns",
        }
    ):
        raise ValueError("Invalid client project brief contract")
    text(definition["title"], "title")
    string_list(definition["evidence_ids"], "evidence_ids", nonempty=True)
    string_list(definition["unknowns"], "unknowns")
    sections = definition["sections"]
    if not isinstance(sections, dict) or set(sections) != SECTIONS:
        raise ValueError("Client project brief requires all project context sections")
    statement_ids, unresolved = set(), list(definition["unknowns"])
    for section, entries in sections.items():
        if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
            raise ValueError(
                "Each brief section needs bounded statements or an explicit unknown"
            )
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "statement_id",
                "text",
                "basis",
                "evidence_ids",
            }:
                raise ValueError("Invalid client brief statement")
            for key in ("statement_id", "text"):
                text(entry[key], key)
                if len(entry[key]) > 4000:
                    raise ValueError("Client brief statement exceeds limit")
            if entry["statement_id"] in statement_ids or entry["basis"] not in BASES:
                raise ValueError("Duplicate statement or invalid intent basis")
            statement_ids.add(entry["statement_id"])
            string_list(
                entry["evidence_ids"],
                "statement evidence",
                nonempty=entry["basis"] == "explicit",
            )
            if not set(entry["evidence_ids"]).issubset(definition["evidence_ids"]):
                raise ValueError(
                    "Statement evidence must be retained in the project brief"
                )
            if entry["basis"] != "explicit":
                unresolved.append(
                    f"{section}/{entry['statement_id']}: {entry['basis']}: {entry['text']}"
                )
    if len(str(definition)) > 100_000:
        raise ValueError("Client project brief exceeds limit")
    with registry.store.transaction():
        sources = source_basis(registry, project, definition["evidence_ids"])
        if len(str(sources)) > 300_000:
            raise ValueError("Client project evidence exceeds bounded context limit")
        for control in sources["source_control"]:
            if control["status"] == "unverified":
                unresolved.append(
                    "Project source authority is unverified: " + control["evidence_id"]
                )
        for evidence in sources["evidence_review"]:
            if evidence["status"] in {
                "unreviewed_text_missing",
                "insufficient",
                "not_reviewable",
            }:
                unresolved.append(
                    "Project evidence requires clarification: "
                    + evidence["evidence_id"]
                )
        previous = registry.store.get_record(project, "client_project_briefs", brief_id)
        if previous:
            if previous["definition"] != definition or previous[
                "source_digest"
            ] != digest(sources):
                raise ValueError("Client project brief is immutable; use a new ID")
            return previous
        record = {
            "schema_version": 1,
            "project_id": project,
            "brief_id": brief_id,
            "definition": deepcopy(definition),
            "source_basis": sources,
            "source_digest": digest(sources),
            "unknowns": sorted(set(unresolved)),
            "approved_by": registry.principal.actor_id,
            "approved_at": timestamp(),
        }
        record["brief_digest"] = digest(record)
        registry.store.put_record(project, "client_project_briefs", brief_id, record)
        registry.store.put_record(
            project,
            "active_client_project_brief",
            "active",
            {"brief_id": brief_id},
            replace=True,
        )
        registry._event(
            project,
            brief_id,
            "client_project_brief_recorded",
            brief_digest=record["brief_digest"],
        )
        return record


def client_project_brief(registry: "RegistryService", project: str) -> dict | None:
    from .service import digest

    registry.check_access(project)
    pointer = registry.store.get_record(
        project, "active_client_project_brief", "active"
    )
    if not pointer:
        return None
    record = registry.store.get_record(
        project, "client_project_briefs", pointer["brief_id"]
    )
    if not record or record["source_digest"] != digest(
        source_basis(registry, project, record["definition"]["evidence_ids"])
    ):
        raise ValueError(
            "Client project brief is stale; refresh changed project evidence"
        )
    return record
