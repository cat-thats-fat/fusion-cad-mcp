"""Execute assembly scripts against local API doubles and check the requested calls."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.tools import assembly as asm
from tests._assembly_fakes import (
    Collection,
    NoScriptAdapter,
    ScriptAdapter,
    occurrence,
    point,
    root_with,
)


def test_bodies_to_components_renames_component_and_restores_body_name(monkeypatch):
    bodies = [SimpleNamespace(name=name) for name in ("plate", "bracket")]
    replacements = [
        SimpleNamespace(name="Body1", parentComponent=SimpleNamespace(name="Default"))
        for _ in bodies
    ]
    for body, replacement in zip(bodies, replacements, strict=True):
        body.createComponent = Mock(return_value=replacement)
    root = root_with(bodies=bodies, occurrences=())
    root.occurrences = Collection(replacements)
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root))

    env = asm.bodies_to_components(adapter, {"plate": "plate_comp", "bracket": "bracket_comp"})

    assert env.ok is True
    assert env.result["converted"] == [
        {"from_body": "plate", "to_component": "plate_comp"},
        {"from_body": "bracket", "to_component": "bracket_comp"},
    ]
    assert [b.name for b in replacements] == ["plate", "bracket"]
    assert [b.parentComponent.name for b in replacements] == ["plate_comp", "bracket_comp"]
    for body in bodies:
        body.createComponent.assert_called_once_with()


def test_bodies_to_components_short_circuits_on_empty():
    env = asm.bodies_to_components(NoScriptAdapter(), {})
    assert env.ok is False
    assert env.error == "invalid_input"
    assert "non-empty dict" in env.message


def test_bodies_to_components_preflights_later_ambiguous_selector(monkeypatch):
    plate = SimpleNamespace(name="plate", createComponent=Mock())
    bracket = SimpleNamespace(name="bracket", createComponent=Mock())
    other_bracket = SimpleNamespace(name="bracket", createComponent=Mock())
    root = root_with(bodies=[plate, bracket], occurrences=[occurrence("Part:1", bodies=[other_bracket])])
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root))

    env = asm.bodies_to_components(adapter, {"plate": "Plate", "bracket": "Bracket"})

    assert env.error == "body_ambiguous"
    assert env.result["matches"] == ["bracket", "Part:1 / bracket"]
    plate.createComponent.assert_not_called()
    bracket.createComponent.assert_not_called()
    other_bracket.createComponent.assert_not_called()


class TranslationMatrix:
    """Models translation only, using a detached value when read from an occurrence."""

    def __init__(self, x=0, y=0, z=0):
        self.translation = point(x, y, z)

    def transformBy(self, other):
        a, b = self.translation, other.translation
        self.translation = point(a.x + b.x, a.y + b.y, a.z + b.z)

    def asArray(self):
        p = self.translation
        return [1, 0, 0, p.x, 0, 1, 0, p.y, 0, 0, 1, p.z, 0, 0, 0, 1]


class MovableOccurrence:
    name = "Plate:1"
    fullPathName = "Plate:1"
    component = SimpleNamespace(name="Plate")

    def __init__(self, **ground_flags):
        self._position = (1, 2, 3)
        self.writes = []
        self.__dict__.update(ground_flags)

    @property
    def transform2(self):
        return TranslationMatrix(*self._position)

    @transform2.setter
    def transform2(self, matrix):
        p = matrix.translation
        self._position = (p.x, p.y, p.z)
        self.writes.append(self._position)


def _move_adapter(monkeypatch, occ, snapshots, matrix_factory=TranslationMatrix):
    design = SimpleNamespace(rootComponent=root_with(occurrences=[occ]), snapshots=snapshots)
    return ScriptAdapter(
        monkeypatch,
        design,
        core={
            "Matrix3D": SimpleNamespace(create=matrix_factory),
            "Point3D": SimpleNamespace(create=point),
            "Vector3D": SimpleNamespace(create=point),
        },
    )


def test_move_translation_only_converts_mm_to_cm(monkeypatch):
    occ = MovableOccurrence()
    snapshots = SimpleNamespace(hasPendingSnapshot=False, add=Mock())
    adapter = _move_adapter(monkeypatch, occ, snapshots)
    env = asm.move_component(adapter, "Plate:1", translation_mm=[20, -10, 5])

    assert env.ok is True
    assert occ.writes == [(3, 1, 3.5)]
    assert env.result["before_translation_mm"] == [10, 20, 30]
    assert env.result["after_translation_mm"] == [30, 10, 35]
    assert env.result["rotation_axis"] is None
    assert env.result["rotation_angle_deg"] is None
    assert env.result["moved"] is True
    snapshots.add.assert_not_called()


def test_move_rotation_only_uses_radians(monkeypatch):
    rotation_calls = []

    class RotationInput(TranslationMatrix):
        def setToRotation(self, angle, axis, origin):
            rotation_calls.append((angle, vars(axis), vars(origin)))

    occ = MovableOccurrence()
    adapter = _move_adapter(
        monkeypatch, occ, SimpleNamespace(hasPendingSnapshot=False), RotationInput
    )
    env = asm.move_component(
        adapter, "Plate:1", rotation_axis=[0, 0, 1], rotation_angle_deg=90,
        rotation_origin_mm=[20, -10, 5],
    )

    assert env.ok is True
    assert rotation_calls == [(math.pi / 2, vars(point(z=1)), vars(point(2, -1, 0.5)))]
    assert env.result["rotation_axis"] == [0, 0, 1]
    assert env.result["rotation_angle_deg"] == 90


@pytest.mark.parametrize("ground_flag", ["isGroundToParent", "isGrounded"])
def test_move_rejects_grounded_occurrence_without_writing_transform(monkeypatch, ground_flag):
    occ = MovableOccurrence(**{ground_flag: True})
    snapshots = SimpleNamespace(hasPendingSnapshot=True, add=Mock())
    adapter = _move_adapter(monkeypatch, occ, snapshots)
    env = asm.move_component(adapter, "Plate:1", translation_mm=[10, 0, 0])

    assert env.ok is False
    assert env.error == "occurrence_grounded"
    assert getattr(occ, ground_flag) is True
    assert occ.writes == []
    snapshots.add.assert_not_called()


def test_move_preserves_existing_pending_transforms(monkeypatch):
    occ = MovableOccurrence()
    snapshots = SimpleNamespace(hasPendingSnapshot=True, add=Mock(), revertPendingSnapshot=Mock())
    adapter = _move_adapter(monkeypatch, occ, snapshots)

    env = asm.move_component(adapter, "Plate:1", translation_mm=[10, 0, 0])

    assert env.error == "pending_assembly_transforms"
    assert not occ.writes and snapshots.hasPendingSnapshot
    snapshots.add.assert_not_called()
    snapshots.revertPendingSnapshot.assert_not_called()


def test_move_reverts_failed_transform_without_capturing_it(monkeypatch):
    snapshots = SimpleNamespace(hasPendingSnapshot=False, add=Mock())

    class PartiallyConstrained(MovableOccurrence):
        @MovableOccurrence.transform2.setter
        def transform2(self, matrix):
            p = matrix.translation
            MovableOccurrence.transform2.fset(self, TranslationMatrix(p.x - 0.5, p.y, p.z))
            snapshots.hasPendingSnapshot = True

    occ = PartiallyConstrained()
    before = occ.transform2

    def revert():
        MovableOccurrence.transform2.fset(occ, before)
        snapshots.hasPendingSnapshot = False
        return True

    snapshots.revertPendingSnapshot = Mock(side_effect=revert)
    adapter = _move_adapter(monkeypatch, occ, snapshots)
    env = asm.move_component(adapter, "Plate:1", translation_mm=[10, 0, 0])

    assert env.error == "move_not_achieved" and env.result["rolled_back"] is True
    assert occ.writes == [(1.5, 2, 3), (1, 2, 3)]
    assert not snapshots.hasPendingSnapshot
    snapshots.add.assert_not_called()
    snapshots.revertPendingSnapshot.assert_called_once_with()


@pytest.mark.parametrize("solver_overrides", [False, True])
def test_move_reports_snapshot_reverting_requested_transform(monkeypatch, solver_overrides):
    snapshots = SimpleNamespace(hasPendingSnapshot=False, revertPendingSnapshot=Mock())

    class CaptureOccurrence(MovableOccurrence):
        @MovableOccurrence.transform2.setter
        def transform2(self, matrix):
            MovableOccurrence.transform2.fset(self, matrix)
            snapshots.hasPendingSnapshot = True

    occ = CaptureOccurrence()
    before = occ.transform2
    snapshot = SimpleNamespace(deleteMe=Mock(return_value=True))

    def capture():
        if solver_overrides:
            MovableOccurrence.transform2.fset(occ, before)
        snapshots.hasPendingSnapshot = False
        return snapshot

    snapshots.add = Mock(side_effect=capture)
    adapter = _move_adapter(monkeypatch, occ, snapshots)
    env = asm.move_component(adapter, "Plate:1", translation_mm=[10, 0, 0])

    snapshots.add.assert_called_once_with()
    snapshots.revertPendingSnapshot.assert_not_called()
    if not solver_overrides:
        assert env.ok and env.result["moved"]
        assert occ.writes == [(2, 2, 3)]
        snapshot.deleteMe.assert_not_called()
        return
    assert occ.writes == [(2, 2, 3), (1, 2, 3)]
    assert env.ok is False
    assert env.error == "move_not_achieved"
    assert env.result["rolled_back"] is True
    assert env.result["before_transform"] == env.result["after_transform"]
    assert env.result["expected_transform"] != env.result["after_transform"]
    snapshot.deleteMe.assert_called_once_with()


def test_move_rejects_axis_without_angle():
    with pytest.raises(ValueError, match="rotation_axis requires rotation_angle_deg"):
        asm.build_move_component("p", rotation_axis=[0, 0, 1])


def test_move_rejects_nothing():
    with pytest.raises(ValueError, match="must provide at least"):
        asm.build_move_component("p")


def test_move_rejects_bad_translation_length():
    with pytest.raises(ValueError, match="translation_mm must be"):
        asm.build_move_component("p", translation_mm=[10, 5])


@pytest.mark.parametrize("outcome", ["pinned", "ignored", "moved", "parent_grounded", "both_grounded"])
def test_ground_pins_selected_occurrence_in_place(monkeypatch, outcome):
    class PinOccurrence(MovableOccurrence):
        def __init__(self):
            super().__init__()
            self._position = (12.2, 3, 0)
            self._pinned = outcome == "both_grounded"
            self.pin_writes = []

        @property
        def isGroundToParent(self):
            return outcome in {"parent_grounded", "both_grounded"}

        @isGroundToParent.setter
        def isGroundToParent(self, value):
            raise AssertionError("Pin must not set Ground To Parent")

        @property
        def isGrounded(self):
            return self._pinned

        @isGrounded.setter
        def isGrounded(self, value):
            self.pin_writes.append(value)
            if outcome != "ignored":
                self._pinned = value
            if outcome == "moved":
                self._position = (12, 3, 0)

    chosen = PinOccurrence()
    before = chosen.transform2.asArray()
    other = occurrence("Plate:2", component=chosen.component, isGrounded=False, isGroundToParent=False)
    root = root_with(occurrences=[other, chosen])
    env = asm.ground_component(ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root)), "Plate:1")

    assert not other.isGrounded and not other.isGroundToParent
    if outcome in {"parent_grounded", "both_grounded"}:
        assert not env.ok and env.error == "occurrence_grounded_to_parent"
        assert not chosen.pin_writes
        return
    assert chosen.pin_writes == [True]
    assert env.result["isGroundToParent"] is False
    if outcome == "pinned":
        assert env.ok and env.result["isGrounded"]
        assert chosen.transform2.asArray() == before
    elif outcome == "ignored":
        assert not env.ok and env.error == "grounding_change_not_achieved"
        assert env.result["isGrounded"] is False
    else:
        assert not env.ok and env.error == "pin_changed_transform"
        assert env.result["before_transform"] == before
        assert env.result["after_transform"] == chosen.transform2.asArray()
        assert env.result["model_may_have_changed"] is True


@pytest.mark.parametrize("pinned", [False, True])
def test_unground_clears_both_flags_on_selected_occurrence(monkeypatch, pinned):
    chosen = occurrence("Plate:1", isGrounded=pinned, isGroundToParent=True)
    other = occurrence("Plate:2", component=chosen.component, isGrounded=True, isGroundToParent=True)
    root = root_with(occurrences=[other, chosen])
    env = asm.unground_component(ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root)), "Plate:1")

    assert env.ok and not env.result["isGrounded"] and not env.result["isGroundToParent"]
    assert not chosen.isGrounded and not chosen.isGroundToParent
    assert other.isGrounded and other.isGroundToParent


def test_rigid_group_requires_at_least_2_components():
    with pytest.raises(ValueError, match="at least 2 component names"):
        asm.build_create_rigid_group(["only_one"])


@pytest.mark.parametrize("fail", [False, True], ids=["created", "rejected"])
def test_rigid_group_passes_selected_occurrences(monkeypatch, fail):
    first, second = occurrence("Part:1"), occurrence("Part:2")
    rigid_group = SimpleNamespace(name="Default", occurrences=Collection([first, second]))
    add = Mock(
        return_value=rigid_group, side_effect=RuntimeError("over-constrained") if fail else None
    )
    root = root_with(occurrences=[first, second], rigidGroups=SimpleNamespace(add=add, count=0, item=lambda i: None))
    adapter = ScriptAdapter(
        monkeypatch, SimpleNamespace(rootComponent=root),
        core={"ObjectCollection": SimpleNamespace(create=Collection)},
    )

    env = asm.create_rigid_group(adapter, ["Part:2", "Part:1"], name="locked_pair")

    add.assert_called_once()
    members, include_children = add.call_args.args
    assert include_children is True
    assert members.items == [second, first]
    if fail:
        assert env.ok is False
        assert env.error == "rigid_group_add_failed"
        assert env.result["detail"] == "over-constrained"
    else:
        assert env.ok is True
        assert env.result["rigid_group_name"] == rigid_group.name == "locked_pair"


def test_contact_set_requires_at_least_2_bodies():
    with pytest.raises(ValueError, match="at least 2 body names"):
        asm.build_create_contact_set(["solo"])


def test_contact_set_uses_selected_occurrence_body(monkeypatch):
    first, second = SimpleNamespace(name="Body"), SimpleNamespace(name="Body")
    add = Mock(return_value=SimpleNamespace(name="Contact"))
    root = root_with(
        occurrences=[occurrence("Part:1", bodies=[first]), occurrence("Part:2", bodies=[second])]
    )
    adapter = ScriptAdapter(
        monkeypatch, SimpleNamespace(rootComponent=root, contactSets=SimpleNamespace(add=add))
    )
    selectors = ["Part:2 / Body", "Part:1 / Body"]

    env = asm.create_contact_set(adapter, selectors)

    assert env.ok is True
    assert env.result["contact_set_name"] == "Contact"
    assert env.result["bodies"] == selectors
    add.assert_called_once()
    selected = add.call_args.args[0]
    assert selected[0] is second
    assert selected[1] is first


def test_interference_requires_at_least_2():
    with pytest.raises(ValueError, match="at least 2 entity names"):
        asm.build_interference_check(["one_body"])


def test_interference_reports_per_pair_volume(monkeypatch):
    plate, bracket = SimpleNamespace(name="plate"), occurrence("Bracket:1")
    inputs = SimpleNamespace(areCoincidentFacesIncluded=True)
    create_input = Mock(return_value=inputs)
    analyze = Mock(return_value=Collection([
        SimpleNamespace(
            entityOne=plate, entityTwo=bracket, interferenceBody=SimpleNamespace(volume=0.125)
        ),
    ]))
    root = root_with(bodies=[plate], occurrences=[bracket])
    adapter = ScriptAdapter(
        monkeypatch,
        SimpleNamespace(rootComponent=root, createInterferenceInput=create_input,
                        analyzeInterference=analyze),
        core={"ObjectCollection": SimpleNamespace(create=Collection)},
    )

    env = asm.interference_check(adapter, ["plate", "Bracket:1"])

    assert env.ok is True
    create_input.assert_called_once()
    assert create_input.call_args.args[0].items == [plate, bracket]
    analyze.assert_called_once_with(inputs)
    assert inputs.areCoincidentFacesIncluded is False
    assert env.result["pair_count"] == 1
    assert env.result["interference_pairs"] == [{
        "entity_one": "plate", "entity_two": "Bracket:1",
        "interference_volume_cm3": 0.125, "interference_volume_mm3": 125,
    }]


def test_ground_requires_name():
    env = asm.ground_component(NoScriptAdapter(), "")
    assert env.ok is False
    assert env.error == "invalid_input"


def test_rigid_group_preflight_bounds_and_deduplicates_expansion(monkeypatch):
    parent = occurrence("Parent:1", assemblyContext=None)
    child = occurrence("Parent:1+Child:1", assemblyContext=parent)
    other = occurrence("Other:1", assemblyContext=None)
    add = Mock()
    root = root_with(occurrences=[parent, child, other], rigidGroups=SimpleNamespace(add=add, count=0))
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root), core={"ObjectCollection": SimpleNamespace(create=Collection)})
    result = asm.create_rigid_group(adapter, [parent.fullPathName, child.fullPathName], preview=True)
    assert result.ok and result.result["selected_count"] == 2 and result.result["expanded_count"] == 2
    result = asm.create_rigid_group(adapter, [parent.fullPathName, other.fullPathName], max_occurrences=2)
    assert result.error == "max_occurrences_exceeded" and result.result["expanded_count"] == 3
    assert result.result["mutation_state"] == "not_started"
    assert asm.create_rigid_group(adapter, [parent.fullPathName, parent.name], preview=True).error == "duplicate_occurrence"
    add.assert_not_called()


def test_rigid_group_name_conflict_and_existing_membership(monkeypatch):
    one, two = occurrence("One:1"), occurrence("Two:1")
    group = SimpleNamespace(name="locked", occurrences=Collection([one, two]), isSuppressed=True)
    groups = Collection([group])
    root = root_with(occurrences=[one, two], rigidGroups=groups)
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root))
    assert asm.create_rigid_group(adapter, [one.name, two.name], name="locked").error == "rigid_group_name_exists"
    page = asm.inspect_rigid_group(adapter, "locked", limit=1)
    assert page.ok and page.result["actual_count"] == 2 and page.result["is_suppressed"] is True
    assert page.result["items"] == [one.fullPathName] and page.result["next_offset"] == 1


def test_rigid_group_without_children_reports_actual_members(monkeypatch):
    one, two = occurrence("One:1"), occurrence("Two:1")
    group = SimpleNamespace(name="group", occurrences=Collection([one, two]), isSuppressed=False)
    add = Mock(return_value=group)
    groups = SimpleNamespace(count=0, add=add)
    root = root_with(occurrences=[one, two], rigidGroups=groups)
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root), core={"ObjectCollection": SimpleNamespace(create=Collection)})
    result = asm.create_rigid_group(adapter, [one.name, two.name], include_children=False)
    assert result.ok and result.result["expanded_count"] == result.result["actual_count"] == 2
    assert add.call_args.args[1] is False


@pytest.mark.parametrize("actual,error", [(None, "rigid_group_membership_unverified"), (1, "rigid_group_membership_mismatch")])
def test_rigid_group_post_add_membership_is_not_silently_accepted(monkeypatch, actual, error):
    one, two = occurrence("One:1"), occurrence("Two:1")
    group = SimpleNamespace(name="created")
    if actual is not None:
        group.occurrences = Collection([one] * actual)
    add = Mock(return_value=group)
    root = root_with(occurrences=[one, two], rigidGroups=SimpleNamespace(count=0, add=add))
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root), core={"ObjectCollection": SimpleNamespace(create=Collection)})
    result = asm.create_rigid_group(adapter, [one.name, two.name], include_children=False)
    assert result.error == error and result.result["mutation_state"] == "unknown"
    assert result.result["rigid_group_name"] == "created" and result.result["actual_count"] == actual
    add.assert_called_once()


def test_rigid_group_selector_refusal_is_not_started(monkeypatch):
    one = occurrence("One:1")
    root = root_with(occurrences=[one], rigidGroups=SimpleNamespace(count=0, add=Mock()))
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root))
    result = asm.create_rigid_group(adapter, [one.name, "missing"])
    assert result.error == "occurrence_not_found" and result.result["mutation_state"] == "not_started"


def test_rigid_group_outcome_receipt_barrier_semantics():
    from fusion_cad_mcp.receipts import _unresolved

    assert not _unresolved({"terminal": {"declared_result": {"ok": False, "mutation_state": "not_started"}}})
    assert _unresolved({"terminal": {"declared_result": {"ok": False, "mutation_state": "unknown"}}})
