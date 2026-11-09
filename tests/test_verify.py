"""Verification outputs from actual generated code using local API doubles."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import verify as v
from tests._assembly_fakes import Collection, ScriptAdapter, occurrence, point, root_with


def test_bounding_box_reads_exact_quoted_name_and_converts_coordinates(monkeypatch):
    name = 'plate "front" \\ Ω'
    chosen = SimpleNamespace(
        name=name, boundingBox=SimpleNamespace(minPoint=point(-4, -2.5, 0), maxPoint=point(4, 2.5, 0.5))
    )
    root = root_with(bodies=[SimpleNamespace(name="decoy"), chosen])

    env = v.bounding_box(ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root)), name)

    assert env.ok is True
    assert env.result == {
        "ok": True, "count": 1, "bodies": [{
            "name": name, "path": name,
            "bbox_mm": {"min": [-40, -25, 0], "max": [40, 25, 5]},
            "extent_mm": [80, 50, 5],
        }],
    }


def test_build_volume_walks_components(monkeypatch):
    root = root_with(
        bodies=[SimpleNamespace(name="Plate", volume=0.125)],
        occurrences=[occurrence("Part:1+Leaf:1", bodies=[SimpleNamespace(name="Body", volume=2.25)])],
    )

    env = v.volume(ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root)))

    assert env.ok is True
    assert env.result["count"] == 2
    assert env.result["bodies"] == [
        {"name": "Plate", "path": "Plate", "volume_cm3": 0.125, "volume_mm3": 125},
        {"name": "Body", "path": "Part:1+Leaf:1 / Body", "volume_cm3": 2.25, "volume_mm3": 2250},
    ]


def test_mass_reads_material_and_physical_properties(monkeypatch):
    plate = SimpleNamespace(
        name="Plate", material=SimpleNamespace(name="PLA"),
        physicalProperties=SimpleNamespace(mass=0.0248, volume=20, density=0.00124),
    )
    root = root_with(bodies=[plate])

    env = v.mass(ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root)), "Plate")

    assert env.ok is True
    assert env.result["bodies"] == [{
        "path": "Plate", "name": "Plate", "mass_kg": 0.0248, "volume_cm3": 20,
        "material": "PLA", "density_kg_m3": 1240,
    }]


def test_body_not_found_surfaces_structured_error(monkeypatch):
    root = root_with(bodies=[SimpleNamespace(name="existing")])
    env = v.bounding_box(ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root)), "missing")

    assert env.ok is False
    assert env.error == "body_not_found"
    assert env.result == {
        "ok": False, "error": "body_not_found", "name": "missing", "matches": [],
    }


def test_unparseable_stdout_surfaces_parse_error():
    class MalformedAdapter:
        def execute_script(self, script, *, read_only=False):
            assert read_only is True
            return Envelope(ok=True, message="not json at all")

    env = v.volume(MalformedAdapter())
    assert env.ok is False
    assert env.error == "volume_parse_failed"
    assert env.message == "not json at all"


def _health_component(name, features, sketches=()):
    return SimpleNamespace(
        name=name, features=Collection(features), sketches=Collection(sketches),
    )


def _feature(name, health, detail=""):
    return SimpleNamespace(
        name=name, healthState=health, errorOrWarningMessage=detail, isSuppressed=False,
        classType=lambda: "adsk::fusion::FixtureEntity",
    )


@pytest.mark.parametrize("include_healthy", [False, True], ids=["skip_healthy", "include_healthy"])
def test_audit_reports_health_across_components(monkeypatch, include_healthy):
    nested = _health_component(
        "Leaf", [_feature("Cut", 1, "cached geometry")], [_feature("Profile", 2, "missing edge")]
    )
    root = root_with(
        name="Root", features=Collection([_feature("Extrude", 0)]), sketches=Collection(),
        occurrences=[occurrence("Part:1+Leaf:1", component=nested)],
    )
    compute = Mock(return_value=True)
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root, computeAll=compute))

    env = v.audit_feature_health(adapter, include_healthy=include_healthy)

    assert env.ok is True
    compute.assert_called_once_with()
    assert env.result["recomputed"] is True
    assert all(row["classType"] == "FixtureEntity" for row in env.result["features"])
    assert env.result["summary"] == {
        "total": 3, "healthy": 1, "warning": 1, "failed": 1,
        "suppressed": 0, "rolled_back": 0, "unknown": 0,
    }
    features = [
        (row["path"], row["category"], row["healthLabel"], row["errorOrWarningMessage"])
        for row in env.result["features"]
    ]
    assert features == (
        ([("Root / Extrude", "feature", "healthy", "")] if include_healthy else []) + [
            ("Part:1+Leaf:1 / Cut", "feature", "warning", "cached geometry"),
            ("Part:1+Leaf:1 / Profile", "sketch", "failed", "missing edge"),
        ]
    )


def test_audit_no_active_design_surfaces_error(monkeypatch):
    env = v.audit_feature_health(ScriptAdapter(monkeypatch, None))
    assert env.ok is False
    assert env.error == "no_active_design"


def test_audit_summary_respects_component_filter(monkeypatch):
    leaf = _health_component('Leaf "quoted"', [_feature("Cut", 1)])
    root = root_with(
        name="Root", features=Collection([_feature("Extrude", 2)]), sketches=Collection(),
        occurrences=[occurrence("Part:2+Leaf:1", component=leaf)],
    )
    adapter = ScriptAdapter(
        monkeypatch, SimpleNamespace(rootComponent=root, computeAll=lambda: True)
    )

    env = v.audit_feature_health(adapter, component_name='Leaf "quoted"')

    assert env.ok is True
    assert env.result["summary"] == {
        "total": 1, "healthy": 0, "warning": 1, "failed": 0,
        "suppressed": 0, "rolled_back": 0, "unknown": 0,
    }
    assert [row["path"] for row in env.result["features"]] == ["Part:2+Leaf:1 / Cut"]


def test_audit_stops_if_recompute_fails(monkeypatch):
    # The design deliberately has no rootComponent to catch scans after failure.
    compute = Mock(return_value=False)
    env = v.audit_feature_health(
        ScriptAdapter(monkeypatch, SimpleNamespace(computeAll=compute))
    )

    compute.assert_called_once_with()
    assert env.ok is False
    assert env.error == "recompute_failed"
