"""Behavior tests for native sweep, using a small Fusion API double."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fusion_cad_mcp.tools import native_features as n
from tests._assembly_fakes import Collection, ScriptAdapter, point, root_with


class ObjectCollection:
    def __init__(self):
        self.items = []

    def add(self, x):
        self.items.append(x)

    @staticmethod
    def create():
        return ObjectCollection()


def fixture(monkeypatch, *, bad_path=False, bad_health=False, empty=False):
    profile = object()
    line1 = SimpleNamespace(isValid=True)
    line2 = SimpleNamespace(isValid=True)
    curves = SimpleNamespace(
        sketchLines=Collection([line1, line2]), sketchArcs=Collection(), sketchCircles=Collection()
    )
    sketch = SimpleNamespace(
        name="profile", isValid=True, healthState=0, profiles=Collection([profile])
    )
    path_sketch = SimpleNamespace(name="path", isValid=True, healthState=0, sketchCurves=curves)
    feats = Collection()
    calls = []
    root = root_with(
        name="Root",
        isValid=True,
        sketches=Collection([sketch, path_sketch]),
        features=feats,
    )
    root.occurrences = Collection()
    bb = SimpleNamespace(minPoint=point(), maxPoint=point(1, 1, 2))
    body = SimpleNamespace(
        name="solid", isValid=True, isSolid=True, volume=2.0, boundingBox=bb, entityToken="body"
    )
    feat = SimpleNamespace(
        name="Sweep1",
        isValid=True,
        healthState=1 if bad_health else 0,
        errorOrWarningMessage="bad",
        objectType="adsk::fusion::SweepFeature",
        entityToken="feature",
        bodies=Collection([] if empty else [body]),
    )

    def delete():
        feat.isValid = False
        feats.items.remove(feat)
        return True

    feat.deleteMe = delete

    def add(inp):
        calls.append(("add", inp))
        feats.items.append(feat)
        return feat

    feats.sweepFeatures = SimpleNamespace(
        createInput=lambda prof, path, op: ("input", prof, path, op), add=add
    )

    def create_path(collection, mode):
        calls.append(("path", collection.items, mode))
        return None if bad_path else "path-object"

    design = SimpleNamespace(
        rootComponent=root, userParameters=Collection(), computeAll=lambda: True
    )
    app = SimpleNamespace(activeDocument=SimpleNamespace(creationId="doc", dataFile=None))
    adapter = ScriptAdapter(
        monkeypatch,
        design,
        app=app,
        core={"ObjectCollection": ObjectCollection},
        fusion={
            "Path": SimpleNamespace(create=create_path),
            "ChainedCurveOptions": SimpleNamespace(noChainedCurves=0),
            "FeatureOperations": SimpleNamespace(NewBodyFeatureOperation=7),
        },
    )
    return adapter, root, calls, feat


def test_sweep_explicit_path_and_positive_solid(monkeypatch):
    adapter, root, calls, feat = fixture(monkeypatch)
    result = n.sweep(adapter, "profile", "path", ["line:0", "line:1"], name="Cable")
    assert result.ok and result.result["feature_name"] == "Cable"
    assert len(calls[0][1]) == 2 and calls[0][2] == 0
    assert calls[1][1][3] == 7
    assert result.result["bodies"][0]["volume_mm3"] == 2000
    assert result.result["bodies"][0]["bounds_mm"]["max"] == [10, 10, 20]
    assert result.result["feature_handle"].startswith("feature:Cable|doc=")
    assert feat in root.features.items


@pytest.mark.parametrize("kw", [{"bad_path": True}, {"bad_health": True}, {"empty": True}])
def test_sweep_failure_rolls_back_and_verifies(monkeypatch, kw):
    adapter, root, calls, feat = fixture(monkeypatch, **kw)
    result = n.sweep(adapter, "profile", "path", ["line:0"], name="SweepTest")
    assert not result.ok
    assert result.result["rollback"]["status"] == "verified"
    assert feat not in root.features.items
    if kw.get("bad_path"):
        assert len(calls) == 1


def test_duplicate_feature_name_refuses_before_creation(monkeypatch):
    adapter, root, calls, feat = fixture(monkeypatch)
    root.features.items.append(feat)
    result = n.sweep(adapter, "profile", "path", ["line:0"], name="Sweep1")
    assert result.error == "feature_name_taken"
    assert calls == []


def test_missing_path_index_refuses_before_creation(monkeypatch):
    adapter, root, calls, feat = fixture(monkeypatch)
    result = n.sweep(adapter, "profile", "path", ["line:9"], name="SweepTest")
    assert result.error == "path_entity_invalid"
    assert calls == []


@pytest.mark.parametrize(
    "refs",
    [[], ["line:0", "line:00"], ["line:0:start"], ["point:0"], ["line:-1"], ["line:0", "line:0"]],
)
def test_invalid_path_references_rejected(refs):
    with pytest.raises(ValueError):
        n.build_sweep("profile", "path", refs)


@pytest.mark.parametrize("index", [-1, True, 1.1])
def test_bad_profile_index_rejected(index):
    with pytest.raises(ValueError):
        n.build_sweep("profile", "path", ["line:0"], profile_index=index)


def test_nonpositive_volume_reverts_created_feature(monkeypatch):
    adapter, root, calls, feat = fixture(monkeypatch)
    feat.bodies.item(0).volume = 0
    out = n.sweep(adapter, "profile", "path", ["line:0"], name="SweepTest")
    assert out.error == "sweep_invalid_body"
    assert out.result["rollback"]["status"] == "verified"
    assert feat not in root.features.items


def test_partial_add_failure_is_marked_unknown(monkeypatch):
    adapter, root, calls, feat = fixture(monkeypatch)

    def fail(inp):
        raise RuntimeError("add failed after potential write")

    root.features.sweepFeatures.add = fail
    out = n.sweep(adapter, "profile", "path", ["line:0"], name="SweepTest")
    assert out.error == "sweep_failed"
    assert out.result["mutation_state"] == "unknown"


def test_duplicate_sketch_selector_refused(monkeypatch):
    adapter, root, calls, feat = fixture(monkeypatch)
    root.sketches.items.append(root.sketches.item(0))
    out = n.sweep(adapter, "profile", "path", ["line:0"], name="SweepTest")
    assert out.error == "sketch_ambiguous"
    assert calls == []


@pytest.mark.parametrize("path", ["Board:1", "Carrier:1+Board:1"])
@pytest.mark.parametrize("missing_proxy", [False, True])
def test_child_sweep_uses_occurrence_context_and_native_profile(monkeypatch, path, missing_proxy):

    class Native(SimpleNamespace):
        def __getattribute__(self, name):
            if name == "entityToken":
                raise RuntimeError("native child token unavailable")
            return super().__getattribute__(name)

        def createForAssemblyContext(self, occurrence):
            return SimpleNamespace(entityToken=self.proxy_token)

    adapter, component, calls, old_feature = fixture(monkeypatch)
    path_curve = component.sketches.item(1).sketchCurves.sketchLines.item(0)
    curve_proxy = SimpleNamespace(isValid=True)
    observed = []

    def proxy_for(occurrence):
        observed.append(occurrence)
        return None if missing_proxy else curve_proxy

    path_curve.createForAssemblyContext = proxy_for
    profile = component.sketches.item(0).profiles.item(0)
    inputs = []

    def capture_input(p, path, op):
        inputs.append((p, path, op))
        return (p, path, op)

    component.features.sweepFeatures.createInput = capture_input
    import adsk.core

    native_body = Native(**vars(old_feature.bodies.item(0)), proxy_token="proxy-body")
    native_feature = Native(
        **{**vars(old_feature), "bodies": Collection([native_body])}, proxy_token="proxy-feature"
    )
    component.features.sweepFeatures.add = lambda inp: (
        component.features.items.append(native_feature) or native_feature
    )
    occ = SimpleNamespace(
        fullPathName=path,
        transform2=SimpleNamespace(
            asArray=lambda: [0, -1, 0, 5, 1, 0, 0, 3, 0, 0, 1, 1, 0, 0, 0, 1]
        ),
        component=component,
        bRepBodies=Collection(),
        isReferencedComponent=False,
    )
    top = root_with(name="Root", sketches=Collection(), features=Collection(), occurrences=[occ])
    top.occurrences = Collection([occ])
    adsk.core.Application.get().activeProduct.rootComponent = top
    out = n.sweep(adapter, path + " / profile", path + " / path", ["line:0"], name="SweepTest")
    assert observed == [occ]
    if missing_proxy:
        assert out.error == "path_proxy_unavailable"
        assert out.result["rollback"]["status"] == "verified"
        assert calls == []
        return
    assert out.ok, out.result
    assert "|occ=" + path + "|doc=" in out.result["feature_handle"]
    assert out.result["feature_handle"].endswith(":proxy-feature")
    assert out.result["bodies"][0]["handle"].endswith(":proxy-body")
    assert calls[0][1] == [curve_proxy]
    # Input creation must keep the profile native, as the live Fusion probe established.
    assert inputs[0][0] is profile


@pytest.mark.parametrize("name", [None, "", "   "])
def test_sweep_requires_stable_name(name):
    with pytest.raises(ValueError, match="name is required"):
        n.build_sweep("profile", "path", ["line:0"], name=name)
