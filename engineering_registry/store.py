from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from .models import (
    EngineeringIssue,
    GraphEdge,
    GraphNode,
    IssueStatus,
    NodeType,
    RelationshipType,
)


class GraphIntegrityError(ValueError):
    """A graph write references invalid or cross-project data."""


class GraphStoreError(RuntimeError):
    """A graph store could not read or write its persistent data."""


class EngineeringGraphStore(Protocol):
    """Storage seam consumed by V2 services; SQLite is the initial backend."""

    def add_node(self, node: GraphNode) -> None: ...

    def add_subgraph(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None: ...

    def get_node(self, project_id: str, node_id: str) -> GraphNode | None: ...

    def get_edges(self, project_id: str, source_node_id: str) -> list[GraphEdge]: ...

    def list_decisions_for_records(
        self, project_id: str, record_ids: list[str], *, limit: int = 100
    ) -> tuple[list[GraphNode], int]: ...

    def list_decisions_for_issue(
        self, project_id: str, issue_id: str, *, limit: int = 100
    ) -> tuple[list[GraphNode], int]: ...

    def save_issue(
        self,
        issue: EngineeringIssue,
        *,
        supporting_nodes: list[GraphNode] | None = None,
        supporting_edges: list[GraphEdge] | None = None,
    ) -> None: ...

    def get_issue(self, project_id: str, issue_id: str) -> EngineeringIssue | None: ...

    def transition_issue(
        self,
        project_id: str,
        issue_id: str,
        to_status: IssueStatus,
        *,
        actor_id: str,
        rationale: str,
        evidence_ids: list[str] | None = None,
        human_approved: bool = False,
    ) -> EngineeringIssue: ...

    def list_lifecycle_events(
        self, project_id: str, issue_id: str
    ) -> list[dict[str, object]]: ...


def _json_default(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"Value of type {type(value).__name__} is not JSON serializable")


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


class SQLiteGraphStore:
    """Transactional, project-scoped graph backed by stdlib SQLite."""

    CURRENT_SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = (
            str(Path(path).expanduser()) if str(path) != ":memory:" else ":memory:"
        )
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._transaction_depth = 0
        self._initialize()
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS registry_records(
                project_id TEXT NOT NULL, namespace TEXT NOT NULL,
                record_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(project_id,namespace,record_id));
            CREATE TABLE IF NOT EXISTS registry_metadata(
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    @property
    def bound_scope(self) -> str | None:
        row = self._db.execute(
            "SELECT value FROM registry_metadata WHERE key='scope_id'"
        ).fetchone()
        return str(row[0]) if row else None

    def bind_scope(self, scope_id: str) -> None:
        """Permanently bind a newly isolated database to one project/org scope."""
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError("Registry scope ID is required")
        existing = self.bound_scope
        if existing is not None and existing != scope_id:
            raise GraphStoreError("Registry database is bound to a different scope")
        scopes = self.project_ids()
        if scopes - {scope_id}:
            raise GraphStoreError(
                "Registry database contains records from another scope"
            )
        with self.transaction():
            self._db.execute(
                "INSERT OR IGNORE INTO registry_metadata(key,value) VALUES ('scope_id',?)",
                (scope_id,),
            )

    def _assert_bound_scope(self, scope_id: str) -> None:
        bound = self.bound_scope
        if bound is not None and bound != scope_id:
            raise GraphStoreError(
                f"Registry database is bound to {bound}; cannot write {scope_id}"
            )

    @property
    def schema_version(self) -> int:
        return int(
            self._db.execute(
                "SELECT version FROM graph_schema WHERE singleton=1"
            ).fetchone()[0]
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> SQLiteGraphStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS graph_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)"
        )
        row = self._db.execute(
            "SELECT version FROM graph_schema WHERE singleton=1"
        ).fetchone()
        if row and int(row[0]) > self.CURRENT_SCHEMA_VERSION:
            raise GraphStoreError(
                "Graph database schema is newer than this application"
            )
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS graph_nodes(
                    project_id TEXT NOT NULL, node_id TEXT NOT NULL, node_type TEXT NOT NULL,
                    title TEXT NOT NULL, attributes_json TEXT NOT NULL,
                    PRIMARY KEY(project_id,node_id));
                CREATE TABLE IF NOT EXISTS graph_edges(
                    project_id TEXT NOT NULL, edge_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
                    relationship TEXT NOT NULL, target_node_id TEXT NOT NULL,
                    PRIMARY KEY(project_id,edge_id),
                    FOREIGN KEY(project_id,source_node_id) REFERENCES graph_nodes(project_id,node_id),
                    FOREIGN KEY(project_id,target_node_id) REFERENCES graph_nodes(project_id,node_id));
                CREATE TABLE IF NOT EXISTS graph_edge_provenance(
                    project_id TEXT NOT NULL, edge_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                    PRIMARY KEY(project_id,edge_id,evidence_id),
                    FOREIGN KEY(project_id,edge_id) REFERENCES graph_edges(project_id,edge_id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id,evidence_id) REFERENCES graph_nodes(project_id,node_id));
                CREATE TABLE IF NOT EXISTS engineering_issues(
                    project_id TEXT NOT NULL, issue_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(project_id,issue_id),
                    FOREIGN KEY(project_id,issue_id) REFERENCES graph_nodes(project_id,node_id));
                CREATE TABLE IF NOT EXISTS issue_lifecycle_events(
                    project_id TEXT NOT NULL, issue_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(project_id,issue_id,event_id),
                    FOREIGN KEY(project_id,issue_id) REFERENCES engineering_issues(project_id,issue_id));
            """)
            self._db.execute(
                "INSERT OR IGNORE INTO graph_schema VALUES (1, ?)",
                (self.CURRENT_SCHEMA_VERSION,),
            )
            self._db.execute(
                "UPDATE graph_schema SET version=? WHERE singleton=1 AND version<?",
                (self.CURRENT_SCHEMA_VERSION, self.CURRENT_SCHEMA_VERSION),
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self.transaction():
            yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize writes and compose nested operations without partial commits."""
        depth = self._transaction_depth
        savepoint = f"registry_{depth}"
        try:
            self._db.execute(
                "BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}"
            )
            self._transaction_depth += 1
            yield
            self._db.execute(
                "COMMIT" if depth == 0 else f"RELEASE SAVEPOINT {savepoint}"
            )
        except Exception as exc:
            if self._db.in_transaction:
                if depth == 0:
                    self._db.rollback()
                else:
                    self._db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._db.execute(f"RELEASE SAVEPOINT {savepoint}")
            if isinstance(exc, sqlite3.IntegrityError):
                raise GraphIntegrityError(
                    "Graph write violates project or relationship integrity"
                ) from exc
            if isinstance(exc, (sqlite3.Error, TypeError)):
                raise GraphStoreError(
                    "Graph write failed; no partial changes were retained"
                ) from exc
            raise
        finally:
            self._transaction_depth = depth

    def list_nodes(
        self, project_id: str, node_type: str | None = None
    ) -> list[GraphNode]:
        query = "SELECT node_id FROM graph_nodes WHERE project_id=?"
        args = [project_id]
        if node_type:
            query += " AND node_type=?"
            args.append(NodeType(node_type).value)
        rows = self._db.execute(query + " ORDER BY node_id", args).fetchall()
        nodes = []
        for row in rows:
            node = self.get_node(project_id, row[0])
            if node is None:
                raise GraphIntegrityError("Listed record disappeared during read")
            nodes.append(node)
        return nodes

    def list_nodes_page(
        self,
        project_id: str,
        node_type: str,
        *,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[GraphNode], int]:
        """Return a bounded page of one record type for overview readers."""
        kind = NodeType(node_type).value
        total = int(
            self._db.execute(
                "SELECT COUNT(*) FROM graph_nodes WHERE project_id=? AND node_type=?",
                (project_id, kind),
            ).fetchone()[0]
        )
        rows = self._db.execute(
            "SELECT * FROM graph_nodes WHERE project_id=? AND node_type=? "
            "ORDER BY node_id LIMIT ? OFFSET ?",
            (project_id, kind, limit, offset),
        ).fetchall()
        return (
            [
                GraphNode(
                    row["node_id"],
                    row["project_id"],
                    NodeType(row["node_type"]),
                    row["title"],
                    json.loads(row["attributes_json"]),
                )
                for row in rows
            ],
            total,
        )

    def list_requirements_for_evidence(
        self, project_id: str, evidence_ids: list[str]
    ) -> list[GraphNode]:
        """Read requirements linked to supplied evidence without scanning a project."""
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = self._db.execute(
            "SELECT DISTINCT node.* FROM graph_nodes node "
            "JOIN graph_edges edge ON edge.project_id=node.project_id "
            "AND edge.source_node_id=node.node_id "
            "WHERE node.project_id=? AND node.node_type=? "
            "AND edge.relationship=? AND edge.target_node_id IN ("
            + placeholders
            + ") ORDER BY node.node_id",
            [
                project_id,
                NodeType.REQUIREMENT.value,
                RelationshipType.SUPPORTED_BY.value,
                *evidence_ids,
            ],
        ).fetchall()
        return [
            GraphNode(
                row["node_id"],
                row["project_id"],
                NodeType(row["node_type"]),
                row["title"],
                json.loads(row["attributes_json"]),
            )
            for row in rows
        ]

    def list_approved_lessons(
        self, project_id: str, *, limit: int = 100
    ) -> list[GraphNode]:
        """Read the bounded project-local lesson set approved by a human."""
        rows = self._db.execute(
            "SELECT * FROM graph_nodes WHERE project_id=? AND node_type=? "
            "AND COALESCE(NULLIF(TRIM(json_extract(attributes_json, '$.approved_by')), ''), '')!='' "
            "ORDER BY node_id LIMIT ?",
            (project_id, NodeType.LESSON.value, limit),
        ).fetchall()
        return [
            GraphNode(
                row["node_id"],
                row["project_id"],
                NodeType(row["node_type"]),
                row["title"],
                json.loads(row["attributes_json"]),
            )
            for row in rows
        ]

    def list_evidence_page(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        revision: str | None = None,
        authority: str | None = None,
        query_text: str = "",
        missing_text: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[GraphNode], int]:
        """Return one bounded evidence page without hydrating a whole project."""
        conditions = ["project_id=?", "node_type=?"]
        args: list[object] = [project_id, NodeType.EVIDENCE.value]
        if document_id is not None:
            conditions.append("json_extract(attributes_json, '$.document_id')=?")
            args.append(document_id)
        if revision is not None:
            conditions.append("json_extract(attributes_json, '$.revision')=?")
            args.append(revision)
        if authority is not None:
            conditions.append("json_extract(attributes_json, '$.governing_status')=?")
            args.append(authority)
        if query_text:
            conditions.append("LOWER(title || ' ' || attributes_json) LIKE ?")
            args.append("%" + query_text.casefold() + "%")
        if missing_text:
            conditions.append(
                "COALESCE(NULLIF(TRIM(json_extract(attributes_json, '$.text')), ''), '')=''"
            )
        where = " WHERE " + " AND ".join(conditions)
        total = int(
            self._db.execute(
                "SELECT COUNT(*) FROM graph_nodes" + where, args
            ).fetchone()[0]
        )
        rows = self._db.execute(
            "SELECT * FROM graph_nodes" + where + " ORDER BY node_id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return (
            [
                GraphNode(
                    row["node_id"],
                    row["project_id"],
                    NodeType(row["node_type"]),
                    row["title"],
                    json.loads(row["attributes_json"]),
                )
                for row in rows
            ],
            total,
        )

    def list_human_confirmed_evidence_page(
        self,
        project_id: str,
        *,
        authority: str | None = None,
        revision: str | None = None,
        query_text: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[GraphNode], int]:
        """Return evidence backed by a current human source-authority decision."""
        conditions = [
            "e.project_id=?",
            "e.node_type=?",
            "link.relationship=?",
            "source.node_type IN (?, ?)",
            "json_extract(decision.attributes_json, '$.authority') "
            + ("=?" if authority else "IN (?, ?)"),
            "(source.node_type=? OR NOT EXISTS (SELECT 1 FROM graph_edges preferred "
            "JOIN graph_nodes revision_source "
            "ON revision_source.project_id=preferred.project_id "
            "AND revision_source.node_id=preferred.target_node_id "
            "WHERE preferred.project_id=e.project_id "
            "AND preferred.source_node_id=e.node_id "
            "AND preferred.relationship=? "
            "AND revision_source.node_type=?))",
        ]
        args: list[object] = [
            project_id,
            NodeType.EVIDENCE.value,
            RelationshipType.DERIVED_FROM.value,
            NodeType.DOCUMENT.value,
            NodeType.DOCUMENT_REVISION.value,
            *([authority] if authority else ["governing", "current"]),
            NodeType.DOCUMENT_REVISION.value,
            RelationshipType.DERIVED_FROM.value,
            NodeType.DOCUMENT_REVISION.value,
        ]
        if revision is not None:
            conditions.append("json_extract(e.attributes_json, '$.revision')=?")
            args.append(revision)
        if query_text:
            conditions.append("LOWER(e.title || ' ' || e.attributes_json) LIKE ?")
            args.append("%" + query_text.casefold() + "%")
        where = " WHERE " + " AND ".join(conditions)
        joins = (
            " FROM graph_nodes e "
            "JOIN graph_edges link ON link.project_id=e.project_id "
            "AND link.source_node_id=e.node_id "
            "JOIN graph_nodes source ON source.project_id=link.project_id "
            "AND source.node_id=link.target_node_id "
            "JOIN registry_records control ON control.project_id=source.project_id "
            "AND control.namespace='document_control' AND control.record_id=source.node_id "
            "JOIN graph_nodes decision ON decision.project_id=source.project_id "
            "AND decision.node_id=json_extract(control.payload_json, '$.decision_id')"
        )
        total = int(
            self._db.execute(
                "SELECT COUNT(DISTINCT e.node_id)" + joins + where, args
            ).fetchone()[0]
        )
        rows = self._db.execute(
            "SELECT DISTINCT e.*"
            + joins
            + where
            + " ORDER BY e.node_id LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return (
            [
                GraphNode(
                    row["node_id"],
                    row["project_id"],
                    NodeType(row["node_type"]),
                    row["title"],
                    json.loads(row["attributes_json"]),
                )
                for row in rows
            ],
            total,
        )

    def project_ids(self) -> set[str]:
        return {
            row[0]
            for row in self._db.execute(
                "SELECT project_id FROM graph_nodes UNION SELECT project_id FROM registry_records"
            )
        }

    def get_record(
        self, project_id: str, namespace: str, record_id: str
    ) -> dict | None:
        row = self._db.execute(
            "SELECT payload_json FROM registry_records WHERE project_id=? AND namespace=? AND record_id=?",
            (project_id, namespace, record_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list_records(self, project_id: str, namespace: str) -> list[dict]:
        return [
            json.loads(row[0])
            for row in self._db.execute(
                "SELECT payload_json FROM registry_records WHERE project_id=? AND namespace=? ORDER BY record_id",
                (project_id, namespace),
            )
        ]

    def put_record(
        self,
        project_id: str,
        namespace: str,
        record_id: str,
        payload: dict,
        *,
        replace: bool = False,
    ) -> None:
        self._assert_bound_scope(project_id)
        encoded = _json(payload)
        with self.transaction():
            previous = self.get_record(project_id, namespace, record_id)
            if previous is not None and not replace and _json(previous) != encoded:
                raise GraphIntegrityError(
                    "Immutable registry record conflicts with existing content"
                )
            self._db.execute(
                "INSERT INTO registry_records VALUES (?,?,?,?) ON CONFLICT(project_id,namespace,record_id) DO UPDATE SET payload_json=excluded.payload_json",
                (project_id, namespace, record_id, encoded),
            )

    def backup(self, destination: str | Path) -> None:
        path = Path(destination).expanduser().resolve()
        if self.path != ":memory:" and path == Path(self.path).resolve():
            raise ValueError("Backup destination must differ from source")
        if self._transaction_depth:
            raise ValueError("Backup requires no active write transaction")
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as target:
            self._db.backup(target)

    def _insert_node(self, node: GraphNode) -> None:
        self._assert_bound_scope(node.project_id)
        payload = _json(node.attributes)
        row = self._db.execute(
            "SELECT node_type,title,attributes_json FROM graph_nodes WHERE project_id=? AND node_id=?",
            (node.project_id, node.node_id),
        ).fetchone()
        expected = (node.node_type.value, node.title, payload)
        if row:
            if tuple(row) != expected:
                raise GraphIntegrityError(
                    "A node ID already exists with different content"
                )
            return
        self._db.execute(
            "INSERT INTO graph_nodes VALUES (?,?,?,?,?)",
            (node.project_id, node.node_id, *expected),
        )

    def add_node(self, node: GraphNode) -> None:
        with self._transaction():
            self._insert_node(node)

    def get_node(self, project_id: str, node_id: str) -> GraphNode | None:
        row = self._db.execute(
            "SELECT * FROM graph_nodes WHERE project_id=? AND node_id=?",
            (project_id, node_id),
        ).fetchone()
        if row is None:
            return None
        return GraphNode(
            row["node_id"],
            row["project_id"],
            NodeType(row["node_type"]),
            row["title"],
            json.loads(row["attributes_json"]),
        )

    def _insert_edge(self, edge: GraphEdge) -> None:
        self._assert_bound_scope(edge.project_id)
        for evidence_id in edge.provenance_evidence_ids:
            row = self._db.execute(
                "SELECT node_type FROM graph_nodes WHERE project_id=? AND node_id=?",
                (edge.project_id, evidence_id),
            ).fetchone()
            if row is None or row["node_type"] != NodeType.EVIDENCE.value:
                raise GraphIntegrityError(
                    f"Edge provenance must reference evidence in the same project: {evidence_id}"
                )
        row = self._db.execute(
            "SELECT source_node_id,relationship,target_node_id FROM graph_edges WHERE project_id=? AND edge_id=?",
            (edge.project_id, edge.edge_id),
        ).fetchone()
        expected = (edge.source_node_id, edge.relationship.value, edge.target_node_id)
        if row:
            if tuple(row) != expected:
                raise GraphIntegrityError(
                    "An edge ID already exists with different content"
                )
            old = [
                item[0]
                for item in self._db.execute(
                    "SELECT evidence_id FROM graph_edge_provenance WHERE project_id=? AND edge_id=? ORDER BY evidence_id",
                    (edge.project_id, edge.edge_id),
                )
            ]
            if old != sorted(edge.provenance_evidence_ids):
                raise GraphIntegrityError(
                    "An edge ID already exists with different provenance"
                )
            return
        self._db.execute(
            "INSERT INTO graph_edges VALUES (?,?,?,?,?)",
            (edge.project_id, edge.edge_id, *expected),
        )
        self._db.executemany(
            "INSERT INTO graph_edge_provenance VALUES (?,?,?)",
            [
                (edge.project_id, edge.edge_id, evidence_id)
                for evidence_id in edge.provenance_evidence_ids
            ],
        )

    def add_edge(self, edge: GraphEdge) -> None:
        with self._transaction():
            self._insert_edge(edge)

    def add_subgraph(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        """Atomically add project nodes and edges; foreign keys validate links."""
        with self._transaction():
            for node in nodes:
                self._insert_node(node)
            for edge in edges:
                self._insert_edge(edge)

    def get_edges(self, project_id: str, source_node_id: str) -> list[GraphEdge]:
        rows = self._db.execute(
            "SELECT * FROM graph_edges WHERE project_id=? AND source_node_id=? ORDER BY edge_id",
            (project_id, source_node_id),
        ).fetchall()
        result = []
        for row in rows:
            provenance = [
                item[0]
                for item in self._db.execute(
                    "SELECT evidence_id FROM graph_edge_provenance WHERE project_id=? AND edge_id=? ORDER BY evidence_id",
                    (project_id, row["edge_id"]),
                )
            ]
            result.append(
                GraphEdge(
                    row["edge_id"],
                    row["project_id"],
                    row["source_node_id"],
                    RelationshipType(row["relationship"]),
                    row["target_node_id"],
                    provenance,
                )
            )
        return result

    def list_decisions_for_records(
        self, project_id: str, record_ids: list[str], *, limit: int = 100
    ) -> tuple[list[GraphNode], int]:
        """Find decisions explicitly linked to bounded project evidence/records."""
        if not record_ids:
            return [], 0
        placeholders = ",".join("?" for _ in record_ids)
        joins = (
            " FROM graph_nodes node JOIN graph_edges edge "
            "ON edge.project_id=node.project_id AND edge.source_node_id=node.node_id "
            "WHERE node.project_id=? AND node.node_type=? AND edge.target_node_id IN ("
            + placeholders
            + ") AND edge.relationship IN (?,?,?)"
        )
        relationship_values = [
            RelationshipType.SUPPORTED_BY.value,
            RelationshipType.ADDRESSES.value,
            RelationshipType.RELATES_TO.value,
        ]
        parameters = [
            project_id,
            NodeType.DECISION.value,
            *record_ids,
            *relationship_values,
        ]
        total = int(
            self._db.execute(
                "SELECT COUNT(DISTINCT node.node_id)" + joins, parameters
            ).fetchone()[0]
        )
        rows = self._db.execute(
            "SELECT DISTINCT node.*" + joins + " ORDER BY node.node_id LIMIT ?",
            [*parameters, limit],
        ).fetchall()
        return (
            [
                GraphNode(
                    row["node_id"],
                    row["project_id"],
                    NodeType(row["node_type"]),
                    row["title"],
                    json.loads(row["attributes_json"]),
                )
                for row in rows
            ],
            total,
        )

    def list_decisions_for_issue(
        self, project_id: str, issue_id: str, *, limit: int = 100
    ) -> tuple[list[GraphNode], int]:
        """Read bounded decisions whose provenance names this issue."""
        predicate = (
            "project_id=? AND node_type=? AND "
            "(json_extract(attributes_json,'$.issue_id')=? OR "
            "json_extract(attributes_json,'$.proposal.issue_id')=?)"
        )
        parameters = [project_id, NodeType.DECISION.value, issue_id, issue_id]
        total = int(
            self._db.execute(
                "SELECT COUNT(*) FROM graph_nodes WHERE " + predicate, parameters
            ).fetchone()[0]
        )
        rows = self._db.execute(
            "SELECT * FROM graph_nodes WHERE "
            + predicate
            + " ORDER BY node_id LIMIT ?",
            [*parameters, limit],
        ).fetchall()
        return (
            [
                GraphNode(
                    row["node_id"],
                    row["project_id"],
                    NodeType(row["node_type"]),
                    row["title"],
                    json.loads(row["attributes_json"]),
                )
                for row in rows
            ],
            total,
        )

    def save_issue(
        self,
        issue: EngineeringIssue,
        *,
        supporting_nodes: list[GraphNode] | None = None,
        supporting_edges: list[GraphEdge] | None = None,
    ) -> None:
        self._assert_bound_scope(issue.project_id)
        issue_node = GraphNode(
            issue.issue_id,
            issue.project_id,
            NodeType.ENGINEERING_ISSUE,
            issue.title,
            {"source_finding_id": issue.source_finding_id},
        )
        links = (
            (issue.evidence_ids, RelationshipType.SUPPORTED_BY),
            (issue.requirement_ids, RelationshipType.ADDRESSES),
            (issue.parameter_ids, RelationshipType.AFFECTS),
            (issue.related_entity_ids, RelationshipType.RELATES_TO),
            (issue.action_ids, RelationshipType.RESOLVED_BY),
            (issue.decision_ids, RelationshipType.DECIDED_BY),
            (issue.closure_evidence_ids, RelationshipType.CLOSED_WITH_EVIDENCE),
            (
                [f"document:{item}" for item in issue.source_document_ids],
                RelationshipType.DERIVED_FROM,
            ),
        )
        payload = asdict(issue)
        payload["status"] = issue.status.value
        with self._transaction():
            for node in supporting_nodes or []:
                if node.project_id != issue.project_id:
                    raise GraphIntegrityError(
                        "Supporting node project_id does not match Engineering Issue"
                    )
                self._insert_node(node)
            self._insert_node(issue_node)
            for edge in supporting_edges or []:
                if edge.project_id != issue.project_id:
                    raise GraphIntegrityError(
                        "Supporting edge project_id does not match Engineering Issue"
                    )
                self._insert_edge(edge)
            serialized = _json(payload)
            existing = self._db.execute(
                "SELECT payload_json FROM engineering_issues WHERE project_id=? AND issue_id=?",
                (issue.project_id, issue.issue_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != serialized:
                    raise GraphIntegrityError(
                        "Existing issue lifecycle/content is immutable through import; use a controlled transition"
                    )
                return
            if issue.status != IssueStatus.PROPOSED:
                raise GraphIntegrityError(
                    "New issues must be proposed; use controlled transitions"
                )
            if not issue.human_review_required or issue.closure_evidence_ids:
                raise GraphIntegrityError(
                    "New issues must be unverified and require human review"
                )
            self._db.execute(
                "INSERT INTO engineering_issues VALUES (?,?,?)",
                (issue.project_id, issue.issue_id, serialized),
            )
            for targets, relationship in links:
                for target_id in targets:
                    target = self.get_node(issue.project_id, target_id)
                    if target is None and relationship == RelationshipType.DERIVED_FROM:
                        target_id = "document:" + quote(
                            target_id.removeprefix("document:"), safe=""
                        )
                        target = self.get_node(issue.project_id, target_id)
                    if target is None:
                        raise GraphIntegrityError(
                            f"Issue link target does not exist in project: {target_id}"
                        )
                    expected_type = {
                        RelationshipType.SUPPORTED_BY: NodeType.EVIDENCE,
                        RelationshipType.CLOSED_WITH_EVIDENCE: NodeType.EVIDENCE,
                        RelationshipType.ADDRESSES: NodeType.REQUIREMENT,
                        RelationshipType.AFFECTS: NodeType.PARAMETER,
                        RelationshipType.RESOLVED_BY: NodeType.ACTION,
                        RelationshipType.DECIDED_BY: NodeType.DECISION,
                        RelationshipType.DERIVED_FROM: NodeType.DOCUMENT,
                    }.get(relationship)
                    if expected_type and target.node_type != expected_type:
                        raise GraphIntegrityError(
                            f"Issue {relationship.value} link has wrong node type: {target_id}"
                        )
                    edge_id = f"{issue.issue_id}:{relationship.value}:{target_id}"
                    provenance = (
                        [target_id]
                        if relationship == RelationshipType.SUPPORTED_BY
                        else []
                    )
                    self._insert_edge(
                        GraphEdge(
                            edge_id,
                            issue.project_id,
                            issue.issue_id,
                            relationship,
                            target_id,
                            provenance,
                        )
                    )

    def get_issue(self, project_id: str, issue_id: str) -> EngineeringIssue | None:
        row = self._db.execute(
            "SELECT payload_json FROM engineering_issues WHERE project_id=? AND issue_id=?",
            (project_id, issue_id),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["status"] = IssueStatus(payload["status"])
        return EngineeringIssue(**payload)

    def transition_issue(
        self,
        project_id: str,
        issue_id: str,
        to_status: IssueStatus,
        *,
        actor_id: str,
        rationale: str,
        evidence_ids: list[str] | None = None,
        human_approved: bool = False,
    ) -> EngineeringIssue:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("Lifecycle actor_id must not be blank")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("Lifecycle rationale must not be blank")
        if human_approved is not True:
            raise ValueError(
                "A human approval decision is required for lifecycle transitions"
            )
        target = IssueStatus(to_status)
        evidence_ids = list(evidence_ids or [])
        if target == IssueStatus.CLOSED and not evidence_ids:
            raise ValueError("Closure requires closure evidence")

        allowed = {
            IssueStatus.PROPOSED: {
                IssueStatus.OPEN,
                IssueStatus.REJECTED,
                IssueStatus.HELD,
            },
            IssueStatus.OPEN: {
                IssueStatus.UNDER_REVIEW,
                IssueStatus.ACCEPTED,
                IssueStatus.REJECTED,
                IssueStatus.HELD,
            },
            IssueStatus.UNDER_REVIEW: {
                IssueStatus.OPEN,
                IssueStatus.ACCEPTED,
                IssueStatus.REJECTED,
                IssueStatus.HELD,
            },
            IssueStatus.ACCEPTED: {
                IssueStatus.OPEN,
                IssueStatus.CLOSED,
                IssueStatus.HELD,
            },
            IssueStatus.REJECTED: {IssueStatus.OPEN},
            IssueStatus.HELD: {
                IssueStatus.OPEN,
                IssueStatus.UNDER_REVIEW,
                IssueStatus.REJECTED,
            },
            IssueStatus.CLOSED: {IssueStatus.OPEN},
        }
        current = self.get_issue(project_id, issue_id)
        if current is None:
            raise GraphIntegrityError(
                "Engineering Issue does not exist in this project"
            )
        if target not in allowed[current.status]:
            raise ValueError(
                f"Invalid issue transition: {current.status.value} -> {target.value}"
            )

        now = datetime.now(UTC).isoformat()
        event = {
            "project_id": project_id,
            "issue_id": issue_id,
            "event_id": str(uuid.uuid4()),
            "from_status": current.status.value,
            "to_status": target.value,
            "actor_id": actor_id.strip(),
            "rationale": rationale.strip(),
            "occurred_at": now,
            "evidence_ids": evidence_ids,
            "human_approved": True,
        }
        closure_ids = current.closure_evidence_ids
        if target == IssueStatus.CLOSED:
            closure_ids = list(dict.fromkeys([*closure_ids, *evidence_ids]))
        updated = replace(current, status=target, closure_evidence_ids=closure_ids)
        with self._transaction():
            for evidence_id in evidence_ids:
                row = self._db.execute(
                    "SELECT node_type FROM graph_nodes WHERE project_id=? AND node_id=?",
                    (project_id, evidence_id),
                ).fetchone()
                if row is None or row["node_type"] != NodeType.EVIDENCE.value:
                    raise GraphIntegrityError(
                        f"Lifecycle event evidence is missing or mistyped: {evidence_id}"
                    )
            if target == IssueStatus.CLOSED:
                for evidence_id in evidence_ids:
                    row = self._db.execute(
                        "SELECT node_type FROM graph_nodes WHERE project_id=? AND node_id=?",
                        (project_id, evidence_id),
                    ).fetchone()
                    if row is None:
                        raise GraphIntegrityError(
                            f"Closure evidence does not exist in this project: {evidence_id}"
                        )
                    if row["node_type"] != NodeType.EVIDENCE.value:
                        raise GraphIntegrityError(
                            f"Closure reference is not an evidence node: {evidence_id}"
                        )
                    edge_id = f"{issue_id}:{RelationshipType.CLOSED_WITH_EVIDENCE.value}:{evidence_id}"
                    self._insert_edge(
                        GraphEdge(
                            edge_id,
                            project_id,
                            issue_id,
                            RelationshipType.CLOSED_WITH_EVIDENCE,
                            evidence_id,
                            [evidence_id],
                        )
                    )
            self._db.execute(
                "UPDATE engineering_issues SET payload_json=? WHERE project_id=? AND issue_id=?",
                (_json(asdict(updated)), project_id, issue_id),
            )
            self._db.execute(
                "INSERT INTO issue_lifecycle_events VALUES (?,?,?,?,?)",
                (project_id, issue_id, event["event_id"], now, _json(event)),
            )
        return updated

    def list_lifecycle_events(
        self, project_id: str, issue_id: str
    ) -> list[dict[str, object]]:
        rows = self._db.execute(
            "SELECT payload_json FROM issue_lifecycle_events "
            "WHERE project_id=? AND issue_id=? ORDER BY occurred_at,event_id",
            (project_id, issue_id),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
