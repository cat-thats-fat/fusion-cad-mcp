"""Exercise reference metadata validation and generated scripts without Fusion."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fusion_cad_mcp.tools import references as r
from tests._assembly_fakes import Collection, ScriptAdapter, occurrence, point, root_with

BASE = {"schema_version": 1, "reference_id": "board-1", "units": "mm"}


def test_reference_metadata_reports_mesh_bounds_separately(monkeypatch):
    comp = root(Attributes(json.dumps(BASE)))
    comp.meshBodies = Collection([SimpleNamespace(name="Scan", boundingBox=SimpleNamespace(
        minPoint=point(1, 2, 3), maxPoint=point(4, 5, 6)))])
    result = r.get_reference_metadata(Adapter(monkeypatch, comp), "root")
    assert result.ok and result.result["mesh_count"] == 1
    assert result.result["body_count"] == 0
    assert result.result["mesh_bounds_frame"] == "root"
    assert result.result["mesh_bounds"] == [{"mesh": "Scan", "min_mm": [10, 20, 30],
                                              "max_mm": [40, 50, 60]}]


class Attributes:
    def __init__(self, value=None, *, fail_write=False, fail_restore=False):
        self.value = value
        self.fail_write = fail_write
        self.fail_restore = fail_restore
        self.calls = 0

    def itemByName(self, group, key):
        if self.value is None:
            return None
        attrs = self

        class Attribute:
            @property
            def value(self):
                return attrs.value

            def deleteMe(self):
                if attrs.fail_restore:
                    return False
                attrs.value = None
                return True

        return Attribute()

    def add(self, group, key, value):
        self.calls += 1
        if self.calls == 1 and self.fail_write:
            self.value = "corrupt"
            raise RuntimeError("write failed after mutation")
        if self.calls > 1 and self.fail_restore:
            raise RuntimeError("restore failed")
        self.value = value
        return self.itemByName(group, key)


class Adapter(ScriptAdapter):
    def __init__(self, monkeypatch, root):
        super().__init__(monkeypatch, SimpleNamespace(rootComponent=root))
        self.flags = []

    def execute_script(self, script, *, read_only=False):
        self.flags.append(read_only)
        return super().execute_script(script)


def root(attrs=None, bodies=()):
    return root_with(name="Root", attributes=attrs or Attributes(), bodies=bodies)


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {**BASE, "unknown": 1},
        {**BASE, "schema_version": True},
        {**BASE, "reference_id": "../bad"},
        {**BASE, "source_sha256": "ABC"},
        {
            **BASE,
            "mounting_points": [
                {"id": "a", "position_mm": [0, 0, float("nan")], "axis": [0, 0, 1]}
            ],
        },
        {**BASE, "mounting_points": [{"id": "a", "position_mm": [0, 0, 0], "axis": [0, 0, 2]}]},
        {
            **BASE,
            "mounting_points": [
                {"id": "a", "position_mm": [0, 0, 0], "axis": [0, 0, 1], "hole_diameter_mm": 0}
            ],
        },
        {
            **BASE,
            "connectors": [
                {"id": "a", "origin_mm": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [1, 0, 0]}
            ],
        },
        {**BASE, "keepouts": [{"id": "a", "min_mm": [0, 0, 0], "max_mm": [1, 0, 1]}]},
        {
            **BASE,
            "mounting_points": [{"id": "a", "position_mm": [0, 0, 0], "axis": [0, 0, 1]}],
            "keepouts": [{"id": "a", "min_mm": [0, 0, 0], "max_mm": [1, 1, 1]}],
        },
    ],
)
def test_invalid_metadata_rejected_without_dispatch(monkeypatch, metadata):
    adapter = Adapter(monkeypatch, root())
    result = r.set_reference_metadata(adapter, "root", metadata)
    assert not result.ok and result.error == "invalid_input"
    assert adapter.flags == []


def test_set_get_and_readonly(monkeypatch):
    attrs = Attributes()
    adapter = Adapter(monkeypatch, root(attrs))
    created = r.set_reference_metadata(adapter, "root", BASE)
    again = r.set_reference_metadata(adapter, "root", BASE)
    loaded = r.get_reference_metadata(adapter, "root")
    assert created.ok and created.result["changed"] is True
    assert again.ok and again.result["changed"] is False
    assert loaded.ok and loaded.result["metadata"] == BASE
    assert loaded.result["metadata_frame"] == "component_local"
    assert attrs.calls == 1
    assert adapter.flags == [False, False, True]


def test_nested_occurrence_below_external_reference_refuses_attribute_edit(monkeypatch):
    attrs = Attributes()
    parent = occurrence("Linked:1", isReferencedComponent=True)
    child_comp = SimpleNamespace(name="Board", attributes=attrs)
    child = occurrence("Linked:1+Board:1", component=child_comp,
                       isReferencedComponent=False, assemblyContext=parent)
    rt = root()
    rt.allOccurrences = Collection([parent, child])
    result = r.set_reference_metadata(Adapter(monkeypatch, rt), child.fullPathName, BASE)
    assert result.error == "referenced_component_edit_unsupported"
    assert attrs.calls == 0


def test_missing_optional_bounds_preserve_verified_attribute_write(monkeypatch):
    attrs = Attributes()
    adapter = Adapter(monkeypatch, root(attrs, bodies=[SimpleNamespace(name="Unavailable body")]))
    result = r.set_reference_metadata(adapter, "root", BASE)
    assert result.ok and result.result["changed"]
    assert result.result["body_bounds"] == [] and result.result["body_bounds_error"]
    assert r.get_reference_metadata(adapter, "root").result["metadata"] == BASE


def test_shared_component_refuses_edit_before_attribute_write(monkeypatch):
    attrs = Attributes()
    comp = SimpleNamespace(name="Board", attributes=attrs)
    one = occurrence("Board:1", component=comp, isReferencedComponent=False)
    two = occurrence("Board:2", component=comp, isReferencedComponent=False)
    rt = root(attrs)
    rt.allOccurrences = Collection([one, two])
    result = r.set_reference_metadata(Adapter(monkeypatch, rt), "Board:1", BASE)
    assert result.error == "shared_component_edit_unsupported"
    assert attrs.calls == 0


@pytest.mark.parametrize("existing", [None, json.dumps(BASE)])
@pytest.mark.parametrize("fail_restore", [False, True])
def test_failed_write_reports_actual_attribute_restore(monkeypatch, existing, fail_restore):
    attrs = Attributes(existing, fail_write=True, fail_restore=fail_restore)
    adapter = Adapter(monkeypatch, root(attrs))
    out = r.set_reference_metadata(adapter, "root", {**BASE, "reference_id": "new"})
    assert out.error == "reference_metadata_write_failed"
    assert out.result["rollback"]["status"] == ("failed" if fail_restore else "verified")
    assert (attrs.value == existing) is (not fail_restore)


def test_occurrence_bounds_and_matrix_frames_are_explicit(monkeypatch):
    attrs = Attributes(json.dumps(BASE))
    native = SimpleNamespace(name="board", attributes=attrs)
    box = SimpleNamespace(minPoint=point(2, 3, 4), maxPoint=point(3, 5, 6))
    body = SimpleNamespace(name="boardBody", boundingBox=box)
    occ = occurrence(
        "Board:1",
        component=native,
        bodies=[body],
        transform2=SimpleNamespace(asArray=lambda: list(range(16))),
        isReferencedComponent=False,
    )
    rt = root()
    rt.allOccurrences = Collection([occ])
    out = r.get_reference_metadata(Adapter(monkeypatch, rt), "Board:1")
    assert out.ok and out.result["body_bounds_frame"] == "root"
    assert out.result["body_bounds"][0]["min_mm"] == [20, 30, 40]
    assert out.result["component_to_root_matrix_cm"] == list(range(16))
    assert out.result["metadata_scope"] == "component_definition"


def test_referenced_component_refuses_edit(monkeypatch):
    attrs = Attributes()
    comp = SimpleNamespace(name="board", attributes=attrs)
    occ = occurrence("Board:1", component=comp, isReferencedComponent=True)
    rt = root()
    rt.allOccurrences = Collection([occ])
    out = r.set_reference_metadata(Adapter(monkeypatch, rt), "Board:1", BASE)
    assert out.error == "referenced_component_edit_unsupported"
    assert attrs.calls == 0


@pytest.mark.parametrize("stored", ["{not json", json.dumps({**BASE, "units": "in"})])
def test_get_rejects_bad_stored_metadata(monkeypatch, stored):
    adapter = Adapter(monkeypatch, root(Attributes(stored)))
    out = r.get_reference_metadata(adapter, "root")
    assert out.error == "reference_metadata_invalid"
    assert adapter.flags == [True]


def test_readback_mismatch_rolls_back_old_value(monkeypatch):
    class Mismatching(Attributes):
        def add(self, group, key, value):
            self.calls += 1
            self.value = "corrupt" if self.calls == 1 else value
            return self.itemByName(group, key)

    old = json.dumps(BASE)
    attrs = Mismatching(old)
    out = r.set_reference_metadata(
        Adapter(monkeypatch, root(attrs)), "root", {**BASE, "reference_id": "new"}
    )
    assert out.error == "reference_metadata_write_failed"
    assert out.result["rollback"]["status"] == "verified"
    assert attrs.value == old
