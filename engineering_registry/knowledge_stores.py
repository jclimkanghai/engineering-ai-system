"""Explicit promotion and import across physically separate Registry stores."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

from .models import GraphNode, NodeType
from .service import RegistryService, digest, text, timestamp


class OrganizationalKnowledgeService:
    """Move only human-validated knowledge between granted physical stores."""

    def __init__(
        self, project_registry: RegistryService, organization_registry: RegistryService
    ):
        if (
            project_registry.principal.actor_id
            != organization_registry.principal.actor_id
        ):
            raise PermissionError(
                "Cross-store operation requires one authenticated human identity"
            )
        if (
            project_registry.principal.role != "reviewer"
            or organization_registry.principal.role != "reviewer"
        ):
            raise PermissionError(
                "Cross-store knowledge operations require reviewer principals"
            )
        self.projects = project_registry
        self.organizations = organization_registry

    def promote(
        self,
        organization_id: str,
        lesson_id: str,
        title: str,
        source_lessons: list[tuple[str, str]],
        *,
        observation: str,
        result: str,
        interpretation: str,
        validated_statement: str,
        applicability: str,
        limitations: str,
        relevant_standards: list[str],
        review_due: str,
        rationale: str,
    ) -> dict[str, Any]:
        scope = "ORG:" + text(organization_id, "organization_id")
        self.organizations.check_access(scope, write=True, human=True)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", organization_id):
            raise ValueError("Invalid organization ID")
        if not 1 <= len(source_lessons) <= 20:
            raise ValueError("Organizational knowledge requires 1 to 20 source lessons")
        due = date.fromisoformat(text(review_due, "review_due"))
        if due <= datetime.now(UTC).date():
            raise ValueError(
                "Organizational knowledge review_due must be in the future"
            )
        if len(relevant_standards) != len(set(relevant_standards)):
            raise ValueError("relevant_standards contains duplicates")
        snapshots = []
        seen = set()
        for project_id, source_id in source_lessons:
            self.projects.check_access(project_id, human=True)
            key = (project_id, source_id)
            if key in seen:
                raise ValueError("source_lessons contains duplicates")
            seen.add(key)
            source = self.projects.get_record(project_id, source_id)
            if (
                source["node_type"] != NodeType.LESSON
                or source["attributes"].get("scope") != "project"
                or not source["attributes"].get("approved_by")
            ):
                raise ValueError(
                    "Organizational knowledge must derive from approved project lessons"
                )
            snapshots.append(
                {
                    "project_id": project_id,
                    "lesson_id": source_id,
                    "lesson_digest": digest(source),
                    "snapshot": source,
                }
            )
        if len(json.dumps(snapshots, ensure_ascii=False)) > 2_000_000:
            raise ValueError("Organizational source snapshots exceed the size limit")
        actor = self.organizations.principal.actor_id
        attrs = {
            "scope": "organization",
            "organization_id": organization_id,
            "status": "active",
            "observation": text(observation, "observation"),
            "result": text(result, "result"),
            "interpretation": text(interpretation, "interpretation"),
            "validated_statement": text(validated_statement, "validated_statement"),
            "applicability": text(applicability, "applicability"),
            "limitations": text(limitations, "limitations"),
            "relevant_standards": [
                text(item, "relevant_standard") for item in relevant_standards
            ],
            "source_lessons": snapshots,
            "source_lesson_refs": [
                {
                    "project_id": item["project_id"],
                    "lesson_id": item["lesson_id"],
                    "digest": item["lesson_digest"],
                }
                for item in snapshots
            ],
            "validated_by": actor,
            "validated_at": timestamp(),
            "review_due": due.isoformat(),
            "rationale": text(rationale, "rationale"),
        }
        node = GraphNode(
            text(lesson_id, "lesson_id"),
            scope,
            NodeType.LESSON,
            text(title, "title"),
            attrs,
        )
        self.organizations.store.add_subgraph([node], [])
        self.organizations.store.put_record(
            scope,
            "organizational_lesson_status",
            lesson_id,
            {
                "status": "active",
                "changed_by": actor,
                "changed_at": timestamp(),
                "rationale": attrs["rationale"],
            },
        )
        self.organizations._event(scope, lesson_id, "organizational_lesson_validated")
        return {
            "node": node.__dict__,
            "status": self.organizations.store.get_record(
                scope, "organizational_lesson_status", lesson_id
            ),
        }

    def import_lesson(
        self,
        project_id: str,
        organization_id: str,
        lesson_id: str,
        import_id: str,
        rationale: str,
        expected_source_digest: str,
    ) -> dict[str, Any]:
        self.projects.check_access(project_id, write=True, human=True)
        scope = "ORG:" + text(organization_id, "organization_id")
        self.organizations.check_access(scope)
        source = self.organizations.store.get_node(scope, text(lesson_id, "lesson_id"))
        status = self.organizations.store.get_record(
            scope, "organizational_lesson_status", lesson_id
        )
        if (
            source is None
            or source.node_type != NodeType.LESSON
            or source.attributes.get("scope") != "organization"
            or not status
            or status.get("status") != "active"
        ):
            raise ValueError("Only active organisational knowledge can be imported")
        if (
            date.fromisoformat(source.attributes["review_due"])
            <= datetime.now(UTC).date()
        ):
            raise ValueError(
                "Organisational knowledge is due for review and cannot be imported"
            )
        source_record = source.__dict__
        if (
            digest({"lesson": source_record, "status": status})
            != expected_source_digest
        ):
            raise ValueError("Organisational lesson changed; refresh before importing")
        existing = self.projects.store.get_node(project_id, import_id)
        if existing:
            if (
                existing.attributes.get("source_organizational_lesson_digest")
                == digest(source_record)
                and existing.attributes.get("import_rationale") == rationale
            ):
                return existing.__dict__
            raise ValueError(
                "Import ID already identifies different organisational knowledge"
            )
        actor = self.projects.principal.actor_id
        imported = GraphNode(
            text(import_id, "import_id"),
            project_id,
            NodeType.LESSON,
            source.title,
            {
                **source.attributes,
                "scope": "imported_organizational",
                "status": "active",
                "source_organization_id": organization_id,
                "source_organizational_lesson_id": lesson_id,
                "source_organizational_lesson_digest": digest(source_record),
                "source_organizational_lesson": source_record,
                "imported_by": actor,
                "imported_at": timestamp(),
                "import_rationale": text(rationale, "rationale"),
                "approved_by": actor,
            },
        )
        self.projects.store.add_subgraph([imported], [])
        self.projects._event(
            project_id,
            import_id,
            "organizational_lesson_imported",
            organization_id=organization_id,
            source_lesson_id=lesson_id,
        )
        return imported.__dict__
