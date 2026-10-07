import pytest

from engineering_registry.demo import seed_demo
from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore


@pytest.fixture
def registry(tmp_path):
    database = tmp_path / "workbench.sqlite"
    seed_demo(database)
    with SQLiteGraphStore(database) as store:
        yield RegistryService(
            store, Principal("human", frozenset({"DEMO"}), "reviewer")
        )


@pytest.mark.parametrize("view", ["activity", "evidence", "findings", "overview"])
def test_unloadable_finding_does_not_disable_workbench(registry, monkeypatch, view):
    from engineering_registry.workbench import render_workbench

    def oversized_context(*args, **kwargs):
        raise ValueError("Issue source graph exceeds bounded context limit")

    monkeypatch.setattr(registry, "issue_context", oversized_context)
    page = render_workbench(registry, demo=True, view=view)
    assert "Engineering Workbench" in page
    if view == "findings":
        assert "UNKNOWN / INSUFFICIENT INFORMATION" in page
        assert 'name="context_digest"' not in page


def test_revision_detail_excludes_evidence_from_other_revision(registry):
    from engineering_registry.importers import canonical_id, import_bundle
    from engineering_registry.workbench import render_workbench

    for revision in ["A", "B"]:
        import_bundle(
            registry,
            {
                "schema_version": 1,
                "project_id": "DEMO",
                "documents": [
                    {
                        "document_id": "SPEC",
                        "title": "Specification",
                        "revision": revision,
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "SPEC-" + revision,
                        "document_id": "SPEC",
                        "revision": revision,
                        "page": 1,
                        "locator": "page=1",
                        "text": "INSTRUCTION-ONLY-" + revision,
                    }
                ],
            },
            dry_run=False,
        )
    page = render_workbench(
        registry,
        demo=True,
        view="revisions",
        record=canonical_id("document_revision", "SPEC:B"),
    )
    assert "INSTRUCTION-ONLY-B" in page
    assert "INSTRUCTION-ONLY-A" not in page


@pytest.mark.parametrize(
    "view",
    ["overview", "evidence", "findings", "runs", "revisions", "knowledge", "activity"],
)
def test_workbench_views_and_escaped_search(registry, view):
    from engineering_registry.workbench import render_workbench

    page = render_workbench(
        registry, demo=True, view=view, search="<script>alert(1)</script>"
    )
    assert "Engineering Workbench" in page
    assert "Demonstration" in page
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "/desk" in page


def test_workbench_rejects_ungranted_project_and_invalid_view(registry):
    from engineering_registry.workbench import render_workbench

    with pytest.raises(PermissionError):
        render_workbench(registry, demo=True, project="OTHER")
    with pytest.raises(ValueError):
        render_workbench(registry, demo=True, view="invented")


def test_finding_sources_and_human_actions_use_current_digest(registry):
    from engineering_registry.workbench import render_workbench

    page = render_workbench(registry, demo=True, view="findings", record="issue:F-LOAD")
    context = registry.issue_context("DEMO", "issue:F-LOAD")
    assert context["output_digest"] in page
    assert "evidence%3AEB" in page
    assert "Request more evidence" in page
    assert 'name="rationale"' in page


def test_revision_lineage_and_unknown_run_are_explicit(registry):
    from engineering_registry.workbench import render_workbench

    page = render_workbench(registry, demo=True, view="revisions")
    assert "superseded" in page
    registry.store.put_record(
        "DEMO", "project_runs", "R", {"run_id": "R", "execution_class": "critical"}
    )
    page = render_workbench(registry, demo=True, view="runs")
    assert "UNKNOWN" in page
    assert "Human plan" in page
    assert "Human outcome" in page


def test_human_amendment_preserves_source_and_rejects_stale_snapshot(registry):
    before = registry.issue_context("DEMO", "issue:F-LOAD")
    decision = registry.review_finding(
        "DEMO",
        "issue:F-LOAD",
        before["output_digest"],
        "Revised human interpretation",
        "OBSERVATION",
        "LOW",
        "Checked the source context",
    )
    after = registry.issue_context("DEMO", "issue:F-LOAD")
    assert before["issue"] == after["issue"]
    assert decision["node_id"] in {item["node_id"] for item in after["linked_records"]}
    assert before["output_digest"] != after["output_digest"]
    with pytest.raises(ValueError, match="changed"):
        registry.review_finding(
            "DEMO",
            "issue:F-LOAD",
            before["output_digest"],
            "Old",
            "OBSERVATION",
            "HIGH",
            "Old view",
        )
    agent = RegistryService(registry.store, Principal("agent", frozenset({"DEMO"})))
    with pytest.raises(PermissionError):
        agent.review_finding(
            "DEMO",
            "issue:F-LOAD",
            after["output_digest"],
            "AI",
            "OBSERVATION",
            "LOW",
            "AI cannot approve",
        )


def test_real_dependency_schedule_and_transitive_rerun_preview(run_fixture):
    from engineering_ai_system.project_run import ProjectRunService
    from engineering_registry.workbench import render_workbench
    from tests.support.project_run_harness import alignment_report, workflow_for

    _, agent, human = run_fixture
    service = ProjectRunService(
        agent, workflow_for(run_fixture), reviewer=alignment_report
    )
    service.propose(
        "DEMO",
        "RUN",
        "issue:RUN",
        [
            {
                "task_id": name,
                "tool": "compare_revision",
                "parameters": {
                    "base_evidence_id": "evidence:EA",
                    "head_evidence_id": "evidence:EB",
                },
                "depends_on": predecessors,
            }
            for name, predecessors in [("A", []), ("B", ["A"]), ("C", ["B"]), ("D", [])]
        ],
    )
    before = service.get_status("DEMO", "RUN")
    page = render_workbench(human, demo=True, view="runs", record="RUN", impact="A")
    assert "Dependency graph" in page and "blocked" in page
    assert "Potentially affected: A, B, C" in page
    assert "task_authorization_required" in page
    assert service.get_status("DEMO", "RUN") == before


def test_gate_projection_shows_actual_review_and_detects_changed_sources(run_fixture):
    from engineering_ai_system.project_run import ProjectRunService
    from engineering_registry.workbench import render_workbench
    from tests.support.project_run_harness import alignment_report, workflow_for

    _, agent, human = run_fixture
    service = ProjectRunService(
        agent, workflow_for(run_fixture), reviewer=alignment_report
    )
    service.propose(
        "DEMO",
        "GATE",
        "issue:RUN",
        [
            {
                "task_id": "T",
                "tool": "compare_revision",
                "parameters": {
                    "base_evidence_id": "evidence:EA",
                    "head_evidence_id": "evidence:EB",
                },
            }
        ],
    )
    service.review("DEMO", "GATE", "plan")
    assert service.get_gate_status("DEMO", "GATE", "plan")["status"] == "aligned"
    page = render_workbench(human, demo=True, view="runs", record="GATE")
    assert "aligned" in page and "current" in page
    context = human.issue_context("DEMO", "issue:RUN")
    human.review_finding(
        "DEMO",
        "issue:RUN",
        context["output_digest"],
        "Changed interpretation",
        "VERIFICATION REQUIRED",
        "MEDIUM",
        "New interpretation affects current basis",
    )
    status = service.get_gate_status("DEMO", "GATE", "plan")
    assert status["status"] == "stale" and not status["current"]
    page = render_workbench(human, demo=True, view="runs", record="GATE")
    assert "stale" in page


def test_evidence_form_records_control_and_stale_retry_fails(registry):
    from engineering_registry.desk import apply_action
    from engineering_registry.workbench import render_workbench

    snapshot = registry.evidence_review("DEMO", "evidence:EB")["review_digest"]
    page = render_workbench(registry, demo=True, view="evidence", record="evidence:EB")
    assert snapshot in page and 'name="status"' in page
    fields = {
        "project_id": "DEMO",
        "record_id": "evidence:EB",
        "operation": "review_evidence",
        "digest": snapshot,
        "status": "visually_reviewed",
        "rationale": "Checked the original page",
    }
    apply_action(registry, fields)
    assert registry.evidence_review("DEMO", "evidence:EB")["human_reviewed"]
    with pytest.raises(ValueError, match="changed"):
        apply_action(registry, fields)


def test_revision_view_traces_affected_finding_and_human_decision(registry):
    from engineering_registry.workbench import render_workbench

    context = registry.issue_context("DEMO", "issue:F-LOAD")
    review = registry.review_finding(
        "DEMO",
        "issue:F-LOAD",
        context["output_digest"],
        "Amended interpretation",
        "OBSERVATION",
        "LOW",
        "Source reviewed",
    )
    page = render_workbench(
        registry, demo=True, view="revisions", record="document:BASIS-B"
    )
    assert "Referenced by" in page
    assert "issue%3AF-LOAD" in page
    assert review["node_id"].replace(":", "%3A") in page


def test_pending_proposals_and_overview_search_are_actionable(registry):
    from engineering_registry.workbench import render_workbench

    proposal = registry.propose_transition(
        "DEMO", "issue:F-LOAD", "open", "Please review the source"
    )
    page = render_workbench(registry, demo=True, view="findings")
    assert proposal["proposal_digest"] in page
    assert "Approve requested change" in page
    page = render_workbench(registry, demo=True, search="no matching issue")
    assert "Design load changed" not in page


def test_workbench_candidate_action_promotes_only_to_separate_registry(
    run_fixture, tmp_path
):
    from engineering_registry.desk import apply_action
    from engineering_registry.workbench import render_workbench
    from tests.test_project_run_candidate_stores import candidate_system

    fixture = candidate_system.__wrapped__(run_fixture, tmp_path)
    system, curator, candidate = next(fixture)
    try:
        human = RegistryService(system.registry.store, curator)
        org = RegistryService(system.organization_registry.store, curator)
        page = render_workbench(
            human, demo=True, view="knowledge", organization_configured=True
        )
        assert candidate["proposal"]["validated_statement"] in page
        assert "Validate and promote knowledge" in page
        fields = {
            "project_id": "DEMO",
            "record_id": "CAND",
            "operation": "validate_candidate",
            "digest": candidate["candidate_digest"],
            "accept": "yes",
            "organization_id": "ORG1",
            "organization_lesson_id": "NEW-KNOWLEDGE",
            "rationale": "Reviewed applicability and supporting project outcome",
        }
        with pytest.raises(ValueError, match="separate organisational"):
            apply_action(human, fields, workflow_factory=lambda _: system.brain)
        apply_action(
            human,
            fields,
            workflow_factory=lambda _: system.brain,
            organization_registry=org,
        )
        assert (
            org.get_record("ORG:ORG1", "NEW-KNOWLEDGE")["attributes"]["validated_by"]
            == curator.actor_id
        )
        assert system.registry.store.project_ids() == {"DEMO"}
        with pytest.raises(ValueError, match="already validated"):
            apply_action(
                human,
                fields,
                workflow_factory=lambda _: system.brain,
                organization_registry=org,
            )
    finally:
        fixture.close()


def test_http_workbench_navigation_and_action_context_are_validated(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.cookiejar import CookieJar
    from urllib.parse import urlencode

    from engineering_registry.desk import make_server

    database = tmp_path / "web.sqlite"
    seed_demo(database)
    server, url = make_server(database, {"DEMO"}, "human", demo=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    base = url.split("?")[0]
    try:
        assert "Engineering Workbench" in browser.open(url).read().decode()
        for query in [
            "view=invalid",
            "project=OTHER",
            "view=evidence&view=findings",
            "q=" + "a" * 201,
        ]:
            with pytest.raises(urllib.error.HTTPError) as error:
                browser.open(base + "?" + query)
            assert error.value.code == 400
        assert (
            "Return to Engineering Workbench"
            in browser.open(base + "desk").read().decode()
        )
        with SQLiteGraphStore(database) as store:
            registry = RegistryService(
                store, Principal("human", frozenset({"DEMO"}), "reviewer")
            )
            snapshot = registry.issue_context("DEMO", "issue:F-LOAD")["output_digest"]
        fields = {
            "operation": "transition",
            "project_id": "DEMO",
            "record_id": "issue:F-LOAD",
            "digest": snapshot,
            "status": "open",
            "rationale": "Testing human-controlled review",
            "return_view": "invalid",
        }
        request = urllib.request.Request(
            base + "action",
            urlencode(fields).encode(),
            headers={"Origin": base.rstrip("/")},
        )
        with pytest.raises(urllib.error.HTTPError):
            browser.open(request)
        with SQLiteGraphStore(database) as store:
            assert store.get_issue("DEMO", "issue:F-LOAD").status == "proposed"
        fields["return_view"] = "findings"
        request = urllib.request.Request(
            base + "action",
            urlencode(fields).encode(),
            headers={"Origin": base.rstrip("/")},
        )
        page = browser.open(request).read().decode()
        assert (
            "Engineering Workbench" in page
            and "Recorded with your identity, rationale and the reviewed snapshot"
            in page
        )
        with SQLiteGraphStore(database) as store:
            assert store.get_issue("DEMO", "issue:F-LOAD").status == "open"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
