"""Trusted local operator commands. MCP never invokes this reviewer entry point."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .demo import run_smoke, seed_demo
from .importers import (
    import_bundle,
    import_legacy_document_registry,
    import_legacy_finding_registry,
)
from .service import Principal, RegistryService
from .store import SQLiteGraphStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Engineering Registry local operator")
    parser.add_argument("--database", default="data/projects/DEMO/registry.sqlite3")
    parser.add_argument("--project", action="append")
    parser.add_argument(
        "--organization", action="append", help="Explicit organisational memory grant"
    )
    parser.add_argument("--actor", default=getpass.getuser())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary")
    ingest = commands.add_parser(
        "import", help="Validate first; --apply records the package"
    )
    ingest.add_argument("source")
    ingest.add_argument("--apply", action="store_true")
    legacy_findings = commands.add_parser(
        "import-finding-json",
        help="Validate a legacy finding JSON snapshot; --apply copies it without modifying the source",
    )
    legacy_findings.add_argument("source")
    legacy_findings.add_argument("--apply", action="store_true")
    legacy_documents = commands.add_parser(
        "import-document-json",
        help="Validate a legacy document JSON registry; --apply copies it without modifying the source",
    )
    legacy_documents.add_argument("source")
    legacy_documents.add_argument("--apply", action="store_true")
    review = commands.add_parser("review")
    review.add_argument("proposal_id")
    review.add_argument("digest")
    review.add_argument("--reject", action="store_true")
    review.add_argument("--rationale", required=True)
    authorize = commands.add_parser("authorize")
    authorize.add_argument("task_id")
    authorize.add_argument("digest")
    authorize.add_argument("--rationale", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("result_id")
    verify.add_argument("digest")
    verify.add_argument("--evidence", action="append", required=True)
    verify.add_argument("--rationale", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination")
    smoke = commands.add_parser(
        "smoke", help="Synthetic end-to-end diagnostic; no real project approvals"
    )
    smoke.add_argument("--destination", default="reports/platform-smoke")
    desk = commands.add_parser("desk", help="Trusted local human review screen")
    desk.add_argument("--demo", action="store_true")
    desk.add_argument("--open", action="store_true")
    desk.add_argument(
        "--organization-database",
        help="Separate organisational Registry for human knowledge promotion",
    )
    commands.add_parser(
        "mcp-config",
        help="Print local stdio configuration; does not connect an application",
    )
    args = parser.parse_args(argv)
    projects = args.project or (
        ["DEMO"] if args.command == "smoke" or getattr(args, "demo", False) else None
    )
    if not projects:
        parser.error("Specify at least one --project grant")
    try:
        database = Path(args.database).expanduser().resolve()
        if args.command == "smoke":
            result = run_smoke(args.destination)
        elif args.command == "desk":
            from .desk import serve

            if args.demo:
                if set(projects) != {"DEMO"}:
                    raise ValueError(
                        "Demonstration desk requires the DEMO project only"
                    )
                seed_demo(database)
            serve(
                database,
                set(projects),
                args.actor,
                organization_ids=set(args.organization or []),
                demo=args.demo,
                open_browser=args.open,
                organization_database=args.organization_database,
            )
            return 0
        elif args.command == "mcp-config":
            result = {
                "mcpServers": {
                    "engineering-registry": {
                        "command": str(Path(sys.executable).absolute()),
                        "args": [
                            "-m",
                            "engineering_registry.mcp_server",
                            "--database",
                            str(database),
                            *[
                                arg
                                for project in projects
                                for arg in ("--project", project)
                            ],
                        ],
                    }
                }
            }
        else:
            with SQLiteGraphStore(database) as store:
                registry = RegistryService(
                    store, Principal(args.actor, frozenset(projects), "reviewer")
                )
                if args.command == "summary":
                    result = {
                        p: {
                            "records": len(registry.list_records(p)),
                            "pending_proposals": len(
                                [
                                    r
                                    for r in registry.list_proposals(p)
                                    if r["status"] == "pending"
                                ]
                            ),
                        }
                        for p in projects
                    }
                elif args.command == "backup":
                    registry.backup(args.destination)
                    result = {
                        "status": "backed_up",
                        "destination": str(
                            Path(args.destination).expanduser().resolve()
                        ),
                    }
                elif len(projects) != 1:
                    raise ValueError("This command requires one project grant")
                elif args.command == "import":
                    source = Path(args.source).expanduser()
                    if source.stat().st_size > 10_000_000:
                        raise ValueError("Source package exceeds 10 MB limit")
                    result = import_bundle(
                        registry,
                        json.loads(source.read_text(encoding="utf-8")),
                        dry_run=not args.apply,
                    )
                elif args.command == "import-finding-json":
                    source = Path(args.source).expanduser()
                    if source.stat().st_size > 10_000_000:
                        raise ValueError("Source registry exceeds 10 MB limit")
                    result = import_legacy_finding_registry(
                        registry, source, dry_run=not args.apply
                    )
                elif args.command == "import-document-json":
                    source = Path(args.source).expanduser()
                    if source.stat().st_size > 10_000_000:
                        raise ValueError("Source registry exceeds 10 MB limit")
                    result = import_legacy_document_registry(
                        registry, source, project_id=projects[0], dry_run=not args.apply
                    )
                elif args.command == "review":
                    result = registry.review_proposal(
                        projects[0],
                        args.proposal_id,
                        args.digest,
                        accept=not args.reject,
                        rationale=args.rationale,
                    )
                elif args.command == "authorize":
                    from engineering_execution import ExecutionService

                    result = ExecutionService(registry).authorize_task(
                        projects[0], args.task_id, args.digest, args.rationale
                    )
                else:
                    result = registry.verify_result(
                        projects[0],
                        args.result_id,
                        args.digest,
                        args.evidence,
                        args.rationale,
                    )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, PermissionError, OSError) as exc:
        print(f"Operation was not completed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
