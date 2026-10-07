import json
import subprocess
import sys

from engineering_registry.demo import source_bundle
from engineering_registry.store import SQLiteGraphStore


def test_cli_import_is_dry_run_by_default_and_apply_is_explicit(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(source_bundle()))
    database = tmp_path / "registry.sqlite"
    command = [
        sys.executable,
        "-m",
        "engineering_registry.cli",
        "--database",
        str(database),
        "--project",
        "DEMO",
        "import",
        str(source),
    ]
    checked = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(checked.stdout)["status"] == "dry_run"
    with SQLiteGraphStore(database) as store:
        assert store.list_nodes("DEMO") == []
    applied = subprocess.run(
        command + ["--apply"], check=True, capture_output=True, text=True
    )
    assert json.loads(applied.stdout)["status"] == "imported"
    with SQLiteGraphStore(database) as store:
        assert store.get_issue("DEMO", "issue:F-LOAD").status.value == "proposed"


def test_cli_requires_explicit_organization_grant_for_memory_desk(
    monkeypatch, tmp_path
):
    from engineering_registry import desk
    from engineering_registry.cli import main

    captured = {}

    def fake_serve(database, projects, actor, **options):
        captured.update(
            database=database,
            projects=projects,
            actor=actor,
            organization_ids=options["organization_ids"],
        )

    monkeypatch.setattr(desk, "serve", fake_serve)
    assert (
        main(
            [
                "--database",
                str(tmp_path / "unused.sqlite"),
                "--project",
                "P1",
                "--organization",
                "ACME",
                "desk",
            ]
        )
        == 0
    )
    assert captured["projects"] == {"P1"}
    assert captured["organization_ids"] == {"ACME"}
