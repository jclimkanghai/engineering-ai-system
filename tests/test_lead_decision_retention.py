"""Lead steps become graph decisions only when project impact is possible."""

import pytest

from engineering_registry.service import IMPACT_DOMAINS
from tests.support.registry_decision_harness import setup_registry


def flags(value="no"):
    return dict.fromkeys(IMPACT_DOMAINS, value)


@pytest.mark.parametrize("domain", sorted(IMPACT_DOMAINS))
def test_each_material_domain_routes_to_registry(tmp_path, domain):
    store, agent, _ = setup_registry(tmp_path)
    try:
        impact = flags()
        impact[domain] = "yes"
        step = agent.record_lead_step(
            "P1",
            "I-P1",
            "N-" + domain,
            "Assess project effect",
            "Current source may affect design",
            impact,
            evidence_ids=["E-P1"],
        )
        assert step["route"] == "registry_decision"
        decision = agent.get_record("P1", "decision:lead:N-" + domain)
        assert decision["attributes"]["impact_flags"] == impact
        assert decision["attributes"]["status"] == "proposed"
    finally:
        store.close()


def test_unknown_impact_is_retained_as_proposal(tmp_path):
    store, agent, _ = setup_registry(tmp_path)
    try:
        impact = flags()
        impact["design_basis"] = "unknown"
        step = agent.record_lead_step(
            "P1", "I-P1", "N-UNKNOWN", "Basis uncertain", "Requires review", impact
        )
        assert step["route"] == "registry_decision"
        assert (
            agent.get_record("P1", step["decision_id"])["attributes"]["impact_flags"]
            == impact
        )
    finally:
        store.close()


def test_all_no_uses_execution_log_without_graph_decision(tmp_path):
    store, agent, _ = setup_registry(tmp_path)
    try:
        step = agent.record_lead_step(
            "P1", "I-P1", "N-MICRO", "Format table", "Presentation only", flags()
        )
        assert step["route"] == "execution_log"
        assert step["actor"] == "brain"
        assert agent.store.get_record("P1", "execution_logs", "N-MICRO") == step
        assert agent.list_records("P1", "decision") == []
    finally:
        store.close()


def test_invalid_impact_flags_are_rejected(tmp_path):
    store, agent, _ = setup_registry(tmp_path)
    try:
        for impact in ({"scope": "no"}, {**flags(), "scope": "maybe"}):
            with pytest.raises(ValueError, match="impact"):
                agent.record_lead_step(
                    "P1", "I-P1", "N-BAD", "Choice", "Reason", impact
                )
    finally:
        store.close()


def test_later_promotion_keeps_execution_log_reference(tmp_path):
    store, agent, _ = setup_registry(tmp_path)
    try:
        agent.record_lead_step(
            "P1",
            "I-P1",
            "N-PROMOTE",
            "Initial choice",
            "Presentation only at the time",
            flags(),
        )
        impact = flags()
        impact["technical_solution"] = "yes"
        promoted = agent.record_lead_step(
            "P1",
            "I-P1",
            "N-PROMOTE-2",
            "Choice now affects solution",
            "New evidence",
            impact,
            evidence_ids=["E-P1"],
            source_log_id="N-PROMOTE",
        )
        decision = agent.get_record("P1", promoted["decision_id"])
        assert decision["attributes"]["source_log_id"] == "N-PROMOTE"
        assert agent.store.get_record("P1", "execution_logs", "N-PROMOTE")
    finally:
        store.close()
