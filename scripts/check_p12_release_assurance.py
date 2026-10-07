"""Evaluate two P12 assurance snapshots and emit a release-gate JSON record."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.p12_continuous_assurance import evaluate_release_gate  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--required-benchmark",
        action="append",
        required=True,
        help="Benchmark declared affected by the change; repeat for each affected suite.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate_release_gate(
        load_json(args.current),
        load_json(args.baseline) if args.baseline else None,
        args.required_benchmark,
    )
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    # A gate record is advisory until metric and human-approval provenance are
    # independently authenticated. Status is data for a human/release operator.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
