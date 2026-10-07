"""Host-owned external skill/solver adapters with explicit numeric contracts.

Registration runs supplied benchmark cases. This validates the adapter against
those cases; it does not establish adequacy for every engineering application.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from typing import Any

from engineering_registry.delegation import string_list
from engineering_registry.service import digest, text

SolverCallback = Callable[[dict, dict], dict]


def _production_validation_record(record: dict) -> dict:
    fields = {
        "discipline",
        "governing_standard",
        "governing_edition",
        "approval_record_id",
        "approved_by",
        "approval_date",
        "benchmark_source_digest",
        "benchmark_independent",
        "limitations",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("Production solver requires a complete validation record")
    for field in (
        "discipline",
        "governing_standard",
        "governing_edition",
        "approval_record_id",
        "approved_by",
    ):
        text(record[field], field)
    try:
        date.fromisoformat(record["approval_date"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Validation record approval_date must be ISO date") from exc
    if not isinstance(record["benchmark_source_digest"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", record["benchmark_source_digest"]
    ):
        raise ValueError("Validation record requires benchmark source digest")
    if record["benchmark_independent"] is not True:
        raise ValueError("Production validation requires independent benchmarks")
    string_list(record["limitations"], "limitations", nonempty=True)
    return deepcopy(record)


def finite_number(value: Any, field: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a finite number, not a boolean")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be finite")
    return numeric


def quantities_valid(quantities: dict, specification: dict) -> None:
    if not isinstance(quantities, dict) or set(quantities) != set(specification):
        raise ValueError(
            "Solver quantities must match the explicit input/output contract"
        )
    for name, spec in specification.items():
        quantity = quantities[name]
        if (
            not isinstance(quantity, dict)
            or set(quantity) != {"value", "unit"}
            or quantity["unit"] != spec["unit"]
        ):
            raise ValueError("Solver quantity units do not match the approved method")
        value = finite_number(quantity["value"], name)
        if not spec["minimum"] <= value <= spec["maximum"]:
            raise ValueError("Solver quantity is outside the method's validated bounds")


def output_valid(output: dict, specification: dict) -> None:
    if not isinstance(output, dict) or set(output) != {"values", "warnings"}:
        raise ValueError("Invalid external solver output contract")
    quantities_valid(output["values"], specification)
    string_list(output["warnings"], "warnings")


class SolverCatalog:
    """Injected by the trusted host; tasks never load skills or select providers."""

    def __init__(self) -> None:
        self._adapters: dict[str, tuple[dict, SolverCallback]] = {}

    def register(
        self,
        manifest: dict,
        callback: SolverCallback,
        *,
        validation_record: dict | None = None,
    ) -> dict:
        fields = {
            "solver_id",
            "version",
            "skill",
            "method",
            "applicability_limits",
            "assumptions",
            "inputs",
            "outputs",
            "validation_cases",
        }
        if (
            not isinstance(manifest, dict)
            or set(manifest) != fields
            or not callable(callback)
        ):
            raise ValueError("Invalid external solver manifest or callback")
        for field in ("solver_id", "version", "method"):
            text(manifest[field], field)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", manifest["solver_id"]):
            raise ValueError("Solver identifier must be a bounded catalogue key")
        skill = manifest["skill"]
        if not isinstance(skill, dict) or set(skill) != {
            "name",
            "version",
            "artifact_digest",
        }:
            raise ValueError(
                "Solver skill requires identity, version and artifact digest"
            )
        for field in ("name", "version"):
            text(skill[field], field)
        if not isinstance(skill["artifact_digest"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", skill["artifact_digest"]
        ):
            raise ValueError("Invalid skill artifact digest")
        for field in ("applicability_limits", "assumptions"):
            string_list(manifest[field], field, nonempty=True)
        for field in ("inputs", "outputs"):
            specs = manifest[field]
            if not isinstance(specs, dict) or not 1 <= len(specs) <= 50:
                raise ValueError("Solver quantity specification must be bounded")
            for name, spec in specs.items():
                text(name, "quantity_name")
                if not isinstance(spec, dict) or set(spec) != {
                    "unit",
                    "minimum",
                    "maximum",
                }:
                    raise ValueError(
                        "Solver quantity requires units and explicit bounds"
                    )
                text(spec["unit"], "unit")
                lower, upper = (
                    finite_number(spec[k], k) for k in ("minimum", "maximum")
                )
                if lower > upper:
                    raise ValueError("Solver quantity bounds are reversed")
        cases = manifest["validation_cases"]
        if not isinstance(cases, list) or not 1 <= len(cases) <= 50:
            raise ValueError("Solver requires bounded benchmark cases")
        if len(str(manifest)) > 100_000:
            raise ValueError("Solver manifest exceeds limit")
        frozen = deepcopy(manifest)
        for case in cases:
            if not isinstance(case, dict) or set(case) != {
                "inputs",
                "expected_outputs",
                "relative_tolerance",
                "absolute_tolerance",
            }:
                raise ValueError("Invalid solver benchmark case")
            quantities_valid(case["inputs"], manifest["inputs"])
            quantities_valid(case["expected_outputs"], manifest["outputs"])
            for field in ("relative_tolerance", "absolute_tolerance"):
                if finite_number(case[field], field) < 0:
                    raise ValueError("Solver benchmark tolerances must be nonnegative")
        if frozen["solver_id"] in self._adapters:
            raise ValueError(
                "Solver registrations are immutable; configure a new catalogue"
            )
        results = []
        for index, case in enumerate(frozen["validation_cases"]):
            output = callback(
                deepcopy(case["inputs"]),
                {"validation_case": index, "synthetic_validation": True},
            )
            output_valid(output, frozen["outputs"])
            for name, expected in case["expected_outputs"].items():
                if not math.isclose(
                    output["values"][name]["value"],
                    expected["value"],
                    rel_tol=case["relative_tolerance"],
                    abs_tol=case["absolute_tolerance"],
                ):
                    raise ValueError("External solver failed its benchmark")
            if output["warnings"]:
                raise ValueError(
                    "External solver benchmark returned unresolved warnings"
                )
            results.append(deepcopy(output))
        production_record = (
            _production_validation_record(validation_record)
            if validation_record is not None
            else None
        )
        binding: dict[str, Any] = {
            "definition": frozen,
            "validation": {
                "passed": True,
                "scope": (
                    "production_method_validation"
                    if production_record is not None
                    else "adapter_conformance_only"
                ),
                "production_validated": production_record is not None,
                "validation_record": production_record,
                "case_count": len(results),
                "outputs": results,
                "cases_digest": digest(frozen["validation_cases"]),
            },
        }
        binding["adapter_digest"] = digest(binding)
        self._adapters[frozen["solver_id"]] = (binding, callback)
        return deepcopy(binding)

    def binding(self, solver_id: str) -> dict:
        if solver_id not in self._adapters:
            raise ValueError("External solver is not in the host's validated catalogue")
        return deepcopy(self._adapters[solver_id][0])

    def validate_inputs(self, parameters: dict, snapshot: dict) -> dict:
        binding = self.binding(parameters["solver_id"])
        quantities = parameters["quantities"]
        quantities_valid(quantities, binding["definition"]["inputs"])
        sources = parameters["source_evidence_ids"]
        if not isinstance(sources, dict) or set(sources) != set(quantities):
            raise ValueError("Solver inputs require explicit source evidence bindings")
        evidence = {
            n["node_id"]: n for n in snapshot["records"] if n["node_type"] == "evidence"
        }
        for name, source in sources.items():
            if not isinstance(source, str) or source not in evidence:
                raise ValueError("Solver source must be same-project issue evidence")
            if (
                evidence[source]["attributes"].get("quantities", {}).get(name)
                != quantities[name]
            ):
                raise ValueError(
                    "Solver quantity does not match its retained source evidence"
                )
        return binding

    def execute(self, parameters: dict, snapshot: dict) -> dict:
        binding = self.validate_inputs(parameters, snapshot)
        _, callback = self._adapters[parameters["solver_id"]]
        output = callback(deepcopy(parameters["quantities"]), deepcopy(snapshot))
        output_valid(output, binding["definition"]["outputs"])
        return {
            "values": deepcopy(output["values"]),
            "solver": binding,
            "input_quantities": deepcopy(parameters["quantities"]),
            "source_evidence_ids": deepcopy(parameters["source_evidence_ids"]),
            "warnings": [
                *output["warnings"],
                "Benchmark validation is limited to the recorded cases; applicability and engineering acceptance require assessment.",
            ],
            "solver_warnings": deepcopy(output["warnings"]),
        }

    def check_retained_output(self, task: dict, output: dict) -> bool:
        binding = self.validate_inputs(task["parameters"], task["input_snapshot"])
        if (
            not isinstance(output, dict)
            or set(output)
            != {
                "values",
                "solver",
                "input_quantities",
                "source_evidence_ids",
                "warnings",
                "solver_warnings",
            }
            or binding != task.get("solver_binding")
            or output["solver"] != binding
            or output["input_quantities"] != task["parameters"]["quantities"]
            or output["source_evidence_ids"]
            != task["parameters"]["source_evidence_ids"]
        ):
            return False
        output_valid(
            {"values": output["values"], "warnings": output["solver_warnings"]},
            binding["definition"]["outputs"],
        )
        return not output["solver_warnings"]
