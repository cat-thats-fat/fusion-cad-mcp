"""Local harness for running generated Python. It does not implement Fusion geometry."""

import contextlib
import io
import sys
from types import ModuleType, SimpleNamespace

from fusion_cad_mcp.envelope import Envelope


class Collection:
    """Small stand-in for collections exposing count and item()."""

    def __init__(self, items=()):
        self.items = list(items)

    @property
    def count(self):
        return len(self.items)

    def item(self, index):
        return self.items[index]

    def add(self, item):
        self.items.append(item)


class ScriptAdapter:
    """Run the actual generated entry point against the supplied API surface."""

    def __init__(self, monkeypatch, design, *, app=None, core=None, fusion=None):
        app = SimpleNamespace() if app is None else app
        app.activeProduct = design
        self.scripts = []
        modules = {name: ModuleType(name) for name in ("adsk", "adsk.core", "adsk.fusion")}
        adsk = modules["adsk"]
        adsk.core, adsk.fusion = modules["adsk.core"], modules["adsk.fusion"]
        adsk.core.Application = SimpleNamespace(get=lambda: app)
        adsk.fusion.Design = SimpleNamespace(cast=lambda product: product)
        adsk.fusion.FeatureHealthStates = SimpleNamespace(HealthyFeatureHealthState=0)
        vars(adsk.core).update(core or {})
        vars(adsk.fusion).update(fusion or {})
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)

    def execute_script(self, script, *, read_only=False):
        self.scripts.append(script)
        namespace = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(script, "<generated Fusion script>", "exec"), namespace)
            namespace["run"](None)
        return Envelope(ok=True, message=output.getvalue())


class NoScriptAdapter:
    def execute_script(self, script, *, read_only=False):
        raise AssertionError("invalid input must not submit a Fusion script")


def point(x=0, y=0, z=0):
    return SimpleNamespace(x=x, y=y, z=z)


class RigidMatrix:
    """Independent rigid-coordinate arithmetic for native/proxy API doubles."""

    def __init__(self, rotation=((1, 0, 0), (0, 1, 0), (0, 0, 1)), translation=(0, 0, 0)):
        self.rotation = [list(row) for row in rotation]
        self.translation = list(translation)

    def copy(self):
        return RigidMatrix(self.rotation, self.translation)

    def invert(self):
        self.rotation = [list(row) for row in zip(*self.rotation, strict=True)]
        self.translation = [-sum(a * b for a, b in zip(row, self.translation, strict=True)) for row in self.rotation]
        return True

    def apply(self, values):
        return [sum(a * b for a, b in zip(row, values, strict=True)) + offset
                for row, offset in zip(self.rotation, self.translation, strict=True)]


def transformable_point(x=0, y=0, z=0):
    result = point(x, y, z)
    def transform(matrix):
        result.x, result.y, result.z = matrix.apply([result.x, result.y, result.z])
        return True
    result.transformBy = transform
    return result


def occurrence(path, *, component=None, bodies=(), **properties):
    name = path.split("+")[-1]
    if component is None:
        component = SimpleNamespace(name=name.split(":")[0])
    return SimpleNamespace(
        name=name, fullPathName=path, component=component, bRepBodies=Collection(bodies),
        **properties,
    )


def root_with(*, bodies=(), occurrences=(), **properties):
    return SimpleNamespace(
        bRepBodies=Collection(bodies), allOccurrences=Collection(occurrences), **properties
    )
