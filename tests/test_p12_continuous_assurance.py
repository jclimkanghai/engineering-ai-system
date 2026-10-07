from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from evals.p12_continuous_assurance import evaluate_release_gate, validate_snapshot


def _system_manifest_digest(manifest):
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def snapshot(*, run_id="run-1", offset_days=0, **updates):
    value = {
        "schema_version": 2,
        "run_id": run_id,
        "recorded_at": (datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=offset_days)).isoformat(),
        "system_manifest": [{"path": "pipelines/review.py", "sha256": "a" * 64}],
        "system_digest": "",
        "benchmark_id": "synthetic-assurance-benchmark",
        "benchmark_scope": ["case-01", "case-02", "case-03", "case-04", "case-05", "case-06"],
        "affected_benchmarks": ["synthetic-assurance-benchmark"],
        "human_validated_baseline": True,
        "human_validation_ref": "decision:ENG-APPROVAL-01",
        "metrics": {
            "material_recall": 0.95,
            "precision": 0.85,
            "citation_validity": 1.0,
            "unknown_escalation_accuracy": 0.96,
            "classification_severity_accuracy": 0.82,
            "positive_assurance_correctness": 0.92,
            "critical_requirement_misses": 0,
            "unsupported_clear": 0,
            "wrong_governing_revision_errors": 0,
            "cross_project_leaks": 0,
        },
        "evidence_refs": [{"reference": "reports/p7.4.json", "sha256": "b" * 64}],
    }
    value.update(updates)
    value["system_digest"] = _system_manifest_digest(value["system_manifest"])
    metric_basis = {}
    for name, score in value["metrics"].items():
        if score is None:
            numerator = denominator = None
            reason = "No independent human adjudication available"
        elif name in {
            "material_recall",
            "precision",
            "citation_validity",
            "unknown_escalation_accuracy",
            "classification_severity_accuracy",
            "positive_assurance_correctness",
        }:
            denominator = 100
            numerator = round(score * denominator)
            reason = None
        else:
            denominator = 6
            numerator = score
            reason = None
        metric_basis[name] = {
            "numerator": numerator,
            "denominator": denominator,
            "method": "frozen human-marked benchmark scoring",
            "evidence_sha256": "b" * 64,
            "unscored_reason": reason,
        }
    value["metric_basis"] = metric_basis
    return value


def test_compatible_scored_snapshot_passes_and_records_comparison():
    prior = snapshot(run_id="baseline")
    current = snapshot(run_id="run-2", offset_days=1)

    result = evaluate_release_gate(current, prior, ["synthetic-assurance-benchmark"])

    assert result["status"] == "PASS"
    assert result["decision_authority"] == "ADVISORY_ONLY"
    assert result["may_block_deployment"] is False
    assert result["regressions"] == []
    assert result["baseline_run_id"] == "baseline"


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"metrics": {**snapshot()["metrics"], "material_recall": None}}, "HOLD"),
        ({"metrics": {**snapshot()["metrics"], "critical_requirement_misses": 1}}, "FAIL"),
        ({"metrics": {**snapshot()["metrics"], "unsupported_clear": 1}}, "FAIL"),
        ({"metrics": {**snapshot()["metrics"], "precision": 0.79}}, "FAIL"),
        ({"metrics": {**snapshot()["metrics"], "material_recall": 0.90}}, "HOLD"),
    ],
)
def test_unscored_or_failed_targets_never_pass(changes, expected):
    result = evaluate_release_gate(
        snapshot(run_id="current", offset_days=1, **changes),
        snapshot(run_id="baseline"),
        ["synthetic-assurance-benchmark"],
    )
    assert result["status"] == expected


def test_requires_compatible_human_validated_baseline_and_change_coverage():
    current = snapshot(run_id="current", offset_days=1)
    assert evaluate_release_gate(current, None, [current["benchmark_id"]])["status"] == "HOLD"
    assert evaluate_release_gate(
        current, snapshot(run_id="baseline", human_validated_baseline=False), [current["benchmark_id"]]
    )["status"] == "HOLD"
    assert evaluate_release_gate(
        current, snapshot(run_id="baseline", benchmark_scope=["case-01"]), [current["benchmark_id"]]
    )["status"] == "HOLD"
    assert evaluate_release_gate(
        current,
        snapshot(run_id="baseline", metrics={**snapshot()["metrics"], "precision": None}),
        [current["benchmark_id"]],
    )["status"] == "HOLD"
    assert evaluate_release_gate(current, snapshot(run_id="baseline"), ["P9-structural"])["status"] == "HOLD"


def test_snapshot_validation_rejects_bad_digest_rates_and_duplicate_cases():
    stale_manifest_digest = snapshot()
    stale_manifest_digest["system_manifest"][0]["sha256"] = "c" * 64
    malformed_digest = snapshot()
    malformed_digest["system_digest"] = "not-a-sha256"
    unsafe_manifest_path = snapshot(
        system_manifest=[{"path": "../outside.py", "sha256": "a" * 64}]
    )
    for malformed in (
        stale_manifest_digest,
        malformed_digest,
        unsafe_manifest_path,
        snapshot(metrics={**snapshot()["metrics"], "material_recall": 1.2}),
        snapshot(benchmark_scope=["case-01", "case-01"]),
    ):
        with pytest.raises(ValueError):
            validate_snapshot(malformed)


def test_snapshot_validation_requires_metric_denominator_and_matching_source_hash():
    malformed = snapshot()
    malformed["metric_basis"]["material_recall"]["numerator"] = 94
    with pytest.raises(ValueError, match="does not reconcile"):
        validate_snapshot(malformed)

    malformed = snapshot()
    malformed["metric_basis"]["material_recall"]["evidence_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="metric provenance"):
        validate_snapshot(malformed)


def test_cli_reports_all_gate_outcomes_without_enforcing_deployment(tmp_path, monkeypatch):
    from scripts.check_p12_release_assurance import main

    current = snapshot(
        run_id="failed-candidate",
        offset_days=1,
        metrics={**snapshot()["metrics"], "material_recall": 0.70},
    )
    baseline = snapshot(run_id="baseline")
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "gate.json"
    current_path.write_text(json.dumps(current))
    baseline_path.write_text(json.dumps(baseline))
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_p12_release_assurance.py",
            "--current",
            str(current_path),
            "--baseline",
            str(baseline_path),
            "--required-benchmark",
            current["benchmark_id"],
            "--output",
            str(output_path),
        ],
    )
    assert main() == 0
    result = json.loads(output_path.read_text())
    assert result["status"] == "FAIL"
    assert result["decision_authority"] == "ADVISORY_ONLY"
    assert result["may_block_deployment"] is False


def test_current_p74_snapshot_carries_source_hashes_and_keeps_unscored_marks_unknown():
    from scripts.build_p12_assurance_snapshot import build_snapshot
    from scripts.build_p7_4_assurance_report import build_report

    result = build_snapshot(build_report())
    validate_snapshot(result)
    paths = {item["path"] for item in result["system_manifest"]}
    assert "pipelines/llm/adapter.py" in paths
    assert "prompts/master_reviewer.md" in paths
    assert "skills/structural-review/SKILL.md" in paths
    assert result["human_validated_baseline"] is False
    assert result["metrics"]["material_recall"] is None
    assert result["metric_basis"]["material_recall"]["unscored_reason"]
    assert result["metrics"]["citation_validity"] == (
        result["metric_basis"]["citation_validity"]["numerator"]
        / result["metric_basis"]["citation_validity"]["denominator"]
    )


def test_current_snapshot_cannot_precede_baseline_or_be_same_run():
    baseline = snapshot(run_id="baseline")
    assert evaluate_release_gate(baseline, baseline, [baseline["benchmark_id"]])["status"] == "HOLD"
    earlier = snapshot(run_id="current", offset_days=-1)
    assert evaluate_release_gate(earlier, baseline, [baseline["benchmark_id"]])["status"] == "HOLD"
