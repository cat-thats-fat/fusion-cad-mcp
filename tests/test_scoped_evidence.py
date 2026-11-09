import copy
import math

import pytest

from fusion_cad_mcp.scoped_evidence import compare_scoped_evidence, prepare_evidence_scope
from fusion_cad_mcp.workspace_native import build_workspace_script

SCOPE = {
    "version": 1,
    "path": "root",
    "sections": [
        {
            "kind": "body",
            "names": ["A", "B"],
            "fields": ["bounds_mm"],
            "tolerances": {"bounds_mm": 0.01},
        },
        {
            "kind": "occurrence",
            "names": ["Child:1"],
            "fields": ["transform_cm"],
            "tolerances": {"transform_cm": 1e-8},
        },
    ],
}


def snapshot():
    def row(kind, name, field, units, value):
        return {
            "kind": kind,
            "name": name,
            "fields": {field: {"status": "available", "units": units, "value": value}},
        }

    return {
        "version": 1,
        "scope": copy.deepcopy(SCOPE),
        "binding": {"document": "fixture", "target_handle": "handle"},
        "counts": {"body": 2, "occurrence": 1},
        "records": [
            row("body", "A", "bounds_mm", "mm", [[0, 0, 0], [1, 2, 3]]),
            row("body", "B", "bounds_mm", "mm", [[0, 0, 0], [4, 5, 6]]),
            row(
                "occurrence",
                "Child:1",
                "transform_cm",
                "cm",
                [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            ),
        ],
    }


def test_reordered_records_and_tolerant_values_pass():
    a = snapshot()
    b = copy.deepcopy(a)
    b["records"].reverse()
    b["records"][2]["fields"]["bounds_mm"]["value"][1][0] += 0.005
    assert compare_scoped_evidence(SCOPE, a, b)["status"] == "passed"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda b: b["records"].pop(), "missing_entity"),
        (lambda b: b["records"].append(copy.deepcopy(b["records"][0])), "duplicate_entity"),
        (lambda b: b["records"][0].update(name="renamed"), "extra_entity"),
        (
            lambda b: b["records"][0]["fields"]["bounds_mm"].update(units="cm"),
            "unavailable_or_units_mismatch",
        ),
        (
            lambda b: b["records"][0]["fields"]["bounds_mm"].update(status="unavailable"),
            "unavailable_or_units_mismatch",
        ),
        (
            lambda b: b["records"][0]["fields"]["bounds_mm"]["value"][1].pop(),
            "different_or_invalid",
        ),
        (
            lambda b: b["records"][0]["fields"]["bounds_mm"]["value"][1].__setitem__(0, math.nan),
            "different_or_invalid",
        ),
        (
            lambda b: b["records"][2]["fields"]["transform_cm"]["value"].pop(),
            "different_or_invalid",
        ),
        (lambda b: b["records"][0].update(fields=[]), "unavailable_or_units_mismatch"),
    ],
)
def test_invalid_evidence_cannot_pass(mutation, reason):
    a, b = snapshot(), snapshot()
    mutation(b)
    result = compare_scoped_evidence(SCOPE, a, b)
    assert result["status"] != "passed"
    assert reason in [d["reason"] for d in result["differences"]]


def test_binding_changes_require_explicit_archive_rebind():
    a, b = snapshot(), snapshot()
    b["binding"]["target_handle"] = "new"
    assert compare_scoped_evidence(SCOPE, a, b)["status"] == "failed"
    assert compare_scoped_evidence(SCOPE, a, b, allow_rebind=True)["status"] == "passed"


def test_empty_inventory_cannot_satisfy_names():
    a, b = snapshot(), snapshot()
    b["records"] = []
    assert compare_scoped_evidence(SCOPE, a, b)["status"] == "failed"


def test_scope_rejects_missing_tolerance_and_duplicate_names():
    scope = copy.deepcopy(SCOPE)
    del scope["sections"][0]["tolerances"]["bounds_mm"]
    with pytest.raises(ValueError):
        prepare_evidence_scope(scope)
    scope = copy.deepcopy(SCOPE)
    scope["sections"][0]["names"].append("A")
    with pytest.raises(ValueError):
        prepare_evidence_scope(scope)


def test_generated_script_compiles_without_implicit_recompute():
    source = build_workspace_script("inspect", {"target_handle": "x", "scope": SCOPE})
    compile(source, "<workspace>", "exec")
    capture = source.split("def _capture_scoped(", 1)[1].split("_ACTION =", 1)[0]
    assert "computeAll" not in capture


def test_original_mesh_witness_is_bounded_and_changes_with_geometry():
    from types import SimpleNamespace as NS

    from fusion_cad_mcp.native_mesh import MESH_EVIDENCE_SCRIPT

    namespace = {}
    exec(MESH_EVIDENCE_SCRIPT, namespace)
    mesh = NS(
        nodeCount=3,
        triangleCount=1,
        quadCount=0,
        polygonCount=0,
        nodeCoordinatesAsDouble=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        triangleNodeIndices=[0, 1, 2],
        quadNodeIndices=[],
        polygonNodeIndices=[],
        nodeCountPerPolygon=[],
    )
    box = NS(minPoint=NS(x=0.0, y=0.0, z=0.0), maxPoint=NS(x=1.0, y=1.0, z=0.0))
    body = NS(nativeObject=None, mesh=mesh, boundingBox=box)
    first = namespace["_mesh_witness"](body)
    assert first["node_count"] == 3 and first["source"] == "MeshBody.mesh"
    mesh.nodeCoordinatesAsDouble[3] = 2.0
    assert namespace["_mesh_witness"](body)["geometry_sha256"] != first["geometry_sha256"]
    mesh.triangleNodeIndices.pop()
    with pytest.raises(ValueError):
        namespace["_mesh_witness"](body)
    mesh.nodeCount = 1000001
    with pytest.raises(ValueError, match="too large"):
        namespace["_mesh_witness"](body)


def test_native_capture_reads_inventory_and_marks_missing_fields_unavailable():
    import inspect
    from types import SimpleNamespace as NS

    from fusion_cad_mcp import scoped_evidence
    from fusion_cad_mcp.native_mesh import MESH_EVIDENCE_SCRIPT

    class Collection:
        def __init__(self, items):
            self.items = items
            self.count = len(items)

        def item(self, index):
            return self.items[index]

    box = NS(minPoint=NS(x=0, y=0, z=0), maxPoint=NS(x=1, y=2, z=3))
    root = NS(
        bRepBodies=Collection(
            [
                NS(name="A", isValid=True, preciseBoundingBox=box),
                NS(name="B", isValid=True, preciseBoundingBox=None),
            ]
        )
    )
    entry = {"root": root, "design": NS(), "document": NS(name="fixture")}
    scope = {
        "version": 1,
        "path": "root",
        "sections": [
            {
                "kind": "body",
                "names": ["A", "B"],
                "fields": ["bounds_mm"],
                "tolerances": {"bounds_mm": 0},
            }
        ],
    }
    namespace = {}
    exec(
        inspect.getsource(scoped_evidence).split("SCOPED_CAPTURE_SCRIPT =", 1)[0]
        + MESH_EVIDENCE_SCRIPT
        + scoped_evidence.SCOPED_CAPTURE_SCRIPT,
        namespace,
    )
    evidence = namespace["_capture_scoped"](entry, "handle", scope)
    assert evidence["records"][0]["fields"]["bounds_mm"]["value"][1] == [10, 20, 30]
    assert evidence["records"][1]["fields"]["bounds_mm"]["status"] == "unavailable"
    assert compare_scoped_evidence(scope, evidence, evidence)["status"] == "incomplete"


def test_mesh_witness_requires_declared_tolerances_and_complete_geometry():
    from types import SimpleNamespace as NS

    from fusion_cad_mcp.native_mesh import MESH_EVIDENCE_SCRIPT

    namespace = {}
    exec(MESH_EVIDENCE_SCRIPT, namespace)
    mesh = NS(
        nodeCount=3,
        triangleCount=1,
        quadCount=0,
        polygonCount=0,
        nodeCoordinatesAsDouble=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        triangleNodeIndices=[0, 1, 2],
        quadNodeIndices=[],
        polygonNodeIndices=[],
        nodeCountPerPolygon=[],
    )
    box = NS(minPoint=NS(x=0.0, y=0.0, z=0.0), maxPoint=NS(x=1.0, y=1.0, z=0.0))
    witness = namespace["_mesh_witness"](NS(nativeObject=None, mesh=mesh, boundingBox=box))
    scope = {
        "version": 1,
        "path": "root",
        "sections": [
            {
                "kind": "mesh",
                "names": ["Scan"],
                "fields": ["witness"],
                "tolerances": {"witness": {"bounds_mm": 0.01, "transform_cm": 0.001}},
            }
        ],
    }
    snap = {
        "version": 1,
        "scope": scope,
        "binding": {"document": "fixture", "target_handle": "h"},
        "counts": {"mesh": 1},
        "records": [
            {
                "kind": "mesh",
                "name": "Scan",
                "fields": {"witness": {"status": "available", "units": "native", "value": witness}},
            }
        ],
    }
    other = copy.deepcopy(snap)
    other["records"][0]["fields"]["witness"]["value"]["occurrence_transform_cm"][3] += 0.0005
    assert compare_scoped_evidence(scope, snap, other)["status"] == "passed"
    other["records"][0]["fields"]["witness"]["value"]["occurrence_transform_cm"].pop()
    assert compare_scoped_evidence(scope, snap, other)["status"] == "failed"


@pytest.mark.parametrize(
    "matrix",
    [
        [0] * 16,
        [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0],
        [1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    ],
)
def test_equal_invalid_poses_cannot_pass(matrix):
    a = snapshot()
    a["records"][2]["fields"]["transform_cm"]["value"] = matrix
    assert compare_scoped_evidence(SCOPE, a, copy.deepcopy(a))["status"] == "failed"


def test_equal_inverted_body_bounds_cannot_pass():
    a = snapshot()
    a["records"][0]["fields"]["bounds_mm"]["value"] = [[2, 0, 0], [1, 1, 1]]
    assert compare_scoped_evidence(SCOPE, a, copy.deepcopy(a))["status"] == "failed"


def test_mesh_comparison_rejects_invalid_equal_bounds_and_pose():
    from fusion_cad_mcp.scoped_evidence import _evidence_mesh_equal

    witness = {
        "geometry_sha256": "a" * 64,
        "node_count": 3,
        "triangle_count": 1,
        "quad_count": 0,
        "polygon_count": 0,
        "frame": "component_local",
        "units": "cm",
        "source": "MeshBody.mesh",
        "appearance": "unknown",
        "bounds_mm": [[0, 0, 0], [1, 1, 1]],
        "occurrence_transform_cm": [1, 0, 0, 2, 0, 1, 0, 3, 0, 0, 1, 4, 0, 0, 0, 1],
    }
    tol = {"bounds_mm": 0.01, "transform_cm": 0.001}
    assert _evidence_mesh_equal(witness, copy.deepcopy(witness), tol)
    witness["bounds_mm"] = [[2, 0, 0], [1, 1, 1]]
    assert not _evidence_mesh_equal(witness, copy.deepcopy(witness), tol)
    witness["bounds_mm"] = [[0, 0, 0], [1, 1, 1]]
    witness["occurrence_transform_cm"] = [0] * 16
    assert not _evidence_mesh_equal(witness, copy.deepcopy(witness), tol)


def test_volume_accuracy_is_required():
    scope = {
        "version": 1,
        "path": "root",
        "sections": [
            {
                "kind": "body",
                "names": ["B"],
                "fields": ["volume_mm3"],
                "tolerances": {"volume_mm3": 0.1},
            }
        ],
    }
    record = {
        "kind": "body",
        "name": "B",
        "fields": {
            "volume_mm3": {
                "status": "available",
                "units": "mm^3",
                "value": 10,
                "accuracy": "LowCalculationAccuracy",
            }
        },
    }
    evidence = {
        "version": 1,
        "scope": scope,
        "binding": {"document": "d", "target_handle": "h"},
        "counts": {"body": 1},
        "records": [record],
    }
    assert compare_scoped_evidence(scope, evidence, copy.deepcopy(evidence))["status"] == "passed"
    del evidence["records"][0]["fields"]["volume_mm3"]["accuracy"]
    assert (
        compare_scoped_evidence(scope, evidence, copy.deepcopy(evidence))["status"] == "incomplete"
    )


def test_native_capture_accepts_occurrence_mesh_body_vector():
    import inspect
    from types import SimpleNamespace as NS

    from fusion_cad_mcp import scoped_evidence
    from fusion_cad_mcp.native_mesh import MESH_EVIDENCE_SCRIPT

    scope = {
        "version": 1,
        "path": "Outer:1+Reference:1",
        "sections": [
            {
                "kind": "mesh",
                "names": ["Scan"],
                "fields": ["witness"],
                "tolerances": {"witness": {"bounds_mm": 0, "transform_cm": 0}},
            }
        ],
    }
    occurrence = NS(component=NS(), meshBodies=[NS(name="Scan", isValid=True)])
    namespace = {}
    exec(
        inspect.getsource(scoped_evidence).split("SCOPED_CAPTURE_SCRIPT =", 1)[0]
        + MESH_EVIDENCE_SCRIPT
        + scoped_evidence.SCOPED_CAPTURE_SCRIPT,
        namespace,
    )
    namespace["_strict_occurrence"] = lambda root, path: occurrence
    namespace["_mesh_witness"] = lambda body, occ: {"native": body.name}
    result = namespace["_capture_scoped"](
        {"root": NS(), "design": NS(), "document": NS(name="d")}, "h", scope
    )
    assert result["counts"] == {"mesh": 1}
    assert result["records"][0]["fields"]["witness"]["value"] == {"native": "Scan"}


def test_mesh_witness_skips_zero_count_native_index_getters():
    from types import SimpleNamespace as NS

    from fusion_cad_mcp.native_mesh import MESH_EVIDENCE_SCRIPT

    class TriangleOnly:
        nodeCount, triangleCount, quadCount, polygonCount = 3, 1, 0, 0
        nodeCoordinatesAsDouble = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        triangleNodeIndices = [0, 1, 2]

        @property
        def quadNodeIndices(self):
            raise RuntimeError("zero count native getter")

        @property
        def polygonNodeIndices(self):
            raise RuntimeError("zero count native getter")

        @property
        def nodeCountPerPolygon(self):
            raise RuntimeError("zero count native getter")

    box = NS(minPoint=NS(x=0.0, y=0.0, z=0.0), maxPoint=NS(x=1.0, y=1.0, z=0.0))
    namespace = {}
    exec(MESH_EVIDENCE_SCRIPT, namespace)
    assert (
        namespace["_mesh_witness"](NS(nativeObject=None, mesh=TriangleOnly(), boundingBox=box))[
            "triangle_count"
        ]
        == 1
    )


def test_native_volume_capture_records_requested_accuracy():
    import inspect
    from types import SimpleNamespace as NS

    from fusion_cad_mcp import scoped_evidence
    from fusion_cad_mcp.native_mesh import MESH_EVIDENCE_SCRIPT

    class Collection:
        count = 1

        def item(self, index):
            assert index == 0
            return NS(
                name="B",
                isValid=True,
                isSolid=True,
                getPhysicalProperties=lambda accuracy: NS(accuracy=accuracy, volume=2.0),
            )

    scope = {
        "version": 1,
        "path": "root",
        "sections": [
            {
                "kind": "body",
                "names": ["B"],
                "fields": ["volume_mm3"],
                "tolerances": {"volume_mm3": 1},
            }
        ],
    }
    namespace = {}
    exec(
        inspect.getsource(scoped_evidence).split("SCOPED_CAPTURE_SCRIPT =", 1)[0]
        + MESH_EVIDENCE_SCRIPT
        + scoped_evidence.SCOPED_CAPTURE_SCRIPT,
        namespace,
    )
    namespace["adsk"] = NS(fusion=NS(CalculationAccuracy=NS(LowCalculationAccuracy=17)))
    entry = {"root": NS(bRepBodies=Collection()), "design": NS(), "document": NS(name="d")}
    result = namespace["_capture_scoped"](entry, "h", scope)
    field = result["records"][0]["fields"]["volume_mm3"]
    assert field == {
        "status": "available",
        "units": "mm^3",
        "value": 2000,
        "accuracy": "LowCalculationAccuracy",
    }
