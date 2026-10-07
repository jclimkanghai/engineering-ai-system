"""Non-destructive, transactional import of versioned Document AI source packages."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .adapters import finding_to_issue
from .models import GraphEdge, GraphNode, NodeType, RelationshipType
from .service import RegistryService, digest, finding_signature, text
from .store import GraphIntegrityError


def canonical_id(kind: str, legacy_id: str) -> str:
    return kind + ":" + quote(text(legacy_id, "legacy_id"), safe="")


class _DryRun(Exception):
    def __init__(self, report: dict) -> None:
        self.report = report


def import_bundle(
    service: RegistryService, bundle: dict[str, Any], *, dry_run: bool = True
) -> dict:
    data = copy.deepcopy(bundle)
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported source package schema_version")
    project = text(data.get("project_id"), "project_id")
    service.check_access(project, write=True)
    for collection in ("documents", "evidence", "requirements", "findings"):
        records = data.get(collection, [])
        if not isinstance(records, list):
            raise ValueError(collection + " must be a list")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Source records must be objects")
            if record.get("project_id") not in (None, project):
                raise GraphIntegrityError(
                    "Source record project does not match package project"
                )
    import_id = digest(data)
    previous = service.store.get_record(project, "imports", import_id)
    if previous:
        return {**previous, "status": "already_imported"}
    counts: dict[str, int] = {}
    aliases: dict[tuple[str, str], str] = {}
    records_requiring_review: list[str] = []

    def register(kind: str, legacy: str, title: str, attributes: dict) -> str:
        cid = canonical_id(kind, legacy)
        service.register(
            GraphNode(
                cid, project, NodeType(kind), title, {**attributes, "legacy_id": legacy}
            )
        )
        aliases[(kind, legacy)] = cid
        service.store.put_record(
            project,
            "aliases",
            kind + ":" + legacy,
            {"kind": kind, "legacy_id": legacy, "canonical_id": cid},
        )
        counts[kind] = counts.get(kind, 0) + 1
        return cid

    try:
        with service.store.transaction():
            existing_project = service.store.get_node(
                project, canonical_id("project", project)
            )
            project_title = data.get("project_title") or (
                existing_project.title if existing_project else project
            )
            register("project", project, project_title, {})
            documents = data.get("documents", [])
            for doc in documents:
                legacy = text(doc.get("document_id"), "document_id")
                existing_document = service.store.get_node(
                    project, canonical_id("document", legacy)
                )
                if existing_document and doc.get("revision"):
                    if (
                        existing_document.node_type != NodeType.DOCUMENT
                        or existing_document.attributes.get("legacy_id") != legacy
                    ):
                        raise GraphIntegrityError(
                            "Document identity conflicts with existing source"
                        )
                    for field in ("document_number", "logical_document_id"):
                        old_value = existing_document.attributes.get(field)
                        if old_value and doc.get(field) and old_value != doc[field]:
                            raise GraphIntegrityError(
                                "Document identity changed across revisions"
                            )
                    # Keep the first observation immutable. Every revision node carries
                    # its complete version-specific source metadata and title.
                    register(
                        "document",
                        legacy,
                        existing_document.title,
                        existing_document.attributes,
                    )
                else:
                    register("document", legacy, doc.get("title") or legacy, doc)
                if doc.get("revision"):
                    rid = legacy + ":" + str(doc["revision"])
                    revision_id = register("document_revision", rid, rid, doc)
                    service.store.add_edge(
                        GraphEdge(
                            "revision:" + digest([legacy, rid]),
                            project,
                            aliases[("document", legacy)],
                            RelationshipType.HAS_REVISION,
                            revision_id,
                        )
                    )
            for doc in documents:
                legacy = doc["document_id"]
                for old in doc.get("supersedes", []) or []:
                    target = aliases.get(("document", old)) or canonical_id(
                        "document", old
                    )
                    service.store.add_edge(
                        GraphEdge(
                            "supersedes:" + digest([legacy, old]),
                            project,
                            aliases[("document", legacy)],
                            RelationshipType.SUPERSEDES,
                            target,
                        )
                    )
                for old_revision in doc.get("supersedes_revisions", []) or []:
                    if not doc.get("revision") or not isinstance(old_revision, dict):
                        raise ValueError(
                            "Revision supersession requires explicit revision objects"
                        )
                    old_legacy = (
                        text(old_revision.get("document_id"), "superseded document_id")
                        + ":"
                        + text(old_revision.get("revision"), "superseded revision")
                    )
                    revision_source = canonical_id(
                        "document_revision", legacy + ":" + str(doc["revision"])
                    )
                    target = canonical_id("document_revision", old_legacy)
                    previous_revision = service.store.get_node(project, target)
                    if (
                        previous_revision is None
                        or previous_revision.node_type != NodeType.DOCUMENT_REVISION
                    ):
                        raise GraphIntegrityError(
                            "Superseded revision must exist in the same project"
                        )
                    service.store.add_edge(
                        GraphEdge(
                            "supersedes-revision:" + digest([revision_source, target]),
                            project,
                            revision_source,
                            RelationshipType.SUPERSEDES,
                            target,
                        )
                    )
            evidence_map: dict[str, dict] = {}
            for item in data.get("evidence", []):
                legacy = text(item.get("evidence_id"), "evidence_id")
                source = dict(item)
                source["text"] = (
                    source.get("text")
                    or source.get("source_text")
                    or source.get("excerpt")
                )
                eid = register(
                    "evidence", legacy, source.get("title") or legacy, source
                )
                evidence_map[legacy] = source
                if source.get("document_id"):
                    did = source["document_id"]
                    target = canonical_id("document", did)
                    if source.get("revision"):
                        candidate = canonical_id(
                            "document_revision", did + ":" + str(source["revision"])
                        )
                        if service.store.get_node(project, candidate):
                            target = candidate
                    service.store.add_edge(
                        GraphEdge(
                            "source:" + digest([eid, target]),
                            project,
                            eid,
                            RelationshipType.DERIVED_FROM,
                            target,
                            [eid],
                        )
                    )
            for req in data.get("requirements", []):
                identity = req.get("identity", {})
                legacy = text(
                    req.get("requirement_id") or identity.get("requirement_id"),
                    "requirement_id",
                )
                wording = req.get("source_text") or identity.get("source_text")
                if not isinstance(wording, str) or not wording.strip():
                    raise ValueError("Full requirement wording is required")
                rid = register(
                    "requirement",
                    legacy,
                    req.get("title") or legacy,
                    {**req, "source_text": wording},
                )
                for eid in req.get("source_evidence_ids", []):
                    target = canonical_id("evidence", eid)
                    service.store.add_edge(
                        GraphEdge(
                            "req-source:" + digest([rid, eid]),
                            project,
                            rid,
                            RelationshipType.SUPPORTED_BY,
                            target,
                            [target],
                        )
                    )
            for finding in data.get("findings", []):
                legacy = text(finding.get("finding_id"), "finding_id")
                occurrence = (
                    legacy + "@" + data["run_id"] if data.get("run_id") else legacy
                )
                if not finding.get("finding"):
                    raise ValueError("Finding content is required")
                signature = finding_signature(finding)
                fnode = register(
                    "finding",
                    occurrence,
                    finding.get("title") or legacy,
                    {**finding, "finding_signature": signature},
                )
                errors = finding.get("validation_errors", [])
                issue = finding_to_issue(
                    finding,
                    project,
                    evidence_by_id=evidence_map,
                    validation_errors=errors,
                )
                issue = replace(
                    issue,
                    issue_id=canonical_id("issue", occurrence),
                    evidence_ids=[
                        canonical_id("evidence", eid) for eid in issue.evidence_ids
                    ],
                    requirement_ids=[
                        canonical_id("requirement", rid)
                        for rid in issue.requirement_ids
                    ],
                    related_entity_ids=[fnode],
                    attributes={
                        **issue.attributes,
                        "legacy_evidence_ids": finding.get("source_evidence_ids", []),
                        "legacy_requirement_ids": finding.get("requirement_ids", []),
                    },
                )
                matched_issue_ids = set()
                for existing_finding in service.store.list_nodes(
                    project, NodeType.FINDING
                ):
                    existing_attributes = existing_finding.attributes
                    existing_signature = existing_attributes.get(
                        "finding_signature"
                    ) or finding_signature(existing_attributes)
                    if existing_signature != signature:
                        continue
                    for existing_issue in service.store.list_nodes(
                        project, NodeType.ENGINEERING_ISSUE
                    ):
                        if any(
                            edge.relationship == RelationshipType.RELATES_TO
                            and edge.target_node_id == existing_finding.node_id
                            for edge in service.store.get_edges(
                                project, existing_issue.node_id
                            )
                        ):
                            matched_issue_ids.add(existing_issue.node_id)
                if matched_issue_ids:
                    target_issue_ids = sorted(matched_issue_ids)
                    for target_issue_id in target_issue_ids:
                        service.store.add_edge(
                            GraphEdge(
                                "finding-observation:"
                                + digest([target_issue_id, fnode, import_id]),
                                project,
                                target_issue_id,
                                RelationshipType.RELATES_TO,
                                fnode,
                                issue.evidence_ids,
                            )
                        )
                        service._event(
                            project,
                            target_issue_id,
                            "finding_observed",
                            finding_id=fnode,
                            finding_signature=signature,
                            import_id=import_id,
                        )
                else:
                    target_issue_ids = [issue.issue_id]
                    service.store.save_issue(issue)
                    service._event(
                        project, issue.issue_id, "finding_imported", import_id=import_id
                    )
                    counts["issue"] = counts.get("issue", 0) + 1
                aliases[("issue", occurrence)] = target_issue_ids[0]
                records_requiring_review.extend(target_issue_ids)
                service.store.put_record(
                    project,
                    "aliases",
                    "issue:" + occurrence,
                    {
                        "kind": "issue",
                        "legacy_id": occurrence,
                        "canonical_id": target_issue_ids[0],
                    },
                )
            report = {
                "status": "imported",
                "project_id": project,
                "import_id": import_id,
                "counts": counts,
                "live_cutover": False,
                "aliases": [
                    {"kind": kind, "legacy_id": legacy, "canonical_id": cid}
                    for (kind, legacy), cid in sorted(aliases.items())
                ],
                "records_requiring_review": sorted(set(records_requiring_review)),
                "legacy_review_history": data.get("review_history", []),
                "warnings": [
                    "Imported findings remain proposed; legacy approval does not authorise closure."
                ],
            }
            service.store.put_record(project, "source_packages", import_id, data)
            service.store.put_record(project, "imports", import_id, report)
            if dry_run:
                raise _DryRun({**report, "status": "dry_run"})
        return report
    except _DryRun as result:
        return result.report


def import_legacy_finding_registry(
    service: RegistryService,
    source: str | Path,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and copy a legacy FindingRegistry JSON snapshot into Registry.

    Every cited evidence, requirement, document and revision must already exist
    in the destination project. The input file is opened read-only and retained.
    Imported issue lifecycle remains proposed regardless of legacy flags.
    """
    source_path = Path(source).expanduser().resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    project = text(payload.get("project_id"), "project_id")
    service.check_access(project, write=True)
    entries = payload.get("entries")
    if payload.get("registry_version") != 1 or not isinstance(entries, list):
        raise ValueError("Unsupported legacy finding registry format")

    findings: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    requirement_ids: set[str] = set()
    document_ids: set[str] = set()
    revisions: set[tuple[str, str]] = set()
    missing: set[tuple[str, str]] = set()
    seen_findings: set[str] = set()

    def resolve(kind: str, legacy_id: str):
        alias = service.store.get_record(project, "aliases", kind + ":" + legacy_id)
        node_id = alias["canonical_id"] if alias else canonical_id(kind, legacy_id)
        return service.store.get_node(project, node_id)

    for entry in entries:
        finding_id = text(entry.get("finding_id"), "legacy finding_id")
        if finding_id in seen_findings:
            raise ValueError("Legacy finding IDs must be unique within a snapshot")
        seen_findings.add(finding_id)
        observations = entry.get("observations")
        if not isinstance(observations, list) or not observations:
            raise ValueError(
                "Every legacy finding must retain at least one observation"
            )
        latest = observations[-1]
        finding = latest.get("finding")
        if not isinstance(finding, dict):
            raise ValueError("Legacy finding observation has no finding payload")
        finding = copy.deepcopy(finding)
        finding["finding_id"] = finding_id
        finding["legacy_registry_entry"] = copy.deepcopy(entry)
        finding["legacy_lifecycle_status"] = entry.get("lifecycle_status", "UNKNOWN")
        finding["legacy_closure_status"] = entry.get("closure_status", "UNKNOWN")
        finding["legacy_human_verified"] = bool(entry.get("human_verified", False))
        findings.append(finding)
        evidence_ids.update(finding.get("source_evidence_ids") or [])
        requirement_ids.update(finding.get("requirement_ids") or [])
        document_ids.update(finding.get("source_document_ids") or [])
        for observation in observations:
            document_ids.update(observation.get("document_ids") or [])
            evidence_ids.update(observation.get("source_evidence_ids") or [])
            for location in observation.get("evidence_locations") or []:
                if location.get("document_id"):
                    document_ids.add(location["document_id"])
                    if location.get("revision"):
                        revisions.add(
                            (location["document_id"], str(location["revision"]))
                        )

    requirement_nodes = {}
    for legacy_id in sorted(requirement_ids):
        node = resolve("requirement", legacy_id)
        if node is None or node.node_type != NodeType.REQUIREMENT:
            missing.add(("requirement", legacy_id))
            continue
        source_text = node.attributes.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            missing.add(("requirement_text", legacy_id))
            continue
        requirement_nodes[legacy_id] = node
        evidence_ids.update(node.attributes.get("source_evidence_ids") or [])

    evidence: list[dict[str, Any]] = []
    evidence_nodes = {}
    for legacy_id in sorted(evidence_ids):
        node = resolve("evidence", legacy_id)
        if node is None or node.node_type != NodeType.EVIDENCE:
            missing.add(("evidence", legacy_id))
            continue
        evidence_nodes[legacy_id] = node
        evidence.append(
            {
                "evidence_id": legacy_id,
                **{
                    key: value
                    for key, value in node.attributes.items()
                    if key != "legacy_id"
                },
            }
        )
        if node.attributes.get("document_id"):
            document_ids.add(node.attributes["document_id"])
    requirements = [
        {
            "requirement_id": legacy_id,
            **{
                key: value
                for key, value in node.attributes.items()
                if key != "legacy_id"
            },
            "source_text": node.attributes["source_text"],
        }
        for legacy_id, node in sorted(requirement_nodes.items())
    ]
    documents: list[dict[str, Any]] = []
    for node in evidence_nodes.values():
        if node.attributes.get("document_id") and node.attributes.get("revision"):
            revisions.add(
                (node.attributes["document_id"], str(node.attributes["revision"]))
            )
    for legacy_id in sorted(document_ids):
        node = resolve("document", legacy_id)
        if node is None or node.node_type != NodeType.DOCUMENT:
            missing.add(("document", legacy_id))
            continue
        documents.append(
            {
                "document_id": legacy_id,
                "title": node.title,
                **{
                    key: value
                    for key, value in node.attributes.items()
                    if key != "legacy_id"
                },
            }
        )
    for document_id, revision in sorted(revisions):
        legacy_revision_id = document_id + ":" + revision
        node = resolve("document_revision", legacy_revision_id)
        if node is None or node.node_type != NodeType.DOCUMENT_REVISION:
            missing.add(("document_revision", legacy_revision_id))
            continue
        documents.append(
            {
                "document_id": document_id,
                "title": node.title,
                "revision": revision,
                **{
                    key: value
                    for key, value in node.attributes.items()
                    if key != "legacy_id"
                },
            }
        )

    missing_report = [
        {"kind": kind, "legacy_id": legacy_id} for kind, legacy_id in sorted(missing)
    ]
    if missing_report:
        return {
            "status": "blocked",
            "project_id": project,
            "source_digest": digest(payload),
            "missing_references": missing_report,
            "source_modified": False,
        }

    source_package = {
        "schema_version": 1,
        "project_id": project,
        "run_id": "legacy-finding-registry",
        "documents": documents,
        "evidence": evidence,
        "requirements": requirements,
        "findings": findings,
        "review_history": payload.get("applied_review_digests", []),
        "legacy_registry_source_digest": digest(payload),
    }
    report = import_bundle(service, source_package, dry_run=dry_run)
    return {**report, "source_digest": digest(payload), "source_modified": False}


def import_legacy_document_registry(
    service: RegistryService,
    source: str | Path,
    *,
    project_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Copy a legacy document registry into the project graph without editing it."""
    source_path = Path(source).expanduser().resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), dict):
        raise ValueError("Legacy document registry must contain a documents object")
    service.check_access(project_id, write=True)
    records = []
    ids = set(payload["documents"])
    missing = set()
    supersedes_by_id = {document_id: set() for document_id in ids}
    for document_id, record in payload["documents"].items():
        if not isinstance(record, dict) or record.get("document_id") != document_id:
            raise ValueError("Legacy document IDs must match their records")
        if record.get("project_id") not in (None, project_id):
            raise GraphIntegrityError("Legacy document belongs to another project")
        for superseded in record.get("supersedes") or []:
            if superseded not in ids:
                missing.add(("document", superseded))
            else:
                supersedes_by_id[document_id].add(superseded)
        for superseder in record.get("superseded_by") or []:
            if superseder not in ids:
                missing.add(("document", superseder))
            else:
                supersedes_by_id[superseder].add(document_id)
    for document_id, record in payload["documents"].items():
        records.append(
            {
                **record,
                "project_id": project_id,
                "supersedes": sorted(supersedes_by_id[document_id]),
            }
        )
    if missing:
        return {
            "status": "blocked",
            "project_id": project_id,
            "missing_references": [
                {"kind": kind, "legacy_id": legacy_id}
                for kind, legacy_id in sorted(missing)
            ],
            "source_unchanged": True,
        }
    report = import_bundle(
        service,
        {
            "schema_version": 1,
            "project_id": project_id,
            "project_title": payload.get("project_title"),
            "documents": records,
        },
        dry_run=dry_run,
    )
    return {
        **report,
        "source_unchanged": True,
        "migration_source": str(source_path),
        "warnings": list(report.get("warnings", []))
        + [
            "The JSON file is retained; Engineering Registry is authoritative for project document control."
        ],
    }
