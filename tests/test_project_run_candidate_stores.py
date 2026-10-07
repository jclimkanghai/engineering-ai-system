"""Candidate validation through the facade's isolated Registry stores."""

import pytest

from engineering_ai_system import EngineeringAISystem
from engineering_execution import ExecutionService
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import GraphStoreError
from tests.support.project_run_harness import alignment_report, integrate
from tests.test_project_run import generalize


@pytest.fixture
def candidate_system(run_fixture, tmp_path):
    database, _, human = run_fixture
    system = EngineeringAISystem.open_project(
        database,
        "DEMO",
        Principal("lead", frozenset({"DEMO"}), organization_ids=frozenset({"ORG1"})),
        alignment_reviewer=alignment_report,
        delegation_policy_id="P1",
        run_integrator=integrate,
        knowledge_generalizer=generalize,
        organization_registry_database=tmp_path / "organization.sqlite3",
        organization_id="ORG1",
        create=True,
    )
    try:
        system.brain.decision_registry = RegistryService(
            system.registry.store,
            Principal("technical-lead-delegate", frozenset({"DEMO"}), "delegate"),
            execution_validator=system.execution.validate_task_freshness,
        )
        runs = system.project_runs
        run = runs.propose(
            "DEMO",
            "RUN",
            "issue:RUN",
            [
                {
                    "task_id": "T1",
                    "tool": "compare_revision",
                    "parameters": {
                        "base_evidence_id": "evidence:EA",
                        "head_evidence_id": "evidence:EB",
                    },
                }
            ],
        )
        runs.review("DEMO", "RUN", "plan")
        ExecutionService(human).authorize_task(
            "DEMO", "T1", run["plan"]["tasks"][0]["task_digest"], "Authorise"
        )
        system.execute_and_assess("DEMO", "T1")
        runs.integrate("DEMO", "RUN")
        runs.review("DEMO", "RUN", "outcome")
        runs.decide(
            human.principal,
            "DEMO",
            "RUN",
            "accept",
            ["evidence:EA", "evidence:EB"],
            "Accept reviewed result",
            lesson_id="LESSON",
            lesson_title="Project lesson",
        )
        runs.propose_candidate("DEMO", "RUN", "CAND")
        candidate = runs.generalize("DEMO", "CAND")
        curator = Principal(
            "curator", frozenset({"DEMO"}), "reviewer", frozenset({"ORG1"})
        )
        yield system, curator, candidate
    finally:
        system.close()


def validate(system, curator, candidate, **changes):
    options = {"organization_id": "ORG1", "organization_lesson_id": "ORG-LESSON"}
    options.update(changes)
    return system.project_runs.validate_candidate(
        curator,
        "DEMO",
        "CAND",
        candidate["candidate_digest"],
        True,
        "Human validation of applicability and limits",
        **options,
    )


def test_candidate_promotion_uses_separate_store_and_deciding_human(candidate_system):
    system, curator, candidate = candidate_system
    validated = validate(system, curator, candidate)
    assert validated["status"] == "validated"
    assert validated["validation"]["organizational_lesson_id"] == "ORG-LESSON"
    assert validated["validation"]["validated_by"] == "curator"
    assert system.registry.store.project_ids() == {"DEMO"}
    assert system.organization_registry.store.project_ids() == {"ORG:ORG1"}
    lesson = system.organization_registry.store.get_node("ORG:ORG1", "ORG-LESSON")
    assert lesson.attributes["validated_by"] == "curator"
    assert (
        lesson.attributes["validated_statement"]
        == candidate["proposal"]["validated_statement"]
    )
    source = system.registry.get_record("DEMO", "LESSON")
    assert lesson.attributes["source_lessons"][0]["snapshot"] == source
    assert lesson.attributes["source_lessons"][0]["lesson_digest"] == digest(source)
    assert system.registry.store.get_node("DEMO", "ORG-LESSON") is None


@pytest.mark.parametrize(
    "principal",
    [
        Principal("agent", frozenset({"DEMO"}), organization_ids=frozenset({"ORG1"})),
        Principal("human", frozenset({"DEMO"}), "reviewer"),
        Principal(
            "other-project", frozenset({"OTHER"}), "reviewer", frozenset({"ORG1"})
        ),
    ],
)
def test_candidate_promotion_preserves_human_and_scope_grants(
    candidate_system, principal
):
    system, _, candidate = candidate_system
    with pytest.raises(PermissionError):
        validate(system, principal, candidate)
    assert (
        system.registry.get_control_record("DEMO", "knowledge_candidates", "CAND")[
            "status"
        ]
        == "proposed"
    )
    assert system.organization_registry.store.get_node("ORG:ORG1", "ORG-LESSON") is None


def test_candidate_rejection_does_not_promote(candidate_system):
    system, curator, candidate = candidate_system
    rejected = system.project_runs.validate_candidate(
        curator, "DEMO", "CAND", candidate["candidate_digest"], False, "Not reusable"
    )
    assert rejected["status"] == "rejected"
    assert system.organization_registry.store.project_ids() == set()


def test_isolated_candidate_requires_configured_organization_store(candidate_system):
    system, curator, candidate = candidate_system
    system.project_runs.organization_registry = None
    with pytest.raises(ValueError, match="configured organisational Registry"):
        validate(system, curator, candidate)
    assert (
        system.registry.get_control_record("DEMO", "knowledge_candidates", "CAND")[
            "status"
        ]
        == "proposed"
    )


def test_candidate_promotion_rejects_wrong_organization_store(candidate_system):
    system, _, candidate = candidate_system
    curator = Principal(
        "curator", frozenset({"DEMO"}), "reviewer", frozenset({"ORG1", "ORG2"})
    )
    with pytest.raises(GraphStoreError, match="bound to ORG:ORG1"):
        validate(system, curator, candidate, organization_id="ORG2")
    assert (
        system.registry.get_control_record("DEMO", "knowledge_candidates", "CAND")[
            "status"
        ]
        == "proposed"
    )
    assert system.organization_registry.store.project_ids() == set()


def test_candidate_promotion_rejects_stale_digest(candidate_system):
    system, curator, candidate = candidate_system
    with pytest.raises(ValueError, match="Candidate missing, changed"):
        validate(system, curator, {**candidate, "candidate_digest": "stale"})
    assert system.organization_registry.store.project_ids() == set()


def test_candidate_promotion_rolls_back_when_candidate_write_fails(
    candidate_system, monkeypatch
):
    system, curator, candidate = candidate_system
    original = system.registry.store.put_record

    def fail_validation(project, namespace, record_id, payload, **kwargs):
        if namespace == "knowledge_candidates" and payload["status"] == "validated":
            raise RuntimeError("candidate write failed")
        return original(project, namespace, record_id, payload, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(system.registry.store, "put_record", fail_validation)
        with pytest.raises(RuntimeError, match="candidate write failed"):
            validate(system, curator, candidate)
    assert (
        system.registry.get_control_record("DEMO", "knowledge_candidates", "CAND")[
            "status"
        ]
        == "proposed"
    )
    assert system.organization_registry.store.get_node("ORG:ORG1", "ORG-LESSON") is None
    assert (
        system.organization_registry.store.get_record(
            "ORG:ORG1", "organizational_lesson_status", "ORG-LESSON"
        )
        is None
    )
    assert validate(system, curator, candidate)["status"] == "validated"
