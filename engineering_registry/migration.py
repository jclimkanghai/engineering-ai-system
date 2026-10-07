"""Validated, copy-only migration from a shared Registry database."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .paths import organization_registry_path, project_registry_path

_TABLES = (
    "graph_nodes",
    "graph_edges",
    "graph_edge_provenance",
    "engineering_issues",
    "issue_lifecycle_events",
    "registry_records",
)


def _counts(connection: sqlite3.Connection, scope: str) -> dict[str, int]:
    return {
        table: int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (scope,)
            ).fetchone()[0]
        )
        for table in _TABLES
    }


def _missing_references(
    connection: sqlite3.Connection, scope: str
) -> list[dict[str, str]]:
    checks = (
        (
            "edge_source",
            "SELECT e.edge_id, e.source_node_id FROM graph_edges e LEFT JOIN graph_nodes n ON n.project_id=e.project_id AND n.node_id=e.source_node_id WHERE e.project_id=? AND n.node_id IS NULL",
        ),
        (
            "edge_target",
            "SELECT e.edge_id, e.target_node_id FROM graph_edges e LEFT JOIN graph_nodes n ON n.project_id=e.project_id AND n.node_id=e.target_node_id WHERE e.project_id=? AND n.node_id IS NULL",
        ),
        (
            "edge_provenance_edge",
            "SELECT p.edge_id, p.edge_id FROM graph_edge_provenance p LEFT JOIN graph_edges e ON e.project_id=p.project_id AND e.edge_id=p.edge_id WHERE p.project_id=? AND e.edge_id IS NULL",
        ),
        (
            "edge_provenance_evidence",
            "SELECT p.edge_id, p.evidence_id FROM graph_edge_provenance p LEFT JOIN graph_nodes n ON n.project_id=p.project_id AND n.node_id=p.evidence_id WHERE p.project_id=? AND n.node_id IS NULL",
        ),
        (
            "issue_node",
            "SELECT i.issue_id, i.issue_id FROM engineering_issues i LEFT JOIN graph_nodes n ON n.project_id=i.project_id AND n.node_id=i.issue_id WHERE i.project_id=? AND n.node_id IS NULL",
        ),
        (
            "lifecycle_issue",
            "SELECT e.event_id, e.issue_id FROM issue_lifecycle_events e LEFT JOIN engineering_issues i ON i.project_id=e.project_id AND i.issue_id=e.issue_id WHERE e.project_id=? AND i.issue_id IS NULL",
        ),
    )
    missing = []
    for kind, query in checks:
        missing.extend(
            {"kind": kind, "record_id": str(row[0]), "missing_id": str(row[1])}
            for row in connection.execute(query, (scope,))
        )
    return missing


def validate_migration(
    source: str | Path,
    data_root: str | Path,
    *,
    project_ids: list[str] | None = None,
    organization_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a no-write migration report. All selected graph references are checked."""
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError("Source Registry database does not exist")
    root = Path(data_root).expanduser().resolve()
    with sqlite3.connect(source_path) as connection:
        connection.row_factory = sqlite3.Row
        available = {
            str(row[0])
            for row in connection.execute(
                "SELECT project_id FROM graph_nodes UNION SELECT project_id FROM registry_records"
            )
        }
        projects = sorted(
            project_ids
            if project_ids is not None
            else (s for s in available if not s.startswith("ORG:"))
        )
        organizations = sorted(
            organization_ids
            if organization_ids is not None
            else (s.removeprefix("ORG:") for s in available if s.startswith("ORG:"))
        )
        scopes = [("project", value, value) for value in projects] + [
            ("organization", value, "ORG:" + value) for value in organizations
        ]
        seen: set[str] = set()
        entries = []
        for kind, identifier, scope in scopes:
            if scope in seen:
                raise ValueError(f"Duplicate selected Registry scope: {scope}")
            seen.add(scope)
            target = (
                project_registry_path(root, identifier)
                if kind == "project"
                else organization_registry_path(root, identifier)
            )
            counts = _counts(connection, scope)
            missing = _missing_references(connection, scope)
            entries.append(
                {
                    "kind": kind,
                    "identifier": identifier,
                    "source_scope": scope,
                    "target": str(target),
                    "counts": counts,
                    "missing_references": missing,
                    "collision": target.exists(),
                    "source_scope_exists": scope in available,
                }
            )
    return {
        "source": str(source_path),
        "data_root": str(root),
        "entries": entries,
        "valid": all(
            e["source_scope_exists"]
            and not e["collision"]
            and not e["missing_references"]
            for e in entries
        ),
    }


def _stage_scope(
    source: Path, target: Path, scope: str, expected: dict[str, int]
) -> tuple[Path, dict[str, int]]:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".registry-migration-", suffix=".sqlite3", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(temporary) as copied:
            source_db.backup(copied)
            copied.execute("PRAGMA foreign_keys=ON")
            copied.execute("BEGIN IMMEDIATE")
            for table in (
                "issue_lifecycle_events",
                "engineering_issues",
                "graph_edge_provenance",
                "graph_edges",
                "graph_nodes",
                "registry_records",
            ):
                copied.execute(f"DELETE FROM {table} WHERE project_id<>?", (scope,))
            copied.execute(
                """
                CREATE TABLE IF NOT EXISTS registry_metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            bound = copied.execute(
                "SELECT value FROM registry_metadata WHERE key='scope_id'"
            ).fetchone()
            if bound and str(bound[0]) != scope:
                raise ValueError(f"Source database is already bound to {bound[0]}")
            copied.execute(
                "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES ('scope_id',?)",
                (scope,),
            )
            copied.commit()
            actual = _counts(copied, scope)
            if actual != expected:
                raise ValueError(f"Copied record count mismatch for {scope}")
            if _missing_references(copied, scope):
                raise ValueError(f"Copied graph references are invalid for {scope}")
            foreign_key_errors = copied.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise ValueError(
                    f"Copied database has foreign-key failures for {scope}"
                )
        if target.exists():
            raise FileExistsError(f"Migration target already exists: {target}")
        return temporary, actual
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def apply_migration(report: dict[str, Any]) -> dict[str, Any]:
    """Copy validated scopes into canonical files; never changes the source."""
    if not report.get("valid"):
        raise ValueError(
            "Migration validation failed; inspect collisions and missing references"
        )
    source = Path(report["source"])
    staged: list[tuple[dict[str, Any], Path, dict[str, int]]] = []
    committed: list[Path] = []
    try:
        for entry in report["entries"]:
            temporary, counts = _stage_scope(
                source, Path(entry["target"]), entry["source_scope"], entry["counts"]
            )
            staged.append((entry, temporary, counts))
        for entry, temporary, _counts_result in staged:
            target = Path(entry["target"])
            # Hard-link creation is atomic and refuses to replace a concurrent target.
            os.link(temporary, target)
            committed.append(target)
            temporary.unlink()
    except Exception:
        for target in committed:
            target.unlink(missing_ok=True)
        for _entry, temporary, _counts_result in staged:
            temporary.unlink(missing_ok=True)
        raise
    completed = [
        {**entry, "copied_counts": counts} for entry, _temporary, counts in staged
    ]
    return {"source_unchanged": True, "entries": completed}
