"""Contract and candidate-runtime checks for declarative 3D linear FEM jobs."""

from copy import deepcopy

import pytest

from engineering_execution.fem import (
    FEMRunError,
    fem_task_binding,
    run_linear_static_job,
    run_linear_static_job_isolated,
    validate_fem_job,
)


def cantilever_job():
    return {
        "schema_version": 1,
        "model_id": "synthetic-cantilever",
        "units": "SI",
        "coordinate_system": "right_handed_global_xyz",
        "analysis_type": "linear_static",
        "nodes": [
            {"id": "n1", "coordinates_m": [0.0, 0.0, 0.0]},
            {"id": "n2", "coordinates_m": [3.0, 0.0, 0.0]},
        ],
        "materials": [
            {"id": "mat1", "E_Pa": 200e9, "G_Pa": 80e9},
        ],
        "sections": [
            {
                "id": "sec1",
                "A_m2": 0.02,
                "Iy_m4": 2e-4,
                "Iz_m4": 1e-4,
                "J_m4": 1e-5,
            },
        ],
        "elements": [
            {
                "id": "e1",
                "type": "elastic_frame_3d",
                "node_i": "n1",
                "node_j": "n2",
                "material_id": "mat1",
                "section_id": "sec1",
                "orientation_reference": [0.0, 1.0, 0.0],
            },
        ],
        "supports": [
            {"node_id": "n1", "fixity": [True, True, True, True, True, True]},
            {"node_id": "n2", "fixity": [False, False, False, False, False, False]},
        ],
        "load_cases": [
            {
                "id": "lc1",
                "nodal_loads": [
                    {"node_id": "n2", "values_N_Nm": [0.0, 0.0, -1000.0, 0.0, 0.0, 0.0]}
                ],
                "element_loads": [],
            },
        ],
        "source_bindings": {
            "geometry_source_ids": ["synthetic:geometry"],
            "properties_source_ids": ["synthetic:properties"],
            "supports_source_ids": ["synthetic:supports"],
            "loads_source_ids": ["synthetic:loads"],
        },
    }


@pytest.fixture
def opensees_runtime():
    pytest.importorskip("openseespy.opensees")


def test_valid_declarative_job_runs_and_matches_independent_cantilever_solution(
    opensees_runtime,
):
    result = run_linear_static_job(cantilever_job())
    # Independent Euler-Bernoulli reference: PL^3/(3EI), PL^2/(2EI).
    assert result["solver"]["status"] == 0
    response = result["results"]["lc1"]
    assert response["node_results"]["n2"]["displacement_m"][2] == pytest.approx(
        -1000 * 3**3 / (3 * 200e9 * 1e-4), rel=1e-9
    )
    assert response["node_results"]["n2"]["rotation_rad"][1] == pytest.approx(
        1000 * 3**2 / (2 * 200e9 * 1e-4), rel=1e-9
    )
    assert response["equilibrium"]["force_residual_N"] < 1e-8
    assert response["equilibrium"]["moment_residual_Nm"] < 1e-8
    assert len(result["solver"]["native_binary_sha256"]) == 64
    assert len(result["model_digest"]) == 64
    assert len(result["output_digest"]) == 64
    assert result["human_engineering_approval_required"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda job: job.update(units="US_customary"),
        lambda job: job["nodes"].append(deepcopy(job["nodes"][0])),
        lambda job: job["elements"][0].update(node_j="missing"),
        lambda job: job["elements"][0].update(type="nonlinear_shell"),
        lambda job: job["elements"][0].update(orientation_reference=[3.0, 0.0, 0.0]),
        lambda job: job["supports"][0].update(fixity=[1, True, True, True, True, True]),
        lambda job: job["materials"][0].update(E_Pa=float("inf")),
        lambda job: job["source_bindings"].update(loads_source_ids=[]),
        lambda job: job["source_bindings"].update(loads_source_ids=[["bad"]]),
        lambda job: job["nodes"][1]["coordinates_m"].__setitem__(0, 1e99),
        lambda job: job.update(executable_script="import os; ..."),
        lambda job: job["load_cases"].append(deepcopy(job["load_cases"][0])),
    ],
)
def test_invalid_or_unsafe_job_is_rejected(mutate):
    job = cantilever_job()
    mutate(job)
    with pytest.raises(ValueError):
        validate_fem_job(job)


def test_truss_only_free_rotations_are_rejected_as_ambiguous():
    job = cantilever_job()
    job["elements"][0].update(type="truss_3d")
    job["elements"][0].pop("orientation_reference")
    job["supports"][1]["fixity"] = [False, True, True, False, False, False]
    with pytest.raises(ValueError, match="rotational"):
        validate_fem_job(job)


def test_valid_axial_truss_matches_independent_bar_solution(opensees_runtime):
    job = cantilever_job()
    job["elements"][0].update(type="truss_3d")
    job["elements"][0].pop("orientation_reference")
    job["supports"][1]["fixity"] = [False, True, True, True, True, True]
    job["load_cases"][0]["nodal_loads"][0]["values_N_Nm"] = [1000.0, 0, 0, 0, 0, 0]
    result = run_linear_static_job(job)
    response = result["results"]["lc1"]
    assert response["node_results"]["n2"]["displacement_m"][0] == pytest.approx(
        1000 * 3 / (0.02 * 200e9), rel=1e-9
    )
    assert response["equilibrium"]["force_residual_N"] < 1e-8


def test_uniform_local_element_load_matches_independent_beam_solution(opensees_runtime):
    job = cantilever_job()
    job["nodes"] = [
        {"id": "n1", "coordinates_m": [0.0, 0.0, 0.0]},
        {"id": "n2", "coordinates_m": [3.0, 0.0, 0.0]},
        {"id": "n3", "coordinates_m": [6.0, 0.0, 0.0]},
    ]
    job["materials"][0].update(E_Pa=30e9, G_Pa=12.5e9)
    job["sections"][0].update(A_m2=0.5, Iy_m4=5e-4, Iz_m4=5e-4, J_m4=0.02)
    job["elements"] = [
        {
            "id": element_id,
            "type": "elastic_frame_3d",
            "node_i": node_i,
            "node_j": node_j,
            "material_id": "mat1",
            "section_id": "sec1",
            "orientation_reference": [0.0, 0.0, 1.0],
        }
        for element_id, node_i, node_j in (("e1", "n1", "n2"), ("e2", "n2", "n3"))
    ]
    job["supports"] = [
        {"node_id": "n1", "fixity": [True, True, True, True, False, True]},
        {"node_id": "n2", "fixity": [False, True, False, False, False, True]},
        {"node_id": "n3", "fixity": [False, True, True, True, False, True]},
    ]
    job["load_cases"] = [
        {
            "id": "udl",
            "nodal_loads": [],
            "element_loads": [
                {
                    "element_id": element_id,
                    "type": "uniform_local",
                    "values_N_per_m": [0.0, 0.0, -2000.0],
                }
                for element_id in ("e1", "e2")
            ],
        }
    ]
    result = run_linear_static_job(job)
    response = result["results"]["udl"]
    expected = 5 * 2000 * 6**4 / (384 * 30e9 * 5e-4)
    assert response["node_results"]["n2"]["displacement_m"][2] == pytest.approx(
        -expected, rel=1e-9
    )
    assert response["node_results"]["n1"]["reaction_N_Nm"][2] == pytest.approx(
        6000, rel=1e-9
    )
    assert response["node_results"]["n3"]["reaction_N_Nm"][2] == pytest.approx(
        6000, rel=1e-9
    )


def test_unrestrained_model_fails_closed_without_partial_result(opensees_runtime):
    job = cantilever_job()
    job["supports"][0]["fixity"] = [False, False, False, False, False, False]
    with pytest.raises(FEMRunError):
        run_linear_static_job(job)


def test_nonfinite_nodal_load_and_unconnected_node_are_rejected():
    job = cantilever_job()
    job["load_cases"][0]["nodal_loads"][0]["values_N_Nm"][0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        validate_fem_job(job)
    job = cantilever_job()
    job["nodes"].append({"id": "orphan", "coordinates_m": [9.0, 0.0, 0.0]})
    job["supports"].append(
        {"node_id": "orphan", "fixity": [True, True, True, True, True, True]}
    )
    with pytest.raises(ValueError, match="unconnected"):
        validate_fem_job(job)


def test_fem_task_binding_requires_every_bound_source_in_same_issue_snapshot(
    monkeypatch,
):
    import engineering_execution.fem as fem

    monkeypatch.setattr(
        fem, "_native_backend_binary", lambda: ("openseespymac", "3.8.0.0", "b" * 64)
    )
    monkeypatch.setattr(fem.metadata, "version", lambda _name: "3.8.0.0")
    job = cantilever_job()
    job["source_bindings"] = {
        "geometry_source_ids": ["geometry"],
        "properties_source_ids": ["properties"],
        "supports_source_ids": ["supports"],
        "loads_source_ids": ["loads"],
    }
    snapshot = {
        "records": [
            {
                "node_id": source_id,
                "node_type": "evidence",
                "attributes": {"revision": "A"},
            }
            for source_id in ("geometry", "properties", "supports", "loads")
        ]
    }
    binding = fem_task_binding(job, snapshot)
    assert len(binding["model_digest"]) == 64
    assert len(binding["binding_digest"]) == 64
    snapshot["records"].pop()
    with pytest.raises(ValueError, match="linked evidence"):
        fem_task_binding(job, snapshot)


def test_isolated_runner_bounds_timeout_before_launch(monkeypatch):
    with pytest.raises(ValueError, match="timeout"):
        run_linear_static_job_isolated(cantilever_job(), timeout_seconds=0)
