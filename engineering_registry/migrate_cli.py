"""Command-line validator and explicit copier for Registry separation."""

from __future__ import annotations

import argparse
import json

from .migration import apply_migration, validate_migration


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, help="Existing shared Registry; never modified"
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--project", action="append")
    parser.add_argument("--organization", action="append")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument(
        "--apply", action="store_true", help="Copy only after validation succeeds"
    )
    args = parser.parse_args(argv)
    report = validate_migration(
        args.source,
        args.data_root,
        project_ids=args.project,
        organization_ids=args.organization,
    )
    if args.apply:
        report["applied"] = apply_migration(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
