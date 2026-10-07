"""Clearly labelled synthetic data and an offline end-to-end diagnostic."""

from __future__ import annotations

import html
import json
import uuid
from pathlib import Path

from .importers import import_bundle
from .service import Principal, RegistryService, digest
from .store import SQLiteGraphStore


def source_bundle() -> dict:
    return {
        "schema_version": 1,
        "project_id": "DEMO",
        "project_title": "Demonstration project",
        "documents": [
            {
                "document_id": "BASIS-A",
                "title": "Synthetic design basis",
                "revision": "A",
            },
            {
                "document_id": "BASIS-B",
                "title": "Synthetic updated basis",
                "revision": "B",
                "supersedes": ["BASIS-A"],
            },
        ],
        "evidence": [
            {
                "evidence_id": "EA",
                "document_id": "BASIS-A",
                "revision": "A",
                "page": 1,
                "locator": "page=1",
                "governing_status": "superseded",
                "text": "Design load: 10 kN\nInspect weekly",
            },
            {
                "evidence_id": "EB",
                "document_id": "BASIS-B",
                "revision": "B",
                "page": 1,
                "locator": "page=1",
                "governing_status": "current",
                "text": "Design load: 20 kN\nInspect weekly",
            },
        ],
        "requirements": [
            {
                "requirement_id": "R-LOAD",
                "source_text": "Review changes to the design load.",
                "source_evidence_ids": ["EB"],
                "provenance": {"source_document_id": "BASIS-B", "page": 1},
            }
        ],
        "findings": [
            {
                "finding_id": "F-LOAD",
                "title": "Design load changed",
                "finding": "Synthetic load wording changed between revisions; review is required.",
                "source_evidence_ids": ["EA", "EB"],
                "requirement_ids": ["R-LOAD"],
                "source_document_ids": ["BASIS-A", "BASIS-B"],
                "recommendation": "Compare the two source excerpts and review the change.",
            }
        ],
    }


def seed_demo(database: str | Path) -> dict:
    with SQLiteGraphStore(database) as store:
        registry = RegistryService(
            store, Principal("demo-source-loader", frozenset({"DEMO"}))
        )
        return import_bundle(registry, source_bundle(), dry_run=False)


def run_smoke(destination: str | Path) -> dict:
    """Automated synthetic approvals are test fixtures, never live human authority."""
    from engineering_document_ai_brain import DocumentAIWorkflow
    from engineering_execution import ExecutionService

    directory = Path(destination).expanduser().resolve() / (
        "run-" + uuid.uuid4().hex[:12]
    )
    directory.mkdir(parents=True)
    database = directory / "registry.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(
            store, Principal("synthetic-worker", frozenset({"DEMO"}))
        )
        human = RegistryService(
            store, Principal("synthetic-test-reviewer", frozenset({"DEMO"}), "reviewer")
        )
        for state in ("open", "accepted"):
            p = agent.propose_transition(
                "DEMO", "issue:F-LOAD", state, "Synthetic test fixture"
            )
            human.review_proposal(
                "DEMO",
                p["proposal_id"],
                p["proposal_digest"],
                accept=True,
                rationale="Synthetic test fixture",
            )
        workflow, reviewer = DocumentAIWorkflow(agent), ExecutionService(human)
        task = workflow.structure_task(
            "DEMO",
            "compare-load",
            "issue:F-LOAD",
            "compare_revision",
            {"base_evidence_id": "evidence:EA", "head_evidence_id": "evidence:EB"},
        )
        reviewer.authorize_task(
            "DEMO", task["task_id"], task["task_digest"], "Synthetic scope check"
        )
        outcome = workflow.execute_and_assess("DEMO", task["task_id"])
        result, assessment = outcome["result"], outcome["assessment"]
        if result.get("node_type") != "result":
            raise RuntimeError("Synthetic execution failed")
        decision = human.decide_result(
            "DEMO",
            result["node_id"],
            digest(result),
            assessment["node_id"],
            digest(assessment),
            "accept",
            ["evidence:EB"],
            "Synthetic result check",
        )
        closure = agent.propose_transition(
            "DEMO", "issue:F-LOAD", "closed", "Synthetic closure", ["evidence:EB"]
        )
        human.review_proposal(
            "DEMO",
            closure["proposal_id"],
            closure["proposal_digest"],
            accept=True,
            rationale="Synthetic test fixture",
        )
        human.promote_lesson(
            "DEMO",
            "lesson:revision-control",
            "Check changes against both revisions",
            ["issue:F-LOAD"],
            ["evidence:EA", "evidence:EB"],
            "Synthetic lesson fixture",
        )
        human.backup(directory / "backup.sqlite")
        closed_issue = store.get_issue("DEMO", "issue:F-LOAD")
        if closed_issue is None:
            raise RuntimeError("Synthetic issue was not retained")
        report = {
            "synthetic_decisions": True,
            "no_models_called": True,
            "database": str(database),
            "issue_status": closed_issue.status.value,
            "result_verified": bool(human.list_records("DEMO", "verification")),
            "result": result,
            "brain_task": task["brain_plan"],
            "assessment": assessment,
            "final_decision": decision,
            "history": human.history("DEMO", "issue:F-LOAD"),
        }
    (directory / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (directory / "report.html").write_text(
        "<!doctype html><html><meta charset='utf-8'><title>Engineering Platform diagnostic</title>"
        "<body style='font:18px system-ui;max-width:1000px;margin:40px auto'><h1>Offline diagnostic passed</h1>"
        "<p>This uses synthetic sources and simulated reviewer decisions. It does not approve a real engineering project.</p>"
        "<p>Document AI task → authorised V2 output → Document AI assessment → simulated human verification/decision → Registry → approved lesson → backup.</p>"
        "<pre style='white-space:pre-wrap'>"
        + html.escape(json.dumps(report, indent=2))
        + "</pre></body></html>",
        encoding="utf-8",
    )
    return {**report, "report_path": str(directory / "report.html")}
