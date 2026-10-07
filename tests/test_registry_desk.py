import importlib
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

import pytest

from engineering_registry.models import GraphEdge, GraphNode, NodeType, RelationshipType
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore


def modules():
    assert importlib.util.find_spec("engineering_registry.desk") is not None
    return importlib.import_module(
        "engineering_registry.desk"
    ), importlib.import_module("engineering_registry.demo")


@pytest.mark.parametrize("run_class", ["routine", "engineering", "critical"])
def test_desk_shows_run_class_and_review_state(tmp_path, run_class):
    desk, demo = modules()
    database = tmp_path / "run-desk.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        store.put_record(
            "DEMO",
            "project_runs",
            "RUN-1",
            {
                "run_id": "RUN-1",
                "issue_id": "issue:F-LOAD",
                "execution_class": run_class,
                "classification_reasons": ["<source review>"],
                "policy_id": "P" if run_class == "routine" else None,
                "human_plan_approval_id": None,
                "gate1_review_id": None,
                "gate2_review_id": None,
            },
        )
        page = desk.render_page(human, demo=True)
        assert f"{run_class.title()} run" in page
        assert "&lt;source review&gt;" in page
        assert "<source review>" not in page
        if run_class == "critical":
            assert "Human plan approval: pending" in page


def test_desk_separates_material_comment_and_execution_note(tmp_path):
    desk, demo = modules()
    database = tmp_path / "notes-desk.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        store.put_record(
            "DEMO",
            "execution_logs",
            "N1",
            {
                "route": "execution_log",
                "note_id": "N1",
                "issue_id": "issue:F-LOAD",
                "statement": "Layout choice",
                "impact_flags": {"scope": "no"},
            },
        )
        store.put_record(
            "DEMO",
            "project_run_reviews",
            "REV1",
            {
                "review_id": "REV1",
                "run_id": "RUN1",
                "report": {
                    "material_comments": [
                        {
                            "what": "<critical concern>",
                            "required_response": "Review load",
                        }
                    ]
                },
            },
        )
        page = desk.render_page(human, demo=True)
        assert "Execution log (micro-decisions)" in page
        assert "Layout choice" in page
        assert "Material Reviewer comment" in page
        assert "&lt;critical concern&gt;" in page


def test_desk_snapshot_guard_and_complete_demo(tmp_path):
    desk, demo = modules()
    database = tmp_path / "memory.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        current = human.issue_context("DEMO", "issue:F-LOAD")
        with pytest.raises(ValueError, match="changed"):
            desk.apply_action(
                human,
                {
                    "operation": "transition",
                    "project_id": "DEMO",
                    "record_id": "issue:F-LOAD",
                    "digest": "stale",
                    "status": "open",
                    "rationale": "Checked",
                },
            )
        desk.apply_action(
            human,
            {
                "operation": "transition",
                "project_id": "DEMO",
                "record_id": "issue:F-LOAD",
                "digest": current["output_digest"],
                "status": "open",
                "rationale": "Checked",
            },
        )
        assert store.get_issue("DEMO", "issue:F-LOAD").status.value == "open"
    report = demo.run_smoke(tmp_path / "synthetic-smoke")
    assert report["issue_status"] == "closed"
    assert report["result_verified"] is True
    assert report["synthetic_decisions"] is True
    assert report["no_models_called"] is True


def test_desk_collects_human_project_brief_and_issue_mandate(tmp_path):
    desk, demo = modules()
    database = tmp_path / "intent.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        human = RegistryService(
            store, Principal("project-owner", frozenset({"DEMO"}), "reviewer")
        )
        brief_fields = {
            "operation": "record_client_brief",
            "project_id": "DEMO",
            "record_id": "DEMO",
            "brief_id": "B1",
            "brief_title": "Project purpose and constraints",
            "brief_purpose": "Compare the controlled source revisions.",
            "evidence_purpose": "evidence:EA",
            "brief_success_criteria": "Retain exact changed text and its source identity.",
            "evidence_success_criteria": "evidence:EA",
        }
        desk.apply_action(human, brief_fields)
        brief = human.client_project_brief("DEMO")
        assert brief["approved_by"] == "project-owner"
        assert brief["definition"]["sections"]["scope"][0]["basis"] == "unknown"
        desk.apply_action(
            human,
            {
                "operation": "record_client_mandate",
                "project_id": "DEMO",
                "record_id": "issue:F-LOAD",
                "mandate_id": "M1",
                "objective": "Compare two controlled revisions.",
                "scope": "Compare only the supplied source excerpts.",
                "acceptance_criteria": "Both source revisions are identified\nChanged lines are traceable",
                "mandate_evidence_ids": "evidence:EA,evidence:EB",
                "allowed_tools": "compare_revision",
                "risk_level": "low",
                "importance_level": "low",
                "simple": "yes",
                "reversible": "yes",
            },
        )
        mandate = human.client_mandate("DEMO", "issue:F-LOAD")
        assert mandate["approved_by"] == "project-owner"
        assert mandate["definition"]["allowed_tools"] == ["compare_revision"]
        page = desk.render_page(human, demo=True)
        assert (
            "Client project brief: overall objectives, context and current/stale status"
            in page
        )
        assert "Save human-approved issue mandate" not in page


def test_desk_uses_digest_bound_org_import_and_status_review(tmp_path):
    desk, _ = modules()
    database = tmp_path / "org.sqlite"
    with SQLiteGraphStore(database) as store:
        registry = RegistryService(
            store,
            Principal(
                "engineer",
                frozenset({"P1", "P2"}),
                "reviewer",
                organization_ids=frozenset({"ACME"}),
            ),
        )
        registry.register(GraphNode("E1", "P1", NodeType.EVIDENCE, "Source"))
        registry.propose_issue("P1", "I1", "Review", ["E1"])
        registry.register(GraphNode("E2", "P2", NodeType.EVIDENCE, "Target source"))
        registry.propose_issue("P2", "I2", "Review target", ["E2"])
        registry.promote_lesson(
            "P1", "L1", "Validated project lesson", ["I1"], ["E1"], "Reviewed"
        )
        registry.promote_organizational_lesson(
            "ACME",
            "OL1",
            "Bounded lesson",
            [("P1", "L1")],
            observation="Observed condition",
            result="Reviewed result",
            interpretation="Limited inference",
            validated_statement="Check project basis first",
            applicability="Matching constraints",
            limitations="Not a design rule",
            relevant_standards=[],
            review_due="2027-10-01",
            rationale="Human validation",
        )
        lesson = registry.get_record("ORG:ACME", "OL1")
        status = store.get_record("ORG:ACME", "organizational_lesson_status", "OL1")
        lesson_digest = digest({"lesson": lesson, "status": status})
        with pytest.raises(ValueError, match="changed"):
            desk.apply_action(
                registry,
                {
                    "operation": "import_organizational_lesson",
                    "project_id": "P2",
                    "record_id": "OL1",
                    "organization_id": "ACME",
                    "digest": "stale",
                    "rationale": "Reviewed for P2",
                },
            )
        desk.apply_action(
            registry,
            {
                "operation": "import_organizational_lesson",
                "project_id": "P2",
                "record_id": "OL1",
                "organization_id": "ACME",
                "digest": lesson_digest,
                "import_id": "IMP1",
                "rationale": "Reviewed for P2",
            },
        )
        assert "IMP1" in registry.task_context("P2", "I2")["lesson_ids"]
        with pytest.raises(ValueError, match="changed"):
            desk.apply_action(
                registry,
                {
                    "operation": "review_organizational_lesson",
                    "project_id": "ORG:ACME",
                    "record_id": "OL1",
                    "digest": "stale",
                    "status": "retired",
                    "rationale": "No longer applicable",
                },
            )
        desk.apply_action(
            registry,
            {
                "operation": "review_organizational_lesson",
                "project_id": "ORG:ACME",
                "record_id": "OL1",
                "digest": lesson_digest,
                "status": "retired",
                "rationale": "No longer applicable",
            },
        )
        assert "IMP1" not in registry.task_context("P2", "I2")["lesson_ids"]


def test_desk_records_a_blank_closure_as_unreasoned_not_verified_evidence(tmp_path):
    desk, demo = modules()
    database = tmp_path / "memory.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        for status in ("open", "accepted"):
            context = human.issue_context("DEMO", "issue:F-LOAD")
            desk.apply_action(
                human,
                {
                    "operation": "transition",
                    "project_id": "DEMO",
                    "record_id": "issue:F-LOAD",
                    "digest": context["output_digest"],
                    "status": status,
                    "rationale": "Reviewed the cited source.",
                },
            )
        context = human.issue_context("DEMO", "issue:F-LOAD")
        desk.apply_action(
            human,
            {
                "operation": "transition",
                "project_id": "DEMO",
                "record_id": "issue:F-LOAD",
                "digest": context["output_digest"],
                "status": "closed",
                "rationale": "",
            },
        )
        closure = next(
            decision
            for decision in human.list_records("DEMO", "decision")
            if decision["attributes"].get("reasoning_evidence_status")
            == "no_human_reasoning"
        )
        assert store.get_issue("DEMO", "issue:F-LOAD").status.value == "closed"
        assert closure["attributes"]["reasoning_evidence_ids"] == []


def test_desk_requires_session_and_same_origin_and_escapes_source(tmp_path):
    desk, demo = modules()
    database = tmp_path / "memory.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        registry = RegistryService(store, Principal("setup", frozenset({"DEMO"})))
        registry.propose_issue(
            "DEMO",
            "injection-test",
            "<script>alert('source')</script>",
            ["evidence:EA"],
        )
    server, url = desk.make_server(database, {"DEMO"}, "human", demo=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = url.split("?")[0]
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base)
        assert denied.value.code == 403
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        html = browser.open(url).read().decode()
        assert "Demonstration" in html and "Authorise" in html
        with pytest.raises(urllib.error.HTTPError) as invalid_offset:
            browser.open(base + "?evidence_offset=-1")
        assert invalid_offset.value.code == 400
        foreign = urllib.request.Request(
            base + "action",
            b"operation=transition&project_id=DEMO",
            headers={"Origin": "https://foreign.invalid"},
        )
        with pytest.raises(urllib.error.HTTPError) as blocked:
            browser.open(foreign)
        assert blocked.value.code == 403
        request = urllib.request.Request(
            base + "action",
            b"operation=unknown&project_id=OTHER",
            headers={"Origin": base.rstrip("/")},
        )
        with pytest.raises(urllib.error.HTTPError) as ungranted:
            browser.open(request)
        assert ungranted.value.code == 400
        assert "&lt;script&gt;" in html and "<script>" not in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_proposal_readiness_is_visible_without_issue_or_approval_shortcut(tmp_path):
    desk, demo = modules()
    database = tmp_path / "proposal.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("agent", frozenset({"DEMO"})))
        agent.propose_issue(
            "DEMO",
            "issue:PROPOSAL",
            "Synthetic proposal review",
            [],
            work_type="proposal_readiness",
        )
        submission_id = "submission:DESK"
        submission = GraphNode(
            submission_id,
            "DEMO",
            NodeType.SUBMISSION,
            "Synthetic proposal",
            {
                "issue_id": "issue:PROPOSAL",
                "candidate_revision_id": "document-revision:CANDIDATE:1",
                "template_revision_id": "document-revision:TEMPLATE:A",
                "source_requirement_ids": ["requirement:R1"],
                "source_evidence_ids": ["evidence:C1", "evidence:T1"],
                "scope_matrix": [
                    {
                        "matrix_id": "M1",
                        "requirement_id": "requirement:R1",
                        "disposition": "included",
                    }
                ],
                "protected_sections": [
                    {
                        "label": "Commercial wording",
                        "template_evidence_id": "evidence:T1",
                        "candidate_evidence_id": "evidence:C1",
                    }
                ],
            },
        )
        output = {
            "submission_id": submission_id,
            "submission_digest": "synthetic-digest",
            "candidate_revision_id": "document-revision:CANDIDATE:1",
            "template_revision_id": "document-revision:TEMPLATE:A",
            "source_checks": [{"evidence_id": "evidence:C1", "passed": True}],
            "protected_section_checks": [
                {
                    "template_evidence_id": "evidence:T1",
                    "candidate_evidence_id": "evidence:C1",
                    "unchanged": False,
                    "removed_lines": ["Old"],
                    "added_lines": ["New"],
                }
            ],
            "requirement_coverage": {"exact_coverage": True},
            "unresolved_matrix_rows": [],
            "ready_for_human_review": False,
            "warnings": ["Template wording changed; resolve before review."],
        }
        result_id = "result:PROPOSAL"
        result = GraphNode(
            result_id,
            "DEMO",
            NodeType.RESULT,
            "Proposal result",
            {
                "issue_id": "issue:PROPOSAL",
                "task": "PT",
                "method": "validate_proposal_readiness",
                "outputs": output,
                "evidence_ids": [],
                "inputs": {"task_digest": "task-digest"},
            },
        )
        assessment = GraphNode(
            "assessment:PROPOSAL",
            "DEMO",
            NodeType.ASSESSMENT,
            "Proposal held",
            {
                "issue_id": "issue:PROPOSAL",
                "result_id": result_id,
                "produced_at": "2026-10-03T00:00:00+00:00",
                "unknowns": ["Human review remains required."],
                "checks": [
                    {
                        "criterion": "proposal_readiness",
                        "passed": False,
                        "detail": "Protected wording changed.",
                    }
                ],
            },
        )
        store.add_subgraph(
            [submission, result, assessment],
            [
                GraphEdge(
                    "submission:issue",
                    "DEMO",
                    submission_id,
                    RelationshipType.RELATES_TO,
                    "issue:PROPOSAL",
                )
            ],
        )
        store.put_record(
            "DEMO",
            "tasks",
            "PT",
            {
                "task_id": "PT",
                "project_id": "DEMO",
                "issue_id": "issue:PROPOSAL",
                "tool": "validate_proposal_readiness",
                "parameters": {"submission_id": submission_id},
                "state": "succeeded",
                "result_id": result_id,
                "task_digest": "task-digest",
            },
        )

        page = desk.render_page(agent, demo=False)
        assert "Proposal readiness" in page
        assert "document-revision:CANDIDATE:1" in page
        assert "document-revision:TEMPLATE:A" in page
        assert "included" in page
        assert "Old" in page and "New" in page
        assert "Template wording changed; resolve before review." in page
        assert "Not ready for human review" in page
        assert "Close issue after checking evidence" not in page
        assert "Verify and accept output" not in page
        assert "Approve proposal" not in page


def test_desk_shows_and_records_digest_bound_human_task_decision_review(tmp_path):
    desk, demo = modules()
    database = tmp_path / "task-decisions.sqlite"
    demo.seed_demo(database)
    with SQLiteGraphStore(database) as store:
        agent = RegistryService(store, Principal("document-ai", frozenset({"DEMO"})))
        human = RegistryService(
            store, Principal("engineer", frozenset({"DEMO"}), "reviewer")
        )
        proposal = agent.record_task_decision(
            "DEMO",
            "issue:F-LOAD",
            "decision:task:desk",
            "Retain the interface while checking the changed load.",
            "The source shows that the interface arrangement remains fixed.",
            ["evidence:EB"],
            decision_level="D3",
            alternatives=["Change the interface arrangement"],
        )
        page = desk.render_page(human, demo=False)
        assert "Proposed Engineering AI task decision" in page
        assert "Accept task decision" in page
        assert "Reject task decision" in page
        with pytest.raises(ValueError, match="Stale"):
            desk.apply_action(
                human,
                {
                    "operation": "review_task_decision",
                    "project_id": "DEMO",
                    "record_id": proposal["node_id"],
                    "digest": "stale",
                    "accept": "yes",
                    "evidence_ids": "evidence:EB",
                    "rationale": "Reviewed the source and accepted the constraint.",
                },
            )
        desk.apply_action(
            human,
            {
                "operation": "review_task_decision",
                "project_id": "DEMO",
                "record_id": proposal["node_id"],
                "digest": digest(proposal),
                "accept": "yes",
                "evidence_ids": "evidence:EB",
                "rationale": "Reviewed the source and accepted the constraint.",
            },
        )
        decision = next(
            record
            for record in agent.task_context("DEMO", "issue:F-LOAD")["records"]
            if record["node_id"] == proposal["node_id"]
        )
        assert (
            decision["attributes"]["human_review"]["attributes"]["disposition"]
            == "accept"
        )
