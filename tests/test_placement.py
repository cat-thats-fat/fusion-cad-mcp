import contextlib
import io
import math
import sys
import types
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import placement
from tests._assembly_fakes import Collection, NoScriptAdapter


class Vector:
    def __init__(self, x, y, z, is_point=False):
        self.x, self.y, self.z, self.is_point = x, y, z, is_point

    def crossProduct(self, other):
        return Vector(self.y*other.z-self.z*other.y, self.z*other.x-self.x*other.z, self.x*other.y-self.y*other.x)

    def transformBy(self, matrix):
        m = matrix.values
        x, y, z = self.x, self.y, self.z
        t = 1 if self.is_point else 0
        self.x, self.y, self.z = (m[0]*x+m[1]*y+m[2]*z+m[3]*t, m[4]*x+m[5]*y+m[6]*z+m[7]*t, m[8]*x+m[9]*y+m[10]*z+m[11]*t)
        return True


class Matrix:
    def __init__(self, values=None):
        self.values = list(values or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])

    def asArray(self):
        return list(self.values)

    def setWithCoordinateSystem(self, p, x, y, z):
        self.values = [x.x, y.x, z.x, p.x, x.y, y.y, z.y, p.y, x.z, y.z, z.z, p.z, 0, 0, 0, 1]
        return True


class Occurrence:
    def __init__(self, component, path, snapshots, *, context=None):
        self.component, self.fullPathName = component, path
        self.name, self.entityToken = path, "tok" + path.replace(":", "")
        self.assemblyContext, self.nativeObject = context, None
        self.isReferencedComponent = self.isGrounded = self.isGroundToParent = False
        self.isValid = True
        self._matrix = Matrix()
        self.snapshots = snapshots
        self.attributes = Attributes()

    @property
    def transform2(self):
        return Matrix(self._matrix.values)

    @transform2.setter
    def transform2(self, matrix):
        self._matrix = Matrix(matrix.values)
        self.snapshots.hasPendingSnapshot = True

    def deleteMe(self):
        self.isValid = False
        return True


class Attributes:
    def __init__(self):
        self.items = {}

    def itemByName(self, group, name):
        return self.items.get((group, name))

    def add(self, group, name, value):
        self.items[group, name] = NS(value=value)
        return self.items[group, name]


class FakeTx:
    def __init__(self, design, owner):
        self.created = []

    def track(self, obj):
        if obj is None:
            raise RuntimeError("creation returned None")
        self.created.append(obj)
        return obj

    def verify(self):
        return {"recomputed": True}

    def rollback(self):
        for obj in self.created:
            obj.deleteMe()
        return {"status": "verified"}


class Adapter:
    def __init__(self, monkeypatch):
        self.snapshots = NS(hasPendingSnapshot=False, add=Mock(), revertPendingSnapshot=Mock(return_value=True))
        self.snapshots.add.side_effect = self.commit
        self.root = NS(name="Root")
        self.root.occurrences = Collection()
        self.root.allOccurrences = self.root.occurrences
        self.root.bRepBodies = Collection()
        self.root.features = Collection()
        self.root.sketches = Collection()
        self.components = Collection([self.root])
        self.design = NS(rootComponent=self.root, allComponents=self.components, designType=1,
                         timeline=NS(markerPosition=0, count=0), snapshots=self.snapshots)
        self.document = NS(creationId="doc", dataFile=None)
        self.app = NS(activeProduct=self.design, activeDocument=self.document)
        modules = {name: types.ModuleType(name) for name in ("adsk", "adsk.core", "adsk.fusion")}
        modules["adsk"].core, modules["adsk"].fusion = modules["adsk.core"], modules["adsk.fusion"]
        modules["adsk.core"].Application = NS(get=lambda: self.app)
        modules["adsk.core"].Matrix3D = NS(create=Matrix)
        modules["adsk.core"].Point3D = NS(create=lambda x,y,z: Vector(x,y,z,True))
        modules["adsk.core"].Vector3D = NS(create=Vector)
        modules["adsk.fusion"].Design = NS(cast=lambda obj: obj)
        modules["adsk.fusion"].DesignTypes = NS(ParametricDesignType=1)
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)

    def commit(self):
        self.snapshots.hasPendingSnapshot = False
        return NS(deleteMe=Mock(return_value=True))

    def execute_script(self, script):
        namespace = {}
        output = io.StringIO()
        self.script_output = output
        with contextlib.redirect_stdout(output):
            exec(script, namespace)
            namespace["_Mutation"] = FakeTx
            namespace["run"](None)
        return Envelope(ok=True, message=output.getvalue())

    def add_component(self, name):
        comp = NS(name=name, occurrences=Collection())
        self.components.items.append(comp)
        occ = Occurrence(comp, name + ":1", self.snapshots)
        self.root.occurrences.items.append(occ)
        return occ

    def install_inserter(self):
        def insert(comp, matrix):
            occ = Occurrence(comp, comp.name + ":2", self.snapshots)
            self.root.occurrences.items.append(occ)
            return occ
        def copy(comp, matrix):
            new = NS(name=comp.name + " copy", occurrences=Collection())
            self.components.items.append(new)
            occ = Occurrence(new, new.name + ":1", self.snapshots)
            self.root.occurrences.items.append(occ)
            return occ
        self.root.occurrences.addExistingComponent = Mock(side_effect=insert)
        self.root.occurrences.addNewComponentCopy = Mock(side_effect=copy)


def test_absolute_placement_converts_mm_and_checks_snapshot(monkeypatch):
    adapter = Adapter(monkeypatch)
    occ = adapter.add_component("Board")
    env = placement.place_component(adapter, "Board:1", [10, 20, 30], [0, 1, 0], [-1, 0, 0])
    assert env.ok and env.result["changed"] and env.result["snapshot_created"]
    assert occ.transform2.asArray() == [0, -1, 0, 1, 1, 0, 0, 2, 0, 0, 1, 3, 0, 0, 0, 1]
    assert env.result["occurrence"]["handle"].startswith("occurrence:Board:1|doc=646f63:")
    adapter.snapshots.add.assert_called_once()


def test_reference_pose_rotates_offset_and_basis(monkeypatch):
    adapter = Adapter(monkeypatch)
    target, ref = adapter.add_component("Board"), adapter.add_component("Frame")
    ref._matrix = Matrix([0, -1, 0, 5, 1, 0, 0, 7, 0, 0, 1, 0, 0, 0, 0, 1])
    result = placement.place_component(adapter, "Board:1", [10, 0, 0], relative_to="Frame:1")
    assert result.ok
    assert target.transform2.asArray() == [0, -1, 0, 5, 1, 0, 0, 8, 0, 0, 1, 0, 0, 0, 0, 1]


def test_grounded_or_pending_refused_before_mutation(monkeypatch):
    adapter = Adapter(monkeypatch)
    occ = adapter.add_component("Board")
    occ.isGrounded = True
    assert placement.place_component(adapter, "Board:1", [1, 2, 3]).error == "occurrence_grounded"
    occ.isGrounded = False
    adapter.snapshots.hasPendingSnapshot = True
    assert placement.place_component(adapter, "Board:1", [1, 2, 3]).error == "pending_assembly_transforms"
    assert occ.transform2.asArray() == Matrix().asArray()


@pytest.mark.parametrize("ref_path", ["Carrier:1", "Carrier:1+Board:1"])
def test_placement_refuses_a_reference_frame_that_moves_with_the_target(monkeypatch, ref_path):
    adapter = Adapter(monkeypatch)
    carrier = adapter.add_component("Carrier")
    board = NS(name="Board", occurrences=Collection())
    child = Occurrence(board, "Carrier:1+Board:1", adapter.snapshots, context=carrier)
    adapter.root.allOccurrences.items.append(child)
    result = placement.place_component(adapter, "Carrier:1", [10, 0, 0], relative_to=ref_path)
    assert result.error == "self_relative_placement"
    assert carrier.transform2.asArray() == Matrix().asArray()
    adapter.snapshots.add.assert_not_called()


def test_oversized_integer_coordinates_return_invalid_input():
    result = placement.place_component(NoScriptAdapter(), "Board:1", [10**1000, 0, 0])
    assert result.error == "invalid_input"


def test_insert_retry_id_does_not_duplicate_and_never_renames_source(monkeypatch):
    adapter = Adapter(monkeypatch)
    source = adapter.add_component("Board")
    adapter.install_inserter()
    first = placement.insert_component(adapter, "Board:1", "insert-a")
    assert first.ok and source.component.name == "Board"
    assert first.result["occurrence"]["shared_definition"] is True
    second = placement.insert_component(adapter, "Board:1", "insert-a")
    assert second.error == "operation_id_exists"
    adapter.root.occurrences.addExistingComponent.assert_called_once()


def test_independent_copy_and_cycle_refusal(monkeypatch):
    adapter = Adapter(monkeypatch)
    source = adapter.add_component("Board")
    adapter.install_inserter()
    result = placement.insert_component(adapter, "Board:1", "copy-a", independent=True)
    assert result.ok and result.result["occurrence"]["component_name"] == "copy-a"
    assert source.component.name == "Board"
    result = placement.insert_component(adapter, "Board:1", "loop", parent="Board:1")
    assert result.error == "component_cycle_unsupported"


def test_creation_refuses_duplicate_name(monkeypatch):
    adapter = Adapter(monkeypatch)
    adapter.add_component("Board")
    assert placement.create_component(adapter, "Board").error == "component_name_taken"


@pytest.mark.parametrize("x,y", [([1, 0, 0], [1, 0, 0]), ([2, 0, 0], [0, 1, 0]),
                                 ([math.nan, 0, 0], [0, 1, 0]), ([True, 0, 0], [0, 1, 0])])
def test_invalid_basis_refused_before_dispatch(x, y):
    with pytest.raises(ValueError):
        placement.build_place_component("a", [0, 0, 0], x, y)


def test_move_nested_occurrence_with_shared_parent_is_refused(monkeypatch):
    adapter = Adapter(monkeypatch)
    parent1 = adapter.add_component("Assembly")
    parent2 = Occurrence(parent1.component, "Assembly:2", adapter.snapshots)
    child_component = NS(name="Part", occurrences=Collection())
    nested = Occurrence(child_component, "Assembly:1+Part:1", adapter.snapshots, context=parent1)
    adapter.root.allOccurrences.items.extend([parent2, nested])
    result = placement.place_component(adapter, "Assembly:1+Part:1", [1, 2, 3])
    assert result.error == "shared_component_edit_unsupported"
    assert nested.transform2.asArray() == Matrix().asArray()


def test_snapshot_failure_reports_unproven_commit(monkeypatch):
    adapter = Adapter(monkeypatch)
    occ = adapter.add_component("Board")
    adapter.snapshots.add.side_effect = RuntimeError("failed during commit")
    def revert():
        occ._matrix = Matrix()
        adapter.snapshots.hasPendingSnapshot = False
        return True
    adapter.snapshots.revertPendingSnapshot.side_effect = revert
    result = placement.place_component(adapter, "Board:1", [10, 0, 0])
    assert not result.ok
    assert result.result["rollback"]["pose_restored"]
    assert result.result["rollback"]["status"] == "unknown"


def test_independent_copy_rejects_existing_operation_name(monkeypatch):
    adapter = Adapter(monkeypatch)
    adapter.add_component("Board")
    adapter.add_component("copy-a")
    adapter.install_inserter()
    result = placement.insert_component(adapter, "Board:1", "copy-a", independent=True)
    assert result.error == "component_name_taken"
    adapter.root.occurrences.addNewComponentCopy.assert_not_called()


def test_insert_pending_snapshot_is_not_reported_as_verified_rollback(monkeypatch):
    adapter = Adapter(monkeypatch)
    adapter.add_component("Board")
    adapter.install_inserter()
    original = adapter.root.occurrences.addExistingComponent.side_effect
    def insert_with_pending(comp, matrix):
        result = original(comp, matrix)
        adapter.snapshots.hasPendingSnapshot = True
        return result
    adapter.root.occurrences.addExistingComponent.side_effect = insert_with_pending
    result = placement.insert_component(adapter, "Board:1", "insert-a")
    assert result.error == "component_insertion_failed"
    assert result.result["rollback"]["status"] == "failed"
    assert result.result["rollback"]["pending_snapshot"] is True


def test_occurrence_handle_can_be_used_for_placement(monkeypatch):
    adapter = Adapter(monkeypatch)
    occ = adapter.add_component("Board")
    occ.objectType = "adsk::fusion::Occurrence"
    occ.entityToken = "a" * 300
    adapter.app.documents = Collection([adapter.document])

    def lookup(token):
        assert sys.stdout is adapter.script_output
        return [occ] if token == occ.entityToken else []

    adapter.design.findEntityByToken = lookup
    handle = f"occurrence:Board:1|doc=646f63:{occ.entityToken}"
    result = placement.place_component(adapter, handle, [10, 0, 0])
    assert result.ok and result.result["occurrence"]["path"] == "Board:1"
    bad = handle.replace("646f63", "6f74686572")
    result = placement.place_component(adapter, bad, [0, 0, 0])
    assert result.error == "handle_invalid" and result.result["detail"]["reason"] == "document_scope_mismatch"
    result = placement.place_component(adapter, handle.rsplit(":", 1)[0] + ":missing", [0, 0, 0])
    assert result.error == "handle_invalid" and result.result["detail"]["reason"] == "not_found_in_active_doc"


def test_nested_occurrence_handle_uses_root_context_proxy_token(monkeypatch):
    adapter = Adapter(monkeypatch)
    parent = adapter.add_component("Carrier")
    component = NS(name="Board", occurrences=Collection())
    child = Occurrence(component, "Carrier:1+Board:1", adapter.snapshots, context=parent)
    child.objectType = "adsk::fusion::Occurrence"
    child.entityToken = "proxyToken"
    class NativeWithoutRootToken:
        @property
        def entityToken(self):
            raise RuntimeError("Tokens can only be created for proxies whose top-level parent is the root component")
    child.nativeObject = NativeWithoutRootToken()
    adapter.root.allOccurrences.items.append(child)
    adapter.app.documents = Collection([adapter.document])
    adapter.design.findEntityByToken = lambda token: [child] if token == "proxyToken" else []
    first = placement.place_component(adapter, child.fullPathName, [0, 0, 0])
    assert first.ok
    handle = first.result["occurrence"]["handle"]
    assert handle == "occurrence:Carrier:1+Board:1|occ=Carrier:1|doc=646f63:proxyToken"
    second = placement.place_component(adapter, handle, [0, 0, 0])
    assert second.ok and second.result["occurrence"]["path"] == child.fullPathName
