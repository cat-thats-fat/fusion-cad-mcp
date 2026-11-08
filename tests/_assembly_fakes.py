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
        vars(adsk.core).update(core or {})
        vars(adsk.fusion).update(fusion or {})
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)

    def execute_script(self, script):
        self.scripts.append(script)
        namespace = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(script, "<generated Fusion script>", "exec"), namespace)
            namespace["run"](None)
        return Envelope(ok=True, message=output.getvalue())


class NoScriptAdapter:
    def execute_script(self, script):
        raise AssertionError("invalid input must not submit a Fusion script")


def point(x=0, y=0, z=0):
    return SimpleNamespace(x=x, y=y, z=z)


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
