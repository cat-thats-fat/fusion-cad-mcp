"""Joint script dispatch and read-back tested with local API doubles."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.tools import assembly as asm
from tests._assembly_fakes import Collection, NoScriptAdapter, ScriptAdapter


def test_set_joint_limits_requires_at_least_one_value():
    with pytest.raises(ValueError, match="at least one"):
        asm.build_set_joint_limits("hinge")


def _motion_adapter(monkeypatch, motion, evaluate, is_valid=lambda expr, units: True):
    joint = SimpleNamespace(name="hinge", jointMotion=motion)
    return ScriptAdapter(
        monkeypatch,
        SimpleNamespace(
            rootComponent=SimpleNamespace(joints=Collection([joint])),
            unitsManager=SimpleNamespace(evaluateExpression=evaluate, isValidExpression=is_valid),
        ),
    )


def test_set_joint_limits_min_only(monkeypatch):
    limits = SimpleNamespace(
        isMinimumValueEnabled=False, minimumValue=0,
        isMaximumValueEnabled=True, maximumValue=1.5,
        isRestValueEnabled=False, restValue=0.5,
    )
    evaluate = Mock(return_value=-math.pi / 4)
    env = asm.set_joint_limits(
        _motion_adapter(monkeypatch, SimpleNamespace(rotationLimits=limits), evaluate),
        "hinge", min_value="-45 deg",
    )

    assert env.ok is True
    evaluate.assert_called_once_with("-45 deg", "deg")
    assert limits.minimumValue == -math.pi / 4
    assert limits.isMinimumValueEnabled is True
    assert limits.maximumValue == 1.5
    assert limits.restValue == 0.5
    assert env.result["applied"] == {
        "min_enabled": True, "min_value_internal": -math.pi / 4,
        "max_enabled": True, "max_value_internal": 1.5,
        "rest_enabled": False, "rest_value_internal": None,
    }


def test_set_joint_limits_min_max_rest(monkeypatch):
    limits = SimpleNamespace(
        isMinimumValueEnabled=False, isMaximumValueEnabled=False, isRestValueEnabled=False
    )
    evaluate = Mock(side_effect=[-1.2, 0.8, 0.2])
    env = asm.set_joint_limits(
        _motion_adapter(monkeypatch, SimpleNamespace(slideLimits=limits), evaluate),
        "hinge", min_value="-12 mm", max_value="8 mm", rest_value="2 mm",
    )

    assert env.ok is True
    assert [call.args for call in evaluate.call_args_list] == [
        ("-12 mm", "mm"), ("8 mm", "mm"), ("2 mm", "mm"),
    ]
    assert env.result["limits_kind"] == "slide"
    assert env.result["applied"] == {
        "min_enabled": True, "min_value_internal": -1.2,
        "max_enabled": True, "max_value_internal": 0.8,
        "rest_enabled": True, "rest_value_internal": 0.2,
    }


def test_set_joint_limits_preflights_later_invalid_expression(monkeypatch):
    limits = SimpleNamespace(isMinimumValueEnabled=False, minimumValue=0)
    evaluate = Mock(return_value=-1.2)
    validate = Mock(side_effect=[True, False])
    adapter = _motion_adapter(monkeypatch, SimpleNamespace(slideLimits=limits), evaluate, validate)

    env = asm.set_joint_limits(adapter, "hinge", min_value="-12 mm", max_value="8 deg")

    assert env.error == "expression_invalid" and env.result["expression"] == "8 deg"
    assert vars(limits) == {"isMinimumValueEnabled": False, "minimumValue": 0}
    evaluate.assert_called_once_with("-12 mm", "mm")


def test_drive_joint_requires_value():
    with pytest.raises(ValueError, match="value expression required"):
        asm.build_drive_joint("hinge", "")


@pytest.mark.parametrize(
    ("attr", "units", "expr", "requested"),
    [("rotationValue", "deg", "30 deg", math.pi / 6), ("slideValue", "mm", "12 mm", 1.2)],
    ids=["rotation", "slide"],
)
def test_drive_joint_uses_matching_units_and_readback(monkeypatch, attr, units, expr, requested):
    class LimitedMotion:
        def __init__(self):
            self.writes = []
            setattr(self, attr, 0.1)
            self.writes.clear()

        def __setattr__(self, name, value):
            if name == attr:
                self.writes.append(value)
                value = min(value, 0.2)
            super().__setattr__(name, value)

    motion = LimitedMotion()
    evaluate = Mock(return_value=requested)
    env = asm.drive_joint(_motion_adapter(monkeypatch, motion, evaluate), "hinge", expr)

    assert env.ok is True
    evaluate.assert_called_once_with(expr, units)
    assert motion.writes == [requested]
    assert env.result["drove"] == attr
    assert env.result["applied"] is False
    assert env.result["requested_internal"] == requested
    assert env.result["value_internal"] == 0.2


def _h(kind, token):
    return f"{kind}:body:{token}"


def _joint_adapter(monkeypatch, *, kind="face", fail=None):
    api_type = {"face": "BRepFace", "edge": "BRepEdge", "vertex": "BRepVertex", "point": "SketchPoint"}
    entities = {
        token: SimpleNamespace(objectType=f"adsk::fusion::{api_type[kind]}") for token in ("a", "b")
    }
    geometries = [object(), object()]
    geometry = SimpleNamespace(
        createByPlanarFace=Mock(
            side_effect=geometries if fail != "non_planar" else [None, None],
        ),
        createByNonPlanarFace=Mock(side_effect=geometries),
        createByCurve=Mock(side_effect=geometries),
        createByPoint=Mock(side_effect=geometries),
    )
    joint_input = SimpleNamespace(**{
        f"setAs{motion}JointMotion": Mock()
        for motion in ("Rigid", "Revolute", "Slider", "Cylindrical", "Ball")
    })
    joint = SimpleNamespace(name="Default", entityToken="joint-token")
    joints = SimpleNamespace(
        createInput=Mock(return_value=None if fail == "input_none" else joint_input),
        add=Mock(
            return_value=None if fail == "add_none" else joint,
            side_effect=RuntimeError("JointInput rejected") if fail == "add_exception" else None,
        ),
    )
    directions = SimpleNamespace(**{f"{a}AxisJointDirection": object() for a in "XYZ"})
    key_points = SimpleNamespace(CenterKeyPoint=object(), MiddleKeyPoint=object())
    adapter = ScriptAdapter(
        monkeypatch,
        SimpleNamespace(
            rootComponent=SimpleNamespace(joints=joints),
            findEntityByToken=lambda token: [entities[token]],
        ),
        core={"ValueInput": SimpleNamespace(createByString=lambda value: ("expression", value))},
        fusion={"JointGeometry": geometry, "JointDirections": directions,
                "JointKeyPointTypes": key_points},
    )
    return SimpleNamespace(
        adapter=adapter, geometry=geometry, entities=entities, geometries=geometries,
        joints=joints, joint_input=joint_input, joint=joint, directions=directions,
        key_points=key_points,
    )


@pytest.mark.parametrize(
    ("kind", "motion", "axis", "constructor", "key_point", "motion_axes"),
    [
        ("face", "rigid", "z", "createByPlanarFace", "CenterKeyPoint", ""),
        ("face", "revolute", "z", "createByPlanarFace", "CenterKeyPoint", "Z"),
        ("edge", "slider", "x", "createByCurve", "MiddleKeyPoint", "X"),
        ("face", "cylindrical", "y", "createByPlanarFace", "CenterKeyPoint", "Y"),
        ("vertex", "ball", "z", "createByPoint", None, "ZX"),
        ("point", "rigid", "z", "createByPoint", None, ""),
    ],
    ids=["rigid_faces", "revolute_z", "slider_x", "cylindrical_y", "ball_pitch_yaw", "sketch_points"],
)
def test_create_joint_uses_entity_geometry_and_motion_dispatch(
    monkeypatch, kind, motion, axis, constructor, key_point, motion_axes
):
    fake = _joint_adapter(monkeypatch, kind=kind)

    env = asm.create_joint(fake.adapter, _h(kind, "a"), _h(kind, "b"), motion, axis)

    assert env.ok is True
    geometry_args = [call.args for call in getattr(fake.geometry, constructor).call_args_list]
    extra = (None,) if kind == "face" else ()
    if key_point:
        extra += (getattr(fake.key_points, key_point),)
    assert geometry_args == [(fake.entities[token], *extra) for token in ("a", "b")]
    fake.joints.createInput.assert_called_once_with(*fake.geometries)
    getattr(fake.joint_input, f"setAs{motion.title()}JointMotion").assert_called_once_with(
        *(getattr(fake.directions, f"{a}AxisJointDirection") for a in motion_axes)
    )
    fake.joints.add.assert_called_once_with(fake.joint_input)
    assert env.result["joint_token"] == "joint-token"
    assert env.result["motion_type"] == motion
    assert env.result["axis"] == axis
    assert env.result["offset_mm"] is None
    assert env.result["angle_deg"] is None


def test_create_joint_passes_offset_angle_and_renames(monkeypatch):
    fake = _joint_adapter(monkeypatch)
    env = asm.create_joint(
        fake.adapter, _h("face", "a"), _h("face", "b"), motion_type="revolute",
        offset_mm="9 mm", angle_deg="0 deg", name="hinge_main",
    )

    assert env.ok is True
    assert fake.joint_input.offset == ("expression", "9 mm")
    assert fake.joint_input.angle == ("expression", "0 deg")
    assert env.result["joint_name"] == fake.joint.name == "hinge_main"


def test_create_joint_tries_non_planar_geometry_when_planar_returns_none(monkeypatch):
    fake = _joint_adapter(monkeypatch, fail="non_planar")
    env = asm.create_joint(fake.adapter, _h("face", "a"), _h("face", "b"))

    assert env.ok is True
    assert fake.geometry.createByPlanarFace.call_count == 2
    assert [call.args for call in fake.geometry.createByNonPlanarFace.call_args_list] == [
        (fake.entities[token], fake.key_points.CenterKeyPoint) for token in ("a", "b")
    ]
    fake.joints.createInput.assert_called_once_with(*fake.geometries)


def test_build_create_joint_rejects_bad_motion_type():
    with pytest.raises(ValueError, match="motion_type must be"):
        asm.build_create_joint(_h("face", "a"), _h("face", "b"), motion_type="spring")


def test_build_create_joint_rejects_bad_axis():
    with pytest.raises(ValueError, match="axis must be"):
        asm.build_create_joint(_h("face", "a"), _h("face", "b"), axis="w")


@pytest.mark.parametrize("slot", [0, 1])
def test_build_create_joint_rejects_non_origin_handle_kind(slot):
    origins = [_h("face", "a"), _h("face", "b")]
    origins[slot] = _h("body", "not-a-joint-origin")
    which = ("one", "two")[slot]
    with pytest.raises(ValueError, match=f"geometry_{which} kind 'body' unsupported"):
        asm.build_create_joint(*origins)


def test_create_joint_short_circuits_on_invalid_input():
    env = asm.create_joint(
        NoScriptAdapter(), _h("face", "a"), _h("face", "b"), motion_type="spring"
    )
    assert env.ok is False
    assert env.error == "invalid_input"


@pytest.mark.parametrize(
    ("fail", "error"),
    [("input_none", "joint_input_failed"), ("add_none", "joints_add_failed"),
     ("add_exception", "joints_add_failed")],
)
def test_create_joint_surfaces_rejected_input_or_add(monkeypatch, fail, error):
    fake = _joint_adapter(monkeypatch, fail=fail)
    env = asm.create_joint(fake.adapter, _h("face", "a"), _h("face", "b"), motion_type="revolute")

    assert env.ok is False
    assert env.error == error
    if fail == "input_none":
        fake.joints.add.assert_not_called()
    else:
        fake.joints.add.assert_called_once_with(fake.joint_input)
    if fail == "add_exception":
        assert env.result["detail"] == "JointInput rejected"
