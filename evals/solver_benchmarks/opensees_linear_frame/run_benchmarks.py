"""Synthetic analytical benchmarks for the candidate OpenSeesPy backend.

These cases verify selected solver response primitives only. They are not a
validated production adapter, project model, code check, or structural approval.
Run with OpenSeesPy 3.8.0.0 in an isolated Python environment.
"""

from __future__ import annotations

import math

import openseespy.opensees as ops


def configure_linear_static() -> None:
    ops.system("BandSPD")
    ops.numberer("Plain")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")


def check_close(label: str, actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-11):
        raise AssertionError(f"{label}: actual={actual}, expected={expected}")


def axial_truss() -> dict:
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 2.0, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 1, 1, 1, 1, 1)
    ops.uniaxialMaterial("Elastic", 1, 200.0e9)
    ops.element("Truss", 1, 1, 2, 0.01, 1)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 1000.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    configure_linear_static()
    status = ops.analyze(1)
    displacement = ops.nodeDisp(2, 1)
    ops.reactions()
    reaction = ops.nodeReaction(1, 1)
    force = ops.eleForce(1, 1)
    expected = 1000.0 * 2.0 / (0.01 * 200.0e9)
    assert status == 0
    check_close("axial displacement", displacement, expected)
    check_close("support reaction", reaction, -1000.0)
    check_close("element end force", force, -1000.0)
    return {"displacement_m": displacement, "reaction_N": reaction}


def cantilever_frame(length: float = 3.0, load: float = 1000.0) -> dict:
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, length, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.geomTransf("Linear", 1, 0.0, 1.0, 0.0)
    ops.element(
        "elasticBeamColumn", 1, 1, 2, 0.02, 200e9, 80e9, 1e-5, 2e-4, 1e-4, 1
    )
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -load, 0.0, 0.0, 0.0)
    configure_linear_static()
    status = ops.analyze(1)
    displacement = ops.nodeDisp(2, 3)
    rotation = ops.nodeDisp(2, 5)
    ops.reactions()
    reaction = ops.nodeReaction(1, 3)
    expected_displacement = load * length**3 / (3.0 * 200e9 * 1e-4)
    expected_rotation = load * length**2 / (2.0 * 200e9 * 1e-4)
    assert status == 0
    check_close("cantilever tip displacement", displacement, -expected_displacement)
    check_close("cantilever tip rotation", rotation, expected_rotation)
    check_close("cantilever support reaction", reaction, load)
    return {"tip_z_m": displacement, "tip_rotation_rad": rotation}


def simply_supported_center_load() -> dict:
    length, load, elastic_modulus, inertia = 6.0, 12000.0, 30e9, 5e-4
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    for tag, x in enumerate((0.0, length / 2.0, length), 1):
        ops.node(tag, x, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 0, 1)
    ops.fix(3, 0, 1, 1, 1, 0, 1)
    ops.geomTransf("Linear", 1, 0.0, 0.0, 1.0)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for tag, i_node, j_node in ((1, 1, 2), (2, 2, 3)):
        ops.element(
            "elasticBeamColumn",
            tag,
            i_node,
            j_node,
            0.5,
            elastic_modulus,
            12.5e9,
            0.02,
            inertia,
            inertia,
            1,
        )
    ops.load(2, 0.0, 0.0, -load, 0.0, 0.0, 0.0)
    configure_linear_static()
    status = ops.analyze(1)
    displacement = ops.nodeDisp(2, 3)
    ops.reactions()
    left_reaction = ops.nodeReaction(1, 3)
    right_reaction = ops.nodeReaction(3, 3)
    expected_displacement = load * length**3 / (48.0 * elastic_modulus * inertia)
    assert status == 0
    check_close("simply-supported midspan displacement", displacement, -expected_displacement)
    check_close("left reaction", left_reaction, load / 2.0)
    check_close("right reaction", right_reaction, load / 2.0)
    return {
        "midspan_z_m": displacement,
        "left_reaction_N": left_reaction,
        "right_reaction_N": right_reaction,
    }


def simply_supported_uniform_load() -> dict:
    length, load_per_length, elastic_modulus, inertia = 6.0, 2000.0, 30e9, 5e-4
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    for tag, x in enumerate((0.0, length / 2.0, length), 1):
        ops.node(tag, x, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 0, 1)
    ops.fix(3, 0, 1, 1, 1, 0, 1)
    ops.geomTransf("Linear", 1, 0.0, 0.0, 1.0)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for tag, i_node, j_node in ((1, 1, 2), (2, 2, 3)):
        ops.element(
            "elasticBeamColumn",
            tag,
            i_node,
            j_node,
            0.5,
            elastic_modulus,
            12.5e9,
            0.02,
            inertia,
            inertia,
            1,
        )
        ops.eleLoad("-ele", tag, "-type", "-beamUniform", 0.0, -load_per_length)
    configure_linear_static()
    status = ops.analyze(1)
    displacement = ops.nodeDisp(2, 3)
    ops.reactions()
    left_reaction = ops.nodeReaction(1, 3)
    right_reaction = ops.nodeReaction(3, 3)
    expected_displacement = 5.0 * load_per_length * length**4 / (
        384.0 * elastic_modulus * inertia
    )
    assert status == 0
    check_close("uniform-load midspan displacement", displacement, -expected_displacement)
    check_close("uniform-load left reaction", left_reaction, load_per_length * length / 2.0)
    check_close("uniform-load right reaction", right_reaction, load_per_length * length / 2.0)
    return {
        "midspan_z_m": displacement,
        "left_reaction_N": left_reaction,
        "right_reaction_N": right_reaction,
    }


def cantilever_global_axis_rotation() -> dict:
    length, load, elastic_modulus, inertia = 3.0, 1000.0, 200e9, 1e-4
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, length, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.geomTransf("Linear", 1, 0.0, 0.0, 1.0)
    ops.element(
        "elasticBeamColumn", 1, 1, 2, 0.02, elastic_modulus, 80e9, 1e-5, inertia, 2e-4, 1
    )
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -load, 0.0, 0.0, 0.0)
    configure_linear_static()
    status = ops.analyze(1)
    displacement = ops.nodeDisp(2, 3)
    expected_displacement = load * length**3 / (3.0 * elastic_modulus * inertia)
    assert status == 0
    check_close("rotated cantilever global-z displacement", displacement, -expected_displacement)
    return {"tip_z_m": displacement}


def main() -> None:
    cases = (
        ("3d_axial_truss", axial_truss),
        ("3d_cantilever_frame", cantilever_frame),
        ("3d_simply_supported_center_load", simply_supported_center_load),
        ("3d_simply_supported_uniform_load", simply_supported_uniform_load),
        ("3d_cantilever_global_axis_rotation", cantilever_global_axis_rotation),
    )
    for name, run in cases:
        print({"case": name, "result": run(), "status": "PASS"})
    ops.wipe()


if __name__ == "__main__":
    main()
