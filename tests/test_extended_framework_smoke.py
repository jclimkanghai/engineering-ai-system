"""Installed-style synthetic artifact proves both extended public workflows."""

import importlib.util
import json


def test_extended_smoke_retains_ai_decision_alignment_and_solver_provenance(tmp_path):
    assert importlib.util.find_spec("engineering_registry.extended_demo"), (
        "Extended runnable workflow is missing"
    )
    from engineering_registry.extended_demo import run_extended_smoke

    result = run_extended_smoke(tmp_path)
    assert result["synthetic_only"] is True and result["models_called"] is False
    assert result["delegated_disposition"] == "accept"
    assert result["authority"] == "ai_delegated"
    assert result["human_verification_count"] == 0
    assert result["alignment_status"] == "aligned"
    assert result["stress"] == {"value": 2000000, "unit": "Pa"}
    assert result["solver_benchmark_count"] == 2
    retained = json.loads((tmp_path / "extended-report.json").read_text())
    assert (
        retained["delegated_decision"]["attributes"]["policy_snapshot"]["approved_by"]
        == "synthetic-test-reviewer"
    )
    assert retained["solver_result"]["attributes"]["outputs"]["solver"]["definition"][
        "skill"
    ]["artifact_digest"]
