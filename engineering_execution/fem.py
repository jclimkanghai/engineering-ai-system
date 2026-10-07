"""Candidate declarative FEM contract and OpenSeesPy linear-static adapter.

This module is an evaluation prototype, not an approved production solver.
Callers must run it in a host-controlled isolated worker. Human structural
approval remains mandatory before project use or acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

MAX_MODEL_BYTES = 500_000
MAX_NODES = 500
MAX_ELEMENTS = 1_000
MAX_LOAD_CASES = 50
MAX_ID_LENGTH = 128
MAX_ABS_COORDINATE_M = 1.0e6
MAX_MODULUS_PA = 1.0e13
MAX_SECTION_AREA_M2 = 1.0e5
MAX_SECTION_INERTIA_M4 = 1.0e12
MAX_LOAD_COMPONENT = 1.0e15


class FEMRunError(ValueError):
    """The FEM job was invalid or did not produce a complete valid response."""


def _finite(value: Any, label: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{label} must be a finite number")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number")
    return numeric


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_ID_LENGTH:
        raise ValueError(f"{label} must be a bounded nonblank identifier")
    return value


def _records(value: Any, label: str, minimum: int, maximum: int) -> list[dict]:
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= maximum
        or any(not isinstance(item, dict) for item in value)
    ):
        raise ValueError(f"{label} must be a bounded list of objects")
    return value


def _unique_ids(records: list[dict], key: str, label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in records:
        item_id = _identifier(item.get(key), key)
        if item_id in result:
            raise ValueError(f"Duplicate {label} id: {item_id}")
        result[item_id] = item
    return result


def _vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{label} must contain exactly {length} values")
    return [_finite(component, label) for component in value]


def _cross(left: list[float], right: list[float]) -> list[float]:
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _native_backend_binary() -> tuple[str, str, str]:
    """Return native backend distribution, version and binary SHA-256."""
    candidates: list[tuple[str, str, Path]] = []
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"].lower().replace("-", "")
        if not name.startswith("openseespy") or name == "openseespy":
            continue
        for package_file in distribution.files or ():
            path = Path(str(distribution.locate_file(package_file)))
            if (
                path.is_file()
                and "opensees" in path.name.lower()
                and path.suffix.lower() in {".so", ".dylib", ".pyd", ".dll"}
            ):
                candidates.append(
                    (distribution.metadata["Name"], distribution.version, path)
                )
    if len(candidates) != 1:
        raise FEMRunError("Cannot identify a unique OpenSees native solver binary")
    name, version, path = candidates[0]
    return name, version, hashlib.sha256(path.read_bytes()).hexdigest()


def validate_fem_job(job: dict[str, Any]) -> dict[str, Any]:
    """Validate a bounded SI-only declarative 3D frame/truss job.

    Registry source-ID existence and approval are host responsibilities; this
    validator checks that explicit nonempty source bindings are present.
    """
    required = {
        "schema_version",
        "model_id",
        "units",
        "coordinate_system",
        "analysis_type",
        "nodes",
        "materials",
        "sections",
        "elements",
        "supports",
        "load_cases",
        "source_bindings",
    }
    if not isinstance(job, dict) or set(job) != required:
        raise ValueError("FEM job has missing or unsupported fields")
    try:
        serialized_job = json.dumps(job, allow_nan=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("FEM job must be bounded JSON data") from exc
    if len(serialized_job.encode("utf-8")) > MAX_MODEL_BYTES:
        raise ValueError("FEM job exceeds the model-size limit")
    if job["schema_version"] != 1:
        raise ValueError("Unsupported FEM schema version")
    _identifier(job["model_id"], "model_id")
    if job["units"] != "SI" or job["coordinate_system"] != "right_handed_global_xyz":
        raise ValueError("FEM job must use SI units and right-handed global XYZ")
    if job["analysis_type"] != "linear_static":
        raise ValueError("Only the linear-static profile is supported")

    nodes = _records(job["nodes"], "nodes", 2, MAX_NODES)
    node_map = _unique_ids(nodes, "id", "node")
    for node in nodes:
        if set(node) != {"id", "coordinates_m"}:
            raise ValueError("Invalid node record")
        coordinates = _vector(node["coordinates_m"], 3, "node coordinates")
        if any(abs(value) > MAX_ABS_COORDINATE_M for value in coordinates):
            raise ValueError("Node coordinates exceed the approved numeric bounds")

    materials = _records(job["materials"], "materials", 1, MAX_ELEMENTS)
    material_map = _unique_ids(materials, "id", "material")
    for material in materials:
        if set(material) != {"id", "E_Pa", "G_Pa"}:
            raise ValueError("Invalid material record")
        if any(
            not 0 < _finite(material[field], field) <= MAX_MODULUS_PA
            for field in ("E_Pa", "G_Pa")
        ):
            raise ValueError("Elastic and shear moduli are outside approved bounds")

    sections = _records(job["sections"], "sections", 1, MAX_ELEMENTS)
    section_map = _unique_ids(sections, "id", "section")
    for section in sections:
        if set(section) != {"id", "A_m2", "Iy_m4", "Iz_m4", "J_m4"}:
            raise ValueError("Invalid section record")
        for field in ("A_m2", "Iy_m4", "Iz_m4", "J_m4"):
            value = _finite(section[field], field)
            maximum = MAX_SECTION_AREA_M2 if field == "A_m2" else MAX_SECTION_INERTIA_M4
            if not 0 < value <= maximum:
                raise ValueError("Section properties are outside approved bounds")

    elements = _records(job["elements"], "elements", 1, MAX_ELEMENTS)
    element_map = _unique_ids(elements, "id", "element")
    incident: dict[str, list[str]] = {key: [] for key in node_map}
    for element in elements:
        element_type = element.get("type")
        fields = {
            "id",
            "type",
            "node_i",
            "node_j",
            "material_id",
            "section_id",
        }
        if element_type == "elastic_frame_3d":
            fields.add("orientation_reference")
        elif element_type != "truss_3d":
            raise ValueError("Unsupported FEM element type")
        if set(element) != fields:
            raise ValueError("Invalid element record")
        ni = _identifier(element["node_i"], "node_i")
        nj = _identifier(element["node_j"], "node_j")
        if ni not in node_map or nj not in node_map or ni == nj:
            raise ValueError("Element connectivity references invalid nodes")
        pi = node_map[ni]["coordinates_m"]
        pj = node_map[nj]["coordinates_m"]
        axis = [
            _finite(pj[k], "coordinate") - _finite(pi[k], "coordinate")
            for k in range(3)
        ]
        length = math.sqrt(sum(component * component for component in axis))
        if not 1e-6 <= length <= MAX_ABS_COORDINATE_M:
            raise ValueError("Element length is outside approved numeric bounds")
        material_id = _identifier(element["material_id"], "material_id")
        section_id = _identifier(element["section_id"], "section_id")
        if material_id not in material_map or section_id not in section_map:
            raise ValueError("Element references an unknown material or section")
        if element_type == "elastic_frame_3d":
            reference = _vector(
                element["orientation_reference"], 3, "orientation_reference"
            )
            if (
                any(abs(value) > MAX_ABS_COORDINATE_M for value in reference)
                or math.sqrt(sum(component * component for component in reference))
                <= 1e-9
            ):
                raise ValueError("Frame orientation reference cannot be zero")
            if math.sqrt(sum(v * v for v in _cross(axis, reference))) <= 1e-9 * length:
                raise ValueError(
                    "Frame orientation reference is collinear with the element"
                )
        incident[ni].append(element_type)
        incident[nj].append(element_type)

    if any(not types for types in incident.values()):
        raise ValueError("FEM model contains an unconnected node")

    supports = _records(job["supports"], "supports", len(node_map), MAX_NODES)
    support_map = _unique_ids(supports, "node_id", "support")
    if set(support_map) != set(node_map):
        raise ValueError("Every node requires one explicit support/restraint record")
    for support in supports:
        if set(support) != {"node_id", "fixity"}:
            raise ValueError("Invalid support record")
        fixity = support["fixity"]
        if (
            not isinstance(fixity, list)
            or len(fixity) != 6
            or any(type(value) is not bool for value in fixity)
        ):
            raise ValueError("Support fixity must be six explicit booleans")
        if (
            incident[support["node_id"]]
            and all(item == "truss_3d" for item in incident[support["node_id"]])
            and not all(fixity[3:])
        ):
            raise ValueError("Truss-only nodes require explicit fixed rotational DOFs")

    load_cases = _records(job["load_cases"], "load_cases", 1, MAX_LOAD_CASES)
    case_map = _unique_ids(load_cases, "id", "load case")
    for load_case in load_cases:
        if set(load_case) != {"id", "nodal_loads", "element_loads"}:
            raise ValueError("Invalid load case record")
        nodal_loads = _records(load_case["nodal_loads"], "nodal_loads", 0, MAX_NODES)
        load_nodes: set[str] = set()
        for load in nodal_loads:
            if set(load) != {"node_id", "values_N_Nm"}:
                raise ValueError("Invalid nodal load record")
            node_id = _identifier(load["node_id"], "load node_id")
            if node_id not in node_map or node_id in load_nodes:
                raise ValueError("Nodal load has an unknown or duplicate node")
            load_nodes.add(node_id)
            if any(
                abs(value) > MAX_LOAD_COMPONENT
                for value in _vector(load["values_N_Nm"], 6, "nodal load values")
            ):
                raise ValueError("Nodal loads exceed approved numeric bounds")
        element_loads = _records(
            load_case["element_loads"], "element_loads", 0, MAX_ELEMENTS
        )
        loaded_elements: set[str] = set()
        for load in element_loads:
            if set(load) != {"element_id", "type", "values_N_per_m"}:
                raise ValueError("Invalid element load record")
            element_id = _identifier(load["element_id"], "element load element_id")
            if element_id not in element_map or element_id in loaded_elements:
                raise ValueError("Element load has an unknown or duplicate element")
            loaded_elements.add(element_id)
            if element_map[element_id]["type"] != "elastic_frame_3d":
                raise ValueError(
                    "Uniform beam loads are supported only on 3D frame elements"
                )
            if load["type"] != "uniform_local":
                raise ValueError("Only uniform local-axis beam loads are supported")
            if any(
                abs(value) > MAX_LOAD_COMPONENT
                for value in _vector(load["values_N_per_m"], 3, "uniform load values")
            ):
                raise ValueError("Element loads exceed approved numeric bounds")
        if not nodal_loads and not element_loads:
            raise ValueError(
                "Each linear-static load case requires at least one explicit load"
            )

    bindings = job["source_bindings"]
    required_bindings = {
        "geometry_source_ids",
        "properties_source_ids",
        "supports_source_ids",
        "loads_source_ids",
    }
    if not isinstance(bindings, dict) or set(bindings) != required_bindings:
        raise ValueError("FEM job requires explicit source binding categories")
    for category, ids in bindings.items():
        if not isinstance(ids, list) or not ids or len(ids) > 200:
            raise ValueError(f"{category} must be a bounded nonempty unique list")
        for evidence_id in ids:
            _identifier(evidence_id, category)
        if len(set(ids)) != len(ids):
            raise ValueError(f"{category} must be a bounded nonempty unique list")

    return {
        "node_map": node_map,
        "material_map": material_map,
        "section_map": section_map,
        "element_map": element_map,
        "support_map": support_map,
        "case_map": case_map,
    }


def fem_task_binding(job: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bind the complete FEM input model and its source records to one issue snapshot."""
    validate_fem_job(job)
    records = {
        record.get("node_id"): record
        for record in snapshot.get("records", [])
        if isinstance(record, dict) and record.get("node_type") == "evidence"
    }
    source_ids = sorted(
        {source_id for ids in job["source_bindings"].values() for source_id in ids}
    )
    if not source_ids or any(source_id not in records for source_id in source_ids):
        raise ValueError(
            "Every FEM source binding must be linked evidence in this issue"
        )
    sources = [records[source_id] for source_id in source_ids]
    manifest = [
        {
            "node_id": source["node_id"],
            "revision": source["attributes"].get("revision"),
            "content_digest": source["attributes"].get("content_digest"),
            "source_digest": source["attributes"].get("source_digest"),
        }
        for source in sources
    ]
    model_digest = hashlib.sha256(
        json.dumps(job, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()
    try:
        platform_package, platform_version, native_digest = _native_backend_binary()
        runtime = {
            "name": "OpenSeesPy",
            "package_version": metadata.version("openseespy"),
            "platform_package": platform_package,
            "platform_package_version": platform_version,
            "native_binary_sha256": native_digest,
            "adapter_source_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        }
    except (FEMRunError, metadata.PackageNotFoundError) as exc:
        raise ValueError(
            "Pinned OpenSeesPy runtime is unavailable for FEM planning"
        ) from exc
    if runtime["package_version"] != "3.8.0.0":
        raise ValueError(
            "FEM planning requires the benchmarked OpenSeesPy 3.8.0.0 runtime"
        )
    binding_payload = {
        "model_digest": model_digest,
        "sources": manifest,
        "runtime": runtime,
    }
    return {
        **binding_payload,
        "binding_digest": hashlib.sha256(
            json.dumps(
                binding_payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        ).hexdigest(),
    }


def _equilibrium(job: dict, result: dict, load_case: dict) -> dict[str, float]:
    forces = [0.0, 0.0, 0.0]
    moments = [0.0, 0.0, 0.0]
    for load in load_case["nodal_loads"]:
        coords = job["nodes"]
        point = next(n["coordinates_m"] for n in coords if n["id"] == load["node_id"])
        values = load["values_N_Nm"]
        for k in range(3):
            forces[k] += values[k]
            moments[k] += values[k + 3]
        cross = _cross(point, values[:3])
        for k in range(3):
            moments[k] += cross[k]
    node_map = {node["id"]: node["coordinates_m"] for node in job["nodes"]}
    element_map = {element["id"]: element for element in job["elements"]}
    for load in load_case["element_loads"]:
        element = element_map[load["element_id"]]
        start = node_map[element["node_i"]]
        end = node_map[element["node_j"]]
        axis = [end[k] - start[k] for k in range(3)]
        length = math.sqrt(sum(value * value for value in axis))
        local_x = [value / length for value in axis]
        reference = element["orientation_reference"]
        local_y_raw = _cross(reference, local_x)
        local_y_length = math.sqrt(sum(value * value for value in local_y_raw))
        local_y = [value / local_y_length for value in local_y_raw]
        local_z = _cross(local_x, local_y)
        wx, wy, wz = load["values_N_per_m"]
        resultant = [
            length * (wx * local_x[k] + wy * local_y[k] + wz * local_z[k])
            for k in range(3)
        ]
        midpoint = [(start[k] + end[k]) / 2 for k in range(3)]
        applied_moment = _cross(midpoint, resultant)
        for k in range(3):
            forces[k] += resultant[k]
            moments[k] += applied_moment[k]
    for node_id, response in result["node_results"].items():
        point = next(n["coordinates_m"] for n in job["nodes"] if n["id"] == node_id)
        reaction = response["reaction_N_Nm"]
        for k in range(3):
            forces[k] += reaction[k]
            moments[k] += reaction[k + 3]
        cross = _cross(point, reaction[:3])
        for k in range(3):
            moments[k] += cross[k]
    return {
        "force_residual_N": math.sqrt(sum(v * v for v in forces)),
        "moment_residual_Nm": math.sqrt(sum(v * v for v in moments)),
    }


def run_linear_static_job(job: dict[str, Any]) -> dict[str, Any]:
    """Run a validated linear-static job through OpenSeesPy.

    This prototype runs in-process and is not a hardened production worker.
    Production use requires process isolation, host-verified Registry bindings,
    pinned runtime/build provenance, and a production validation record.
    """
    maps = validate_fem_job(job)
    try:
        import openseespy.opensees as ops  # type: ignore[import-not-found]
    except ImportError as exc:
        raise FEMRunError("OpenSeesPy runtime is not installed") from exc
    platform_package, platform_version, native_digest = _native_backend_binary()

    node_tags = {node_id: tag for tag, node_id in enumerate(maps["node_map"], 1)}
    material_tags = {
        material_id: tag for tag, material_id in enumerate(maps["material_map"], 1)
    }
    element_tags = {
        element_id: tag for tag, element_id in enumerate(maps["element_map"], 1)
    }
    try:
        results: dict[str, Any] = {}
        for case_index, load_case in enumerate(job["load_cases"], 1):
            ops.wipe()
            ops.model("basic", "-ndm", 3, "-ndf", 6)
            for node_id, node in maps["node_map"].items():
                ops.node(node_tags[node_id], *node["coordinates_m"])
            for material_id, material in maps["material_map"].items():
                ops.uniaxialMaterial(
                    "Elastic", material_tags[material_id], material["E_Pa"]
                )
            for support in job["supports"]:
                ops.fix(
                    node_tags[support["node_id"]],
                    *[int(flag) for flag in support["fixity"]],
                )
            for element_id, element in maps["element_map"].items():
                tag = element_tags[element_id]
                ni = node_tags[element["node_i"]]
                nj = node_tags[element["node_j"]]
                material = maps["material_map"][element["material_id"]]
                section = maps["section_map"][element["section_id"]]
                if element["type"] == "truss_3d":
                    ops.element(
                        "Truss",
                        tag,
                        ni,
                        nj,
                        section["A_m2"],
                        material_tags[element["material_id"]],
                    )
                else:
                    transf_tag = tag
                    ops.geomTransf(
                        "Linear", transf_tag, *element["orientation_reference"]
                    )
                    ops.element(
                        "elasticBeamColumn",
                        tag,
                        ni,
                        nj,
                        section["A_m2"],
                        material["E_Pa"],
                        material["G_Pa"],
                        section["J_m4"],
                        section["Iy_m4"],
                        section["Iz_m4"],
                        transf_tag,
                    )
            ops.timeSeries("Linear", case_index)
            ops.pattern("Plain", case_index, case_index)
            for load in load_case["nodal_loads"]:
                ops.load(node_tags[load["node_id"]], *load["values_N_Nm"])
            for load in load_case["element_loads"]:
                ops.eleLoad(
                    "-ele",
                    element_tags[load["element_id"]],
                    "-type",
                    "-beamUniform",
                    load["values_N_per_m"][1],
                    load["values_N_per_m"][2],
                    load["values_N_per_m"][0],
                )
            ops.system("BandGeneral")
            ops.numberer("Plain")
            ops.constraints("Plain")
            ops.integrator("LoadControl", 1.0)
            ops.algorithm("Linear")
            ops.analysis("Static")
            status = ops.analyze(1)
            if status != 0:
                raise FEMRunError(
                    f"OpenSeesPy failed load case {load_case['id']} with status {status}"
                )
            node_results: dict[str, Any] = {}
            for node_id in maps["node_map"]:
                displacement = [
                    ops.nodeDisp(node_tags[node_id], dof) for dof in range(1, 7)
                ]
                if any(not math.isfinite(value) for value in displacement):
                    raise FEMRunError(
                        "OpenSeesPy returned non-finite nodal displacement"
                    )
                node_results[node_id] = {
                    "displacement_m": displacement[:3],
                    "rotation_rad": displacement[3:],
                    "reaction_N_Nm": [0.0] * 6,
                }
            ops.reactions()
            for node_id in maps["node_map"]:
                node_results[node_id]["reaction_N_Nm"] = [
                    ops.nodeReaction(node_tags[node_id], dof) for dof in range(1, 7)
                ]
            element_results = {
                element_id: list(
                    ops.eleResponse(element_tags[element_id], "globalForce")
                )
                for element_id in maps["element_map"]
            }
            if any(
                len(values) != 12 or any(not math.isfinite(v) for v in values)
                for values in element_results.values()
            ):
                raise FEMRunError(
                    "OpenSeesPy returned malformed or non-finite element forces"
                )
            case_result = {
                "node_results": node_results,
                "element_global_end_forces_N_Nm": element_results,
            }
            equilibrium = _equilibrium(job, case_result, load_case)
            if (
                equilibrium["force_residual_N"] > 1e-6
                or equilibrium["moment_residual_Nm"] > 1e-6
            ):
                raise FEMRunError(
                    "Global force/moment equilibrium residual exceeds 1e-6 SI"
                )
            results[load_case["id"]] = {**case_result, "equilibrium": equilibrium}
        output = {
            "model_id": job["model_id"],
            "results": results,
            "solver": {
                "name": "OpenSeesPy",
                "package_version": metadata.version("openseespy"),
                "platform_package": platform_package,
                "platform_package_version": platform_version,
                "engine_version": str(ops.version()),
                "native_binary_sha256": native_digest,
                "adapter_source_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "status": 0,
                "diagnostics_capture": "in_process_output_not_captured",
            },
            "model_digest": hashlib.sha256(
                json.dumps(
                    job,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
            "source_bindings": job["source_bindings"],
            "warnings": [
                "Prototype does not capture OpenSees stdout/stderr; production execution must use an isolated diagnostic-capturing worker."
            ],
            "human_engineering_approval_required": True,
            "production_approved": False,
        }
        output["output_digest"] = hashlib.sha256(
            json.dumps(
                output,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return output
    except FEMRunError:
        raise
    except Exception as exc:
        raise FEMRunError(
            f"OpenSeesPy execution/result extraction failed: {type(exc).__name__}"
        ) from exc
    finally:
        ops.wipe()


def run_linear_static_job_isolated(
    job: dict[str, Any], *, timeout_seconds: int = 120
) -> dict[str, Any]:
    """Run a FEM job in a bounded child process and retain solver diagnostics."""
    validate_fem_job(job)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("FEM worker timeout must be between 1 and 300 seconds")
    request = json.dumps(job, separators=(",", ":"), allow_nan=False)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "engineering_execution.fem_worker"],
            input=request,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
            env={"PATH": str(Path(sys.executable).parent), "PYTHONNOUSERSITE": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise FEMRunError("OpenSeesPy worker exceeded its time limit") from exc
    if (
        len(completed.stdout.encode("utf-8")) > 2_000_000
        or len(completed.stderr.encode("utf-8")) > 256_000
    ):
        raise FEMRunError("OpenSeesPy worker diagnostics exceeded output limits")
    try:
        response = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        diagnostic = completed.stderr[:2_000].strip()
        raise FEMRunError(
            "OpenSeesPy worker returned malformed JSON"
            + (f"; worker stderr: {diagnostic}" if diagnostic else "")
        ) from exc
    if (
        completed.returncode != 0
        or not isinstance(response, dict)
        or response.get("ok") is not True
    ):
        message = (
            response.get("error", "worker failed")
            if isinstance(response, dict)
            else "worker failed"
        )
        diagnostic = (
            response.get("diagnostics", "") if isinstance(response, dict) else ""
        )
        diagnostic = (diagnostic + completed.stderr)[:2_000].strip()
        raise FEMRunError(
            f"OpenSeesPy worker failed: {message}"
            + (f"; {diagnostic}" if diagnostic else "")
        )
    output = response.get("result")
    if not isinstance(output, dict):
        raise FEMRunError("OpenSeesPy worker omitted its result")
    diagnostics = (str(response.get("diagnostics", "")) + completed.stderr)[:16_000]
    output["solver"]["diagnostics_capture"] = "isolated_subprocess"
    output["solver"]["diagnostics"] = diagnostics[:16_000]
    output["warnings"] = [
        warning
        for warning in output.get("warnings", [])
        if "does not capture OpenSees stdout/stderr" not in warning
    ]
    output["warnings"].append(
        "Isolated worker execution does not constitute independent engineering validation or production approval."
    )
    output.pop("output_digest", None)
    output["output_digest"] = hashlib.sha256(
        json.dumps(
            output, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    return output
