"""IO script behavior against a fake Fusion API and real temporary files."""

from __future__ import annotations

import hashlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import io as io_tool

_TRANSFORM = [1, 0, 0, 4, 0, 1, 0, 5, 0, 0, 1, 6, 0, 0, 0, 1]


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("plate.stl", "absolute"),
        (r"C:\parts\..\plate.stl", "parent-dir"),
        ("/parts/../plate.stl", "parent-dir"),
        ("C:/parts/con.stl", "reserved"),
        (r"C:\parts\LPT1", "reserved"),
        ("", "non-empty"),
        ("C:/parts/", "name a file"),
    ],
)
def test_invalid_paths_are_rejected(path, error):
    ok, message = io_tool._validate_path(path)
    assert ok is False
    assert error in message


@pytest.mark.parametrize("path", [r"C:\parts\plate.stl", "/parts/plate.stl", r"\\server\parts\plate.stl"])
def test_absolute_paths_are_accepted_on_either_host(path):
    assert io_tool._validate_path(path) == (True, None)


class _Collection(list):
    @property
    def count(self):
        return len(self)

    def item(self, index):
        return self[index]


class _Component:
    def __init__(self, name):
        self.name = name
        self.bRepBodies = _Collection()
        self.occurrences = _Collection()
        self.allOccurrences = self.occurrences

    def allOccurrencesByComponent(self, component):
        return _Collection(occ for occ in self.allOccurrences if occ.component is component)


class _Occurrence:
    objectType = "adsk::fusion::Occurrence"
    entityToken = "fixture-occ"
    isReferencedComponent = False
    transform2 = SimpleNamespace(asArray=lambda: _TRANSFORM)

    def __init__(self, name, component, assembly_context=None):
        self.name = name
        self.component = component
        self.bRepBodies = component.bRepBodies
        self.assemblyContext = assembly_context
        self.fullPathName = (
            name if assembly_context is None else f"{assembly_context.fullPathName}+{name}"
        )

    def createForAssemblyContext(self, occurrence):
        return _Occurrence(self.name, self.component, occurrence)


class _Body:
    objectType = "adsk::fusion::BRepBody"
    entityToken = "fixture-body"

    def __init__(self, name, assembly_context=None):
        self.name = name
        self.assemblyContext = assembly_context

    def createForAssemblyContext(self, occurrence):
        return _Body(self.name, occurrence)


class _Exporter:
    def __init__(self):
        self.calls = []
        self.options = SimpleNamespace(unitType=None)
        self.output = b"generated CAD file"
        self.sidecar = None
        self.ok = True

    def __getattr__(self, factory):
        def create_options(*args):
            self.calls.append((factory, args))
            return self.options

        return create_options

    def execute(self, options):
        _, args = self.calls[-1]
        path = next(Path(arg) for arg in args if isinstance(arg, str))
        if self.output is not None:
            path.write_bytes(self.output)
        if self.sidecar is not None:
            path.with_suffix(".mtl").write_bytes(self.sidecar)
        return self.ok


class _Importer:
    def __init__(self):
        self.calls = []
        self.items = [_Occurrence("Imported:1", _Component("Imported")), _Body("Solid")]
        self.reject = False

    def __getattr__(self, factory):
        def create_options(path):
            self.calls.append((factory, path))
            return SimpleNamespace(path=path)

        return create_options

    def importToTarget2(self, options, target):
        self.calls.append(("importToTarget2", target))
        if self.reject:
            return None
        target.bRepBodies.extend(item for item in self.items if isinstance(item, _Body))
        target.occurrences.extend(item for item in self.items if isinstance(item, _Occurrence))
        return _Collection(self.items)


class _ScriptAdapter:
    def __init__(self):
        self.scripts = []

    def execute_script(self, script):
        self.scripts.append(script)
        namespace = {}
        exec(script, namespace)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            namespace["run"](None)
        return Envelope(ok=True, message=stdout.getvalue())


@pytest.fixture
def fusion(monkeypatch):
    root = _Component("Root")
    root.bRepBodies.append(_Body("Plate"))
    child = _Component("Child")
    occurrence = _Occurrence("Child:1", child)
    root.occurrences.append(occurrence)
    exporter = _Exporter()
    importer = _Importer()
    design = SimpleNamespace(
        rootComponent=root, exportManager=exporter, unitsManager=SimpleNamespace(defaultLengthUnits="in")
    )
    doc = SimpleNamespace(creationId="fixture-doc", dataFile=None)
    app = SimpleNamespace(activeDocument=doc, activeProduct=design, importManager=importer)

    adsk = ModuleType("adsk")
    adsk.core = ModuleType("adsk.core")
    adsk.core.Application = SimpleNamespace(get=lambda: app)
    adsk.core.Matrix3D = SimpleNamespace(
        create=lambda: SimpleNamespace(asArray=lambda: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    )
    adsk.fusion = ModuleType("adsk.fusion")
    adsk.fusion.Design = SimpleNamespace(cast=lambda product: product)
    adsk.fusion.MeshRefinementSettings = SimpleNamespace(
        MeshRefinementLow=1, MeshRefinementMedium=2, MeshRefinementHigh=3
    )
    adsk.fusion.DistanceUnits = SimpleNamespace(MillimeterDistanceUnits=0, CentimeterDistanceUnits=1)
    for module in (adsk, adsk.core, adsk.fusion):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return SimpleNamespace(
        root=root, child=child, occurrence=occurrence, exporter=exporter,
        importer=importer, adapter=_ScriptAdapter()
    )


def test_stl_export_applies_refinement_and_units_to_native_body(fusion, tmp_path):
    destination = tmp_path / "new" / "plate's 🧩.stl"
    env = io_tool.export(fusion.adapter, "stl", str(destination), body="Plate", refinement="high", units="cm")
    assert env.ok
    factory, (geometry, staged_file) = fusion.exporter.calls[0]
    assert factory == "createSTLExportOptions"
    assert geometry is fusion.root.bRepBodies[0]
    assert Path(staged_file).name == destination.name
    assert Path(staged_file).parent.parent == destination.parent
    assert fusion.exporter.options.meshRefinement == 3
    assert fusion.exporter.options.unitType == 1
    assert fusion.exporter.options.sendToPrintUtility is False
    assert destination.read_bytes() == fusion.exporter.output
    assert env.result["bytes_written"] == len(fusion.exporter.output)
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize(
    ("fmt", "factory", "mesh"),
    [
        ("stl", "createSTLExportOptions", True),
        ("3mf", "createC3MFExportOptions", True),
        ("obj", "createOBJExportOptions", True),
        ("step", "createSTEPExportOptions", False),
        ("iges", "createIGESExportOptions", False),
        ("f3d", "createFusionArchiveExportOptions", False),
        ("sat", "createSATExportOptions", False),
        ("smt", "createSMTExportOptions", False),
    ],
)
@pytest.mark.parametrize("scoped", [False, True])
def test_export_uses_correct_native_factory_and_scope(fusion, tmp_path, fmt, factory, mesh, scoped):
    destination = tmp_path / f"part.{fmt}"
    env = io_tool.export(
        fusion.adapter, fmt, str(destination), component="Child:1" if scoped else None
    )
    assert env.ok
    called_factory, args = fusion.exporter.calls[0]
    assert called_factory == factory
    if mesh:
        assert args[0] is (fusion.occurrence if scoped else fusion.root)
    elif scoped:
        assert args[1] is fusion.child
    else:
        assert len(args) == 1
    assert env.result["scope"] == ("component" if scoped else "root")
    assert destination.read_bytes() == fusion.exporter.output


@pytest.mark.parametrize(
    ("ok", "output", "expected_error"),
    [(False, b"partial", "export_failed"), (True, None, "export_empty"), (True, b"", "export_empty")],
)
def test_failed_export_preserves_existing_destination(fusion, tmp_path, ok, output, expected_error):
    destination = tmp_path / "plate.step"
    destination.write_bytes(b"previous")
    fusion.exporter.ok = ok
    fusion.exporter.output = output
    env = io_tool.export(fusion.adapter, "step", str(destination))
    assert env.error == expected_error
    assert env.ok is False
    assert destination.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [destination]


def test_obj_preserves_material_sidecar(fusion, tmp_path):
    destination = tmp_path / "plate.obj"
    material = destination.with_suffix(".mtl")
    fusion.exporter.output = b"mtllib plate.mtl\n"
    fusion.exporter.sidecar = b"newmtl plate\n"
    env = io_tool.export(fusion.adapter, "obj", str(destination), body="Plate", refinement="low")
    assert env.ok
    assert fusion.exporter.options.meshRefinement == 1
    assert destination.read_bytes() == fusion.exporter.output
    assert material.read_bytes() == fusion.exporter.sidecar
    assert env.result["sidecars"] == [str(material)]


def test_obj_restores_material_if_main_publish_fails(fusion, tmp_path):
    destination = tmp_path / "plate.obj"
    destination.mkdir()  # Cannot replace a directory with a regular file.
    material = destination.with_suffix(".mtl")
    material.write_bytes(b"previous material")
    fusion.exporter.sidecar = b"new material"
    with pytest.raises(OSError):
        io_tool.export(fusion.adapter, "obj", str(destination))
    assert destination.is_dir()
    assert material.read_bytes() == b"previous material"
    assert set(tmp_path.iterdir()) == {destination, material}


@pytest.mark.parametrize("fmt", ["step", "iges", "f3d", "sat", "smt"])
def test_unsupported_body_scope_never_dispatches(fusion, tmp_path, fmt):
    env = io_tool.export(fusion.adapter, fmt, str(tmp_path / f"x.{fmt}"), body="Plate")
    assert env.error == "invalid_input"
    assert "body scope" in env.message
    assert fusion.adapter.scripts == []


@pytest.mark.parametrize(
    ("fmt", "path", "options", "error"),
    [
        ("stl", "plate.stl", {}, "invalid_path"),
        ("ply", "/parts/x.ply", {}, "invalid_input"),
        ("stl", "/parts/x.stl", {"refinement": "ultra"}, "invalid_input"),
        ("stl", "/parts/x.stl", {"units": "furlongs"}, "invalid_input"),
        ("stl", "/parts/x.stl", {"body": "Plate", "component": "Child:1"}, "invalid_input"),
    ],
)
def test_export_rejects_invalid_inputs_without_dispatch(fusion, fmt, path, options, error):
    env = io_tool.export(fusion.adapter, fmt, path, **options)
    assert env.ok is False and env.error == error
    assert fusion.adapter.scripts == []


def test_export_rejects_ambiguous_body_without_writing_file(fusion, tmp_path):
    fusion.occurrence.bRepBodies = _Collection([_Body("Plate", fusion.occurrence)])
    destination = tmp_path / "plate.stl"
    env = io_tool.export(fusion.adapter, "stl", str(destination), body="Plate")
    assert env.error == "body_ambiguous"
    assert env.result["matches"] == ["Plate", "Child:1 / Plate"]
    assert fusion.exporter.calls == []
    assert not destination.exists()


@pytest.mark.parametrize(
    ("fmt", "factory"),
    [
        ("step", "createSTEPImportOptions"),
        ("iges", "createIGESImportOptions"),
        ("sat", "createSATImportOptions"),
        ("smt", "createSMTImportOptions"),
        ("f3d", "createFusionArchiveImportOptions"),
    ],
)
def test_import_uses_native_format_and_root_default(fusion, tmp_path, fmt, factory):
    source = tmp_path / f"part.{fmt}"
    source.write_bytes(b"fixture geometry")
    env = io_tool.import_geometry(fusion.adapter, fmt, str(source))
    assert env.ok
    assert fusion.importer.calls == [(factory, str(source)), ("importToTarget2", fusion.root)]
    assert env.result["coordinate_frame"] == "root"
    assert env.result["target_component"] == "Root"
    assert env.result["occurrences_added"] == env.result["bodies_added"] == 1


def test_scoped_import_reports_source_and_assembly_context(fusion, tmp_path):
    source = tmp_path / "part.step"
    data = b"fixture geometry"
    source.write_bytes(data)
    env = io_tool.import_geometry(fusion.adapter, "step", str(source), target_component="Child:1")
    assert env.ok
    assert fusion.importer.calls[1] == ("importToTarget2", fusion.child)
    assert len(fusion.root.bRepBodies) == len(fusion.root.occurrences) == 1
    result = env.result
    assert result["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["target_component"] == "Child"
    assert result["target_occurrence_path"] == "Child:1"
    assert result["target_to_root_transform_cm"] == _TRANSFORM
    assert result["coordinate_frame"] == "target_component"
    assert result["fusion_length_unit"] == "cm" and result["display_length_unit"] == "in"
    assert [entry["path"] for entry in result["imported_objects"]] == [
        "Child:1+Imported:1", "Child:1 / Solid"
    ]
    handles = env.state["handles_added"]
    assert handles == [entry["handle"] for entry in result["imported_objects"]]
    assert all("|occ=Child:1|doc=" in handle for handle in handles)


@pytest.mark.parametrize("referenced", [False, True])
def test_import_rejects_unsafe_component_edits(fusion, tmp_path, referenced):
    source = tmp_path / "part.step"
    source.write_bytes(b"fixture geometry")
    if referenced:
        fusion.occurrence.isReferencedComponent = True
    else:
        fusion.root.occurrences.append(_Occurrence("Child:2", fusion.child))
    env = io_tool.import_geometry(fusion.adapter, "step", str(source), target_component="Child:1")
    assert not env.ok
    expected = "referenced_component_edit_unsupported" if referenced else "shared_component_edit_unsupported"
    assert env.error == expected
    assert fusion.importer.calls == []


@pytest.mark.parametrize("empty", [False, True])
def test_import_fails_if_fusion_reports_no_objects(fusion, tmp_path, empty):
    source = tmp_path / "part.step"
    source.write_bytes(b"fixture geometry")
    if empty:
        fusion.importer.items = []
    else:
        fusion.importer.reject = True
    env = io_tool.import_geometry(fusion.adapter, "step", str(source))
    assert not env.ok and env.error == "import_no_objects"
    assert len(fusion.root.bRepBodies) == len(fusion.root.occurrences) == 1


def test_import_missing_file_never_invokes_native_importer(fusion, tmp_path):
    env = io_tool.import_geometry(fusion.adapter, "step", str(tmp_path / "missing.step"))
    assert not env.ok and env.error == "file_not_found"
    assert fusion.importer.calls == []


@pytest.mark.parametrize("fmt", ["stl", "ply"])
def test_import_unknown_format_short_circuits(fusion, tmp_path, fmt):
    env = io_tool.import_geometry(fusion.adapter, fmt, str(tmp_path / f"x.{fmt}"))
    assert not env.ok and env.error == "invalid_input"
    assert fusion.adapter.scripts == []
