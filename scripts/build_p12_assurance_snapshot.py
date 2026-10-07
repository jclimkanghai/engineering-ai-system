"""Translate the retained P7.4 report into an honest P12 monitor snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.p12_continuous_assurance import (  # noqa: E402
    RATE_TARGETS,
    REQUIRED_METRICS,
    evaluate_release_gate,
    validate_snapshot,
)
from scripts.build_p7_4_assurance_report import build_report  # noqa: E402

SYSTEM_ROOTS = (
    "agents",
    ".github/workflows",
    "config",
    "engineering_ai_reviewer",
    "engineering_ai_system",
    "engineering_document_ai_brain",
    "engineering_execution",
    "engineering_registry",
    "evals",
    "pipelines",
    "prompts",
    "schemas",
    "scripts",
    "skills",
    "tests",
    "docs/implementation",
    "README.md",
    "pyproject.toml",
)
FINGERPRINT_SUFFIXES = {".md", ".py", ".json", ".toml", ".yaml", ".yml", ".txt", ".sh"}


def build_system_manifest() -> list[dict[str, str]]:
    paths: set[Path] = set()
    for root in SYSTEM_ROOTS:
        base = ROOT / root
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            if (
                path.is_file()
                and not path.is_symlink()
                and path.name != ".DS_Store"
                and path.suffix.lower() in FINGERPRINT_SUFFIXES
            ):
                paths.add(path)
    return [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix())
    ]


def manifest_digest(manifest: list[dict[str, str]]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    benchmark_id = report["benchmark_id"]
    evidence_check = report["candidate_output_evidence_checks"]
    citation_count = int(evidence_check["cited_evidence_ids"])
    valid_citation_count = int(evidence_check["valid_citations"])
    # The retained report's citation rate is only deterministic ID membership;
    # it does not establish substantive claim-to-source support.
    citation_rate = valid_citation_count / citation_count if citation_count else None
    metrics: dict[str, int | float | None] = {
        "material_recall": None,
        "precision": None,
        "citation_validity": citation_rate,
        "unknown_escalation_accuracy": None,
        "classification_severity_accuracy": None,
        "positive_assurance_correctness": None,
        "critical_requirement_misses": None,
        "unsupported_clear": None,
        "wrong_governing_revision_errors": None,
        "cross_project_leaks": None,
    }
    report_digest = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    metric_basis = {}
    for name in REQUIRED_METRICS:
        value = metrics[name]
        if value is None:
            numerator = denominator = None
            reason = "P7.4 report has no independently adjudicated value for this dimension"
        elif name in RATE_TARGETS:
            numerator, denominator, reason = valid_citation_count, citation_count, None
        else:
            numerator, denominator, reason = value, len(report["case_ids"]), None
        metric_basis[name] = {
            "numerator": numerator,
            "denominator": denominator,
            "method": (
                "P7.4 deterministic evidence-ID membership count; does not test substantive citation support"
                if name == "citation_validity"
                else "Unscored from retained P7.4 adjudication" if value is None else "P7.4 adjudication"
            ),
            "evidence_sha256": report_digest,
            "unscored_reason": reason,
        }
    system_files = build_system_manifest()
    snapshot = {
        "schema_version": 2,
        "run_id": f"p12-monitor-{report['report_date']}",
        "recorded_at": datetime.now(UTC).isoformat(),
        "system_manifest": system_files,
        "system_digest": manifest_digest(system_files),
        "benchmark_id": benchmark_id,
        "benchmark_scope": list(report["case_ids"]),
        "affected_benchmarks": [benchmark_id],
        "human_validated_baseline": False,
        "human_validation_ref": None,
        "metrics": metrics,
        "metric_basis": metric_basis,
        "evidence_refs": [
            {
                "reference": "reports/p7_4_assurance_summary.json (canonical build_report JSON)",
                "sha256": report_digest,
            }
        ],
    }
    validate_snapshot(snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-output", type=Path, required=True)
    parser.add_argument("--gate-output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    snapshot = build_snapshot(build_report())
    baseline = None
    if args.baseline:
        baseline_value = json.loads(args.baseline.read_text(encoding="utf-8"))
        baseline = baseline_value.get("snapshot", baseline_value)
    gate = evaluate_release_gate(snapshot, baseline, [snapshot["benchmark_id"]])
    args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
    args.gate_output.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot_output.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.gate_output.write_text(
        json.dumps(gate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Advisory P12 assurance result: {gate['status']} (does not block deployment)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
