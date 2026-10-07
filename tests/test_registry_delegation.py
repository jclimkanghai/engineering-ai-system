"""Delegation cannot turn a model's own classification into approval authority."""

import pytest

from engineering_registry.service import Principal, RegistryService
from engineering_registry.store import SQLiteGraphStore
from tests.support.delegation_harness import (
    mandate_definition,
    policy_definition,
)


def test_agent_cannot_define_client_intent_or_grant_delegation(services):
    _, agent, _ = services
    assert hasattr(agent, "record_client_mandate"), (
        "Controlled client mandate API is missing"
    )
    with pytest.raises(PermissionError):
        agent.record_client_mandate("DEMO", "M1", "issue:F-LOAD", mandate_definition())
    with pytest.raises(PermissionError):
        agent.approve_delegation_policy("DEMO", "P1", policy_definition())


def test_client_mandate_is_immutable_and_survives_restart(services):
    database, agent, human = services
    assert hasattr(human, "record_client_mandate"), (
        "Controlled client mandate API is missing"
    )
    first = human.record_client_mandate(
        "DEMO", "M1", "issue:F-LOAD", mandate_definition()
    )
    assert first["approved_by"] == "human"
    assert agent.client_mandate("DEMO", "issue:F-LOAD") == first
    assert (
        human.record_client_mandate("DEMO", "M1", "issue:F-LOAD", mandate_definition())
        == first
    )
    with pytest.raises(ValueError, match="immutable"):
        human.record_client_mandate(
            "DEMO",
            "M1",
            "issue:F-LOAD",
            mandate_definition(objective="Different intent"),
        )
    with SQLiteGraphStore(database) as reopened:
        reader = RegistryService(
            reopened, Principal("reader", frozenset({"DEMO"}), "reader")
        )
        assert (
            reader.client_mandate("DEMO", "issue:F-LOAD")["definition"]["objective"]
            == mandate_definition()["objective"]
        )


def test_policy_revocation_is_attributed_and_cannot_be_undone_by_replay(services):
    _, agent, human = services
    assert hasattr(human, "approve_delegation_policy"), (
        "Delegation policy API is missing"
    )
    policy = human.approve_delegation_policy("DEMO", "P1", policy_definition())
    assert agent.delegation_policy("DEMO", "P1")["active"] is True
    human.revoke_delegation_policy("DEMO", "P1", "Suspend automatic decisions")
    assert agent.delegation_policy("DEMO", "P1")["active"] is False
    assert human.approve_delegation_policy("DEMO", "P1", policy_definition()) == policy
    assert agent.delegation_policy("DEMO", "P1")["active"] is False
    with pytest.raises(PermissionError):
        agent.revoke_delegation_policy("DEMO", "P1", "Override")


@pytest.mark.parametrize(
    "change",
    [
        {"risk_level": "invented"},
        {"importance_level": "invented"},
        {"simple": "yes"},
        {"reversible": 1},
        {"evidence_ids": ["evidence:foreign"]},
        {"acceptance_criteria": []},
        {"objective": " "},
        {"allowed_tools": ["shell"]},
    ],
)
def test_invalid_mandate_does_not_create_active_authority(services, change):
    _, agent, human = services
    assert hasattr(human, "record_client_mandate"), (
        "Controlled client mandate API is missing"
    )
    with pytest.raises(ValueError):
        human.record_client_mandate(
            "DEMO", "bad", "issue:F-LOAD", mandate_definition(**change)
        )
    assert agent.client_mandate("DEMO", "issue:F-LOAD") is None


def test_changed_issue_sources_make_client_mandate_stale(services):
    _, agent, human = services
    assert hasattr(human, "record_client_mandate"), (
        "Controlled client mandate API is missing"
    )
    human.record_client_mandate("DEMO", "M1", "issue:F-LOAD", mandate_definition())
    proposal = agent.propose_transition(
        "DEMO", "issue:F-LOAD", "open", "Change lifecycle"
    )
    human.review_proposal(
        "DEMO",
        proposal["proposal_id"],
        proposal["proposal_digest"],
        accept=True,
        rationale="Reviewed",
    )
    with pytest.raises(ValueError, match="stale"):
        agent.client_mandate("DEMO", "issue:F-LOAD")


def test_policy_and_mandate_are_project_scoped(services):
    _, _, human = services
    assert hasattr(human, "record_client_mandate"), (
        "Controlled client mandate API is missing"
    )
    with pytest.raises(PermissionError):
        human.record_client_mandate(
            "FOREIGN", "M1", "issue:F-LOAD", mandate_definition()
        )
    with pytest.raises(PermissionError):
        human.approve_delegation_policy("FOREIGN", "P1", policy_definition())
