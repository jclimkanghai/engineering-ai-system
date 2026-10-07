"""Synthetic axial-stress cases exercise adapter control, not design certification."""

from copy import deepcopy

import pytest

from engineering_document_ai_brain import DocumentAIWorkflow
from engineering_execution import ExecutionService
from engineering_registry.models import GraphNode, NodeType
from engineering_registry.service import Principal, RegistryService, digest
from engineering_registry.store import SQLiteGraphStore


def manifest(version="1"):
    return {
        "solver_id": "synthetic-axial-stress",
        "version": version,
        "skill": {
            "name": "synthetic-stress-skill",
            "version": "1",
            "artifact_digest": "a" * 64,
        },
        "method": "Axial stress = force / area; synthetic adapter demonstration only.",
        "applicability_limits": [
            "Uniform direct axial load; no bending, buckling or capacity conclusion."
        ],
        "assumptions": ["Consistent SI units; area is positive."],
        "inputs": {
            "force": {"unit": "N", "minimum": 0, "maximum": 1e9},
            "area": {"unit": "m2", "minimum": 0.000001, "maximum": 100},
        },
        "outputs": {"stress": {"unit": "Pa", "minimum": 0, "maximum": 1e15}},
        "validation_cases": [
            {
                "inputs": {
                    "force": {"value": 1000, "unit": "N"},
                    "area": {"value": 0.01, "unit": "m2"},
                },
                "expected_outputs": {"stress": {"value": 100000, "unit": "Pa"}},
                "relative_tolerance": 1e-9,
                "absolute_tolerance": 0.001,
            },
            {
                "inputs": {
                    "force": {"value": 0, "unit": "N"},
                    "area": {"value": 0.1, "unit": "m2"},
                },
                "expected_outputs": {"stress": {"value": 0, "unit": "Pa"}},
                "relative_tolerance": 0,
                "absolute_tolerance": 0,
            },
        ],
    }


def axial_solver(quantities, _context):
    return {
        "values": {
            "stress": {
                "value": quantities["force"]["value"] / quantities["area"]["value"],
                "unit": "Pa",
            }
        },
        "warnings": [],
    }


def catalog_for(callback=axial_solver, definition=None):
    import engineering_execution

    assert hasattr(engineering_execution, "SolverCatalog"), (
        "Validated external solver catalogue is missing"
    )
    catalog = engineering_execution.SolverCatalog()
    catalog.register(definition or manifest(), callback)
    return catalog


def test_benchmark_mismatch_cannot_register_external_solver():
    def wrong_solver(q, c):
        result = axial_solver(q, c)
        result["values"]["stress"]["value"] += 100
        return result

    with pytest.raises(ValueError, match="benchmark"):
        catalog_for(wrong_solver)


@pytest.mark.parametrize(
    "change",
    [
        "nonfinite",
        "wrong_unit",
        "empty_limits",
        "unbounded",
        "bad_skill",
        "no_benchmark",
    ],
)
def test_invalid_solver_definition_or_output_is_rejected(change):
    definition = manifest()
    callback = axial_solver
    if change == "nonfinite":

        def callback(q, c):
            return {
                "values": {"stress": {"value": float("nan"), "unit": "Pa"}},
                "warnings": [],
            }
    elif change == "wrong_unit":

        def callback(q, c):
            return {
                "values": {"stress": {"value": 100000, "unit": "MPa"}},
                "warnings": [],
            }
    elif change == "empty_limits":
        definition["applicability_limits"] = []
    elif change == "unbounded":
        definition["inputs"]["area"]["minimum"] = float("-inf")
    elif change == "bad_skill":
        definition["skill"]["artifact_digest"] = "unknown"
    else:
        definition["validation_cases"] = []
    with pytest.raises(ValueError):
        catalog_for(callback, definition)


@pytest.fixture
def solver_services(tmp_path):
    with SQLiteGraphStore(tmp_path / "solver.sqlite") as store:
        agent = RegistryService(store, Principal("brain", frozenset({"SYNTHETIC"})))
        human = RegistryService(
            store, Principal("human-fixture", frozenset({"SYNTHETIC"}), "reviewer")
        )
        agent.register(
            GraphNode(
                "project:P", "SYNTHETIC", NodeType.PROJECT, "Synthetic solver test"
            )
        )
        agent.register(
            GraphNode(
                "evidence:Q",
                "SYNTHETIC",
                NodeType.EVIDENCE,
                "Synthetic quantity basis",
                {
                    "text": "Force is 20000 N; area is 0.01 m2.",
                    "locator": "synthetic-case-1",
                    "quantities": {
                        "force": {"value": 20000, "unit": "N"},
                        "area": {"value": 0.01, "unit": "m2"},
                    },
                },
            )
        )
        agent.propose_issue(
            "SYNTHETIC", "issue:S", "Calculate synthetic direct stress", ["evidence:Q"]
        )
        yield agent, human


def parameters():
    return {
        "solver_id": "synthetic-axial-stress",
        "quantities": {
            "force": {"value": 20000, "unit": "N"},
            "area": {"value": 0.01, "unit": "m2"},
        },
        "source_evidence_ids": {"force": "evidence:Q", "area": "evidence:Q"},
    }


def fem_job(source_id="evidence:Q"):
    return {
        "schema_version": 1,
        "model_id": "synthetic-cantilever",
        "units": "SI",
        "coordinate_system": "right_handed_global_xyz",
        "analysis_type": "linear_static",
        "nodes": [
            {"id": "a", "coordinates_m": [0.0, 0.0, 0.0]},
            {"id": "b", "coordinates_m": [3.0, 0.0, 0.0]},
        ],
        "materials": [{"id": "m", "E_Pa": 200e9, "G_Pa": 80e9}],
        "sections": [
            {"id": "s", "A_m2": 0.02, "Iy_m4": 2e-4, "Iz_m4": 1e-4, "J_m4": 1e-5}
        ],
        "elements": [
            {
                "id": "e",
                "type": "elastic_frame_3d",
                "node_i": "a",
                "node_j": "b",
                "material_id": "m",
                "section_id": "s",
                "orientation_reference": [0, 1, 0],
            }
        ],
        "supports": [
            {"node_id": "a", "fixity": [True, True, True, True, True, True]},
            {"node_id": "b", "fixity": [False, False, False, False, False, False]},
        ],
        "load_cases": [
            {
                "id": "lc",
                "nodal_loads": [
                    {"node_id": "b", "values_N_Nm": [0, 0, -1000, 0, 0, 0]}
                ],
                "element_loads": [],
            }
        ],
        "source_bindings": {
            "geometry_source_ids": [source_id],
            "properties_source_ids": [source_id],
            "supports_source_ids": [source_id],
            "loads_source_ids": [source_id],
        },
    }


def synthetic_analysis(phase, context):
    return {
        "producer": "engineering_document_ai",
        "phase": phase,
        "model": "offline-fixture",
        "response_id": "synthetic-response",
        "request_digest": digest(context),
        "findings": [],
        "usage": {},
        "lesson_context": context["approved_lessons"],
        "human_review_required": True,
    }


def prepare_solver(solver_services, catalog=None):
    agent, human = solver_services
    catalog = catalog or catalog_for()
    workflow = DocumentAIWorkflow(
        agent, analysis=synthetic_analysis, solver_catalog=catalog
    )
    task = workflow.structure_task(
        "SYNTHETIC", "S1", "issue:S", "external_solver", parameters()
    )
    ExecutionService(human, solver_catalog=catalog).authorize_task(
        "SYNTHETIC", "S1", task["task_digest"], "Synthetic method authorization"
    )
    return workflow, task, catalog


def test_external_skill_solver_returns_units_method_and_validation_provenance(
    solver_services,
):
    workflow, task, _ = prepare_solver(solver_services)
    outcome = workflow.execute_and_assess("SYNTHETIC", "S1")
    result = outcome["result"]["attributes"]["outputs"]
    # Independently calculated: 20000 / 0.01 = 2000000 Pa.
    assert result["values"]["stress"] == {"value": 2000000, "unit": "Pa"}
    assert result["solver"]["definition"]["skill"]["artifact_digest"] == "a" * 64
    assert result["solver"]["validation"]["passed"] is True
    assert task["solver_binding"] == result["solver"]
    assert task["brain_plan"]["analysis"]["phase"] == "execution_planning"
    assert any(
        c["criterion"] == "engineering_review" and c["passed"]
        for c in outcome["assessment"]["attributes"]["checks"]
    )
    assert solver_services[0].list_records("SYNTHETIC", "verification") == []


def test_fem_task_is_bound_to_same_issue_evidence_and_retained_freshness(
    solver_services, monkeypatch
):
    import engineering_execution.fem as fem

    monkeypatch.setattr(
        fem, "_native_backend_binary", lambda: ("openseespymac", "3.8.0.0", "b" * 64)
    )
    monkeypatch.setattr(fem.metadata, "version", lambda _name: "3.8.0.0")
    agent, _ = solver_services
    workflow = DocumentAIWorkflow(agent, analysis=synthetic_analysis)
    task = workflow.structure_task(
        "SYNTHETIC",
        "FEM-1",
        "issue:S",
        "fem_linear_static",
        {"job": fem_job()},
    )
    assert task["fem_binding"] == task["brain_plan"]["fem_binding"]
    assert task["brain_plan"]["analysis"]["phase"] == "execution_planning"
    assert task["parameters"]["job"]["model_id"] == "synthetic-cantilever"
    with pytest.raises(ValueError, match="linked evidence"):
        workflow.structure_task(
            "SYNTHETIC",
            "FEM-foreign",
            "issue:S",
            "fem_linear_static",
            {"job": fem_job("evidence:FOREIGN")},
        )


@pytest.mark.parametrize(
    "case", ["unit", "bounds", "boolean", "nonfinite", "foreign", "invented_value"]
)
def test_bad_inputs_fail_before_task_creation(solver_services, case):
    agent, _ = solver_services
    workflow = DocumentAIWorkflow(
        agent, analysis=synthetic_analysis, solver_catalog=catalog_for()
    )
    params = parameters()
    if case == "unit":
        params["quantities"]["force"]["unit"] = "kN"
    elif case == "bounds":
        params["quantities"]["area"]["value"] = 0
    elif case == "boolean":
        params["quantities"]["force"]["value"] = True
    elif case == "nonfinite":
        params["quantities"]["force"]["value"] = float("inf")
    elif case == "foreign":
        params["source_evidence_ids"]["force"] = "evidence:foreign"
    else:
        params["quantities"]["force"]["value"] = 100
    with pytest.raises(ValueError):
        workflow.structure_task(
            "SYNTHETIC", "bad", "issue:S", "external_solver", params
        )
    assert workflow.execution.list_tasks("SYNTHETIC") == []


def test_changed_skill_or_missing_catalogue_blocks_authorized_task(solver_services):
    workflow, _, _ = prepare_solver(solver_services)
    changed = catalog_for(definition=manifest("2"))
    with pytest.raises(ValueError, match="solver|Solver"):
        ExecutionService(solver_services[0], solver_catalog=changed).execute(
            "SYNTHETIC", "S1"
        )
    with pytest.raises(ValueError, match="solver|Solver"):
        ExecutionService(solver_services[0]).execute("SYNTHETIC", "S1")
    assert workflow.execution.get_task("SYNTHETIC", "S1")["attempts"] == []


def test_solver_requires_explicit_technical_reasoning_workflow(solver_services):
    workflow = DocumentAIWorkflow(solver_services[0], solver_catalog=catalog_for())
    with pytest.raises(ValueError, match="reasoning|analysis"):
        workflow.structure_task(
            "SYNTHETIC", "bad", "issue:S", "external_solver", parameters()
        )


def test_catalogue_cannot_be_mutated_through_returned_manifest():
    catalog = catalog_for()
    first = catalog.binding("synthetic-axial-stress")
    changed = deepcopy(first)
    changed["definition"]["inputs"]["area"]["minimum"] = -1
    assert (
        catalog.binding("synthetic-axial-stress")["definition"]["inputs"]["area"][
            "minimum"
        ]
        == 0.000001
    )


def test_benchmark_pass_is_not_reported_as_production_engineering_validation():
    binding = catalog_for().binding("synthetic-axial-stress")

    assert binding["validation"]["scope"] == "adapter_conformance_only"
    assert binding["validation"]["production_validated"] is False


def test_production_solver_requires_explicit_human_and_independent_benchmark_record():
    catalog = __import__("engineering_execution").SolverCatalog()
    with pytest.raises(ValueError, match="validation record"):
        catalog.register(
            manifest(), axial_solver, validation_record={"approved_by": "AI"}
        )

    validation_record = {
        "discipline": "structural",
        "governing_standard": "Example Standard",
        "governing_edition": "2025",
        "approval_record_id": "HUMAN-DECISION-01",
        "approved_by": "qualified structural engineer",
        "approval_date": "2026-10-06",
        "benchmark_source_digest": "b" * 64,
        "benchmark_independent": True,
        "limitations": ["Synthetic fixture; not suitable for engineering use."],
    }
    binding = catalog.register(
        manifest(), axial_solver, validation_record=validation_record
    )
    assert binding["validation"]["scope"] == "production_method_validation"
    assert binding["validation"]["production_validated"] is True
    assert binding["validation"]["validation_record"] == validation_record


@pytest.mark.parametrize(
    "change", ["dependent_benchmarks", "bad_digest", "bad_date", "no_limitations"]
)
def test_invalid_production_validation_evidence_is_rejected(change):
    record = {
        "discipline": "structural",
        "governing_standard": "Example Standard",
        "governing_edition": "2025",
        "approval_record_id": "HUMAN-DECISION-01",
        "approved_by": "qualified structural engineer",
        "approval_date": "2026-10-06",
        "benchmark_source_digest": "b" * 64,
        "benchmark_independent": True,
        "limitations": ["Demonstration only."],
    }
    if change == "dependent_benchmarks":
        record["benchmark_independent"] = False
    elif change == "bad_digest":
        record["benchmark_source_digest"] = "unknown"
    elif change == "bad_date":
        record["approval_date"] = "yesterday"
    else:
        record["limitations"] = []
    with pytest.raises(ValueError, match="validation|benchmark|date|limitations"):
        __import__("engineering_execution").SolverCatalog().register(
            manifest(), axial_solver, validation_record=record
        )


def test_changed_solver_after_output_blocks_new_technical_assessment(solver_services):
    workflow, _, _ = prepare_solver(solver_services)
    workflow.execute_and_assess("SYNTHETIC", "S1")
    changed = DocumentAIWorkflow(
        solver_services[0],
        analysis=synthetic_analysis,
        solver_catalog=catalog_for(definition=manifest("2")),
    )
    with pytest.raises(ValueError, match="solver|Solver"):
        changed.assess_output("SYNTHETIC", "S1")


def test_changed_catalogue_during_planning_does_not_silently_change_method(
    solver_services,
):
    agent, _ = solver_services
    workflow = None

    def analysis(phase, context):
        workflow.execution.solver_catalog = catalog_for(definition=manifest("2"))
        return synthetic_analysis(phase, context)

    workflow = DocumentAIWorkflow(
        agent, analysis=analysis, solver_catalog=catalog_for()
    )
    with pytest.raises(ValueError, match="solver|Solver"):
        workflow.structure_task(
            "SYNTHETIC", "S1", "issue:S", "external_solver", parameters()
        )
    assert workflow.execution.list_tasks("SYNTHETIC") == []


def test_mcp_host_cannot_bypass_project_run_for_solver_work(solver_services):
    import asyncio

    from engineering_registry.mcp_server import create_server

    agent, _ = solver_services
    catalog = catalog_for()

    def factory(registry):
        return DocumentAIWorkflow(
            registry, analysis=synthetic_analysis, solver_catalog=catalog
        )

    server = create_server(agent, workflow_factory=factory)

    async def scenario():
        names = {tool.name for tool in await server.list_tools()}
        assert not {"propose_task", "execute_task"} & names

    asyncio.run(scenario())


@pytest.mark.parametrize("catalogue_state", ["changed", "unavailable"])
def test_direct_registry_decision_rechecks_solver_catalogue(
    solver_services, catalogue_state
):
    agent, human = solver_services
    from engineering_registry.extended_demo import synthetic_client_project_brief

    human.record_client_project_brief(
        "SYNTHETIC", "B1", synthetic_client_project_brief(["evidence:Q"])
    )
    human.record_client_mandate(
        "SYNTHETIC",
        "M1",
        "issue:S",
        {
            "objective": "Demonstrate the synthetic stress adapter",
            "scope": "Synthetic calculation only",
            "acceptance_criteria": ["Return stress in Pa with provenance"],
            "evidence_ids": ["evidence:Q"],
            "allowed_tools": ["external_solver"],
            "risk_level": "low",
            "importance_level": "low",
            "simple": True,
            "reversible": True,
            "consequence_domains": [],
            "unknowns": [],
        },
    )
    human.approve_delegation_policy(
        "SYNTHETIC",
        "P1",
        {
            "allowed_tools": ["external_solver"],
            "allowed_solver_ids": ["synthetic-axial-stress"],
            "allowed_dispositions": ["hold"],
            "rationale": "Synthetic delegated hold fixture",
        },
    )

    def review(context):
        return {
            "status": "aligned"
            if context.get("phase") == "plan"
            else "insufficient_information",
            "summary": "Source authority is unverified",
            "checks": [
                {
                    "criterion": criterion,
                    "status": "aligned"
                    if context.get("phase") == "plan"
                    else "insufficient_information",
                    "detail": "Synthetic case retains unknown authority",
                    "evidence_ids": ["evidence:Q"],
                }
                for criterion in (
                    ("plan_alignment",)
                    if context.get("phase") == "plan"
                    else (
                        "plan_alignment",
                        "execution_direction",
                        "outcome_alignment",
                    )
                )
            ],
            "unknowns": []
            if context.get("phase") == "plan"
            else ["Source authority is unverified"],
            "evidence_ids": ["evidence:Q"],
            "method": "offline-fixture",
            "model": "offline-fixture",
            "response_id": None,
            "request_digest": digest(context),
        }

    catalog = catalog_for()
    delegate = RegistryService(
        agent.store, Principal("delegate", frozenset({"SYNTHETIC"}), "delegate")
    )
    workflow = DocumentAIWorkflow(
        agent,
        analysis=synthetic_analysis,
        solver_catalog=catalog,
        alignment_reviewer=review,
        decision_registry=delegate,
        delegation_policy_id="P1",
    )
    task = workflow.structure_task(
        "SYNTHETIC", "S1", "issue:S", "external_solver", parameters()
    )
    ExecutionService(human, solver_catalog=catalog).authorize_task(
        "SYNTHETIC", "S1", task["task_digest"], "Synthetic authorization"
    )
    outcome = workflow.execute_and_assess("SYNTHETIC", "S1")
    assert outcome["decision"]["attributes"]["disposition"] == "hold"
    attrs = outcome["decision"]["attributes"]
    payload = {
        key: deepcopy(attrs[key])
        for key in (
            "task_id",
            "policy_id",
            "assessment_id",
            "assessment_digest",
            "alignment_review_id",
            "alignment_review_digest",
            "disposition",
            "rationale",
            "evidence_ids",
        )
    }
    payload["rationale"] = "Another decision after changing the solver host"
    workflow.execution.solver_catalog = (
        catalog_for(definition=manifest("2")) if catalogue_state == "changed" else None
    )
    with pytest.raises(ValueError, match="solver|Solver"):
        workflow.decision_registry.decide_delegated_result("SYNTHETIC", payload)
