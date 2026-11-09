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
        rootComponent=root, exportManager=exporter, unitsManager=SimpleNamespace(defaultLengthUnits="in"), designType=0
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
    adsk.fusion.DesignTypes = SimpleNamespace(ParametricDesignType=1)
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


def test_export_lookup_is_prewrite(fusion, tmp_path):
    dest = tmp_path / "absent" / "x.stl"
    env = io_tool.export(fusion.adapter, "stl", str(dest), body="missing")
    assert env.error == "body_not_found"
    assert env.result["mutation_state"] == "not_started"
    assert not dest.parent.exists()
    assert io_tool.export(fusion.adapter, "stl", str(dest), body="Plate").ok


def test_obj_bundle_and_native_mesh_import(fusion, tmp_path, monkeypatch):
    source = tmp_path / "part.obj"
    source.write_text("mtllib part.mtl\nv 0 0 0\n", encoding="utf-8")
    (tmp_path / "part.mtl").write_text("newmtl paint\nmap_Kd texture.png\n", encoding="utf-8")
    (tmp_path / "texture.png").write_bytes(b"synthetic image")
    class Mesh:
        objectType = "adsk::fusion::MeshBody"
        isValid = True
        name = "Mesh"
        def __init__(self, token):
            self.entityToken = token
            self.parentComponent = fusion.root
        mesh = SimpleNamespace(nodeCount=3, triangleCount=1)
        boundingBox = SimpleNamespace(minPoint=SimpleNamespace(x=0, y=0, z=0), maxPoint=SimpleNamespace(x=1, y=2, z=3))
    class Meshes(_Collection):
        def add(self, path, unit):
            assert path == str(source.resolve()) and unit == 11
            self.extend([Mesh("mesh-one"), Mesh("mesh-two")])
            return _Collection(self[-2:])
    fusion.root.meshBodies = Meshes()
    sys.modules["adsk.fusion"].MeshUnits = SimpleNamespace(MillimeterMeshUnit=11)
    env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert env.ok, env.message
    assert env.result["mesh_bodies_added"] == 2
    assert len(env.state["handles_added"]) == 2
    assert env.result["imported_objects"][0]["local_bounds"]["max"] == [10, 20, 30]
    assert env.result["bundle"]["dependency_count"] == 3
    assert Path(env.result["bundle"]["path"]).is_file()
    assert env.state["local_manifest_write_occurred"]
    assert env.result["appearance"]["status"] == "unknown"


def test_obj_missing_resources_require_explicit_geometry_only(fusion, tmp_path):
    source = tmp_path / "part.obj"
    source.write_text("mtllib missing.mtl\n", encoding="utf-8")
    env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert env.error == "invalid_input" and "missing appearance" in env.message
    from fusion_cad_mcp.tools.mesh_import import _bundle
    assert _bundle(str(source), None, "geometry_only")["missing"] == ["missing.mtl"]
    assert not fusion.adapter.scripts


def test_obj_rejects_dependency_escape_and_invalid_units(fusion, tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    source = folder / "part.obj"
    source.write_text("mtllib ../outside.mtl\n", encoding="utf-8")
    (tmp_path / "outside.mtl").write_text("", encoding="utf-8")
    assert io_tool.import_geometry(fusion.adapter, "obj", str(source)).error == "invalid_input"
    rejected = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert rejected.error == "invalid_input" and "escapes" in rejected.message
    from fusion_cad_mcp.tools.mesh_import import _bundle
    assert len(_bundle(str(source), [str(tmp_path)], "require_resources")["dependencies"]) == 2


def test_obj_partial_native_result_is_not_claimed_rolled_back(fusion, tmp_path):
    source = tmp_path / "part.obj"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    class Meshes(_Collection):
        def add(self, path, units):
            self.append(object())
            return None
    fusion.root.meshBodies = Meshes()
    sys.modules["adsk.fusion"].MeshUnits = SimpleNamespace(MillimeterMeshUnit=11)
    env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert env.error == "import_no_mesh_bodies"
    assert env.result["mesh_bodies_delta"] == 1
    assert env.result["cleanup"] == "not_attempted"
    assert "mutation_state" not in env.result


def test_obj_rechecks_dependency_bytes_before_native_call(fusion, tmp_path):
    from fusion_cad_mcp.tools.mesh_import import build_obj_import
    source = tmp_path / "part.obj"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    script = build_obj_import(str(source), None, "mm", None, "require_resources")
    source.write_text("v 1 0 0\n", encoding="utf-8")
    fusion.root.meshBodies = _Collection()
    env = io_tool._ok_runner(fusion.adapter, script, "import_geometry")
    assert env.error == "dependency_changed"
    assert env.result["mutation_state"] == "not_started"


def test_obj_rejects_texture_path_escape_and_remote_reference(fusion, tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    source = folder / "part.obj"
    source.write_text("mtllib part.mtl\n", encoding="utf-8")
    material = folder / "part.mtl"
    for reference in ("../outside.png", "https://example.com/x.png"):
        material.write_text("map_Kd " + reference + "\n", encoding="utf-8")
        env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="cm")
        assert env.error == "invalid_input"
    assert not fusion.adapter.scripts


def test_export_component_lookup_does_not_modify_existing_file(fusion, tmp_path):
    dest = tmp_path / "x.step"
    dest.write_bytes(b"old")
    env = io_tool.export(fusion.adapter, "step", str(dest), component="missing")
    assert env.error == "occurrence_not_found"
    assert env.result["mutation_state"] == "not_started"
    assert dest.read_bytes() == b"old"


def test_import_forwards_caller_operation_id(monkeypatch, tmp_path):
    calls = []
    def execute(adapter, script, *, operation_id=None):
        calls.append(operation_id)
        return Envelope(ok=True, message='{"ok": true, "imported_objects": []}')
    monkeypatch.setattr(io_tool, "execute_typed_script", execute)
    source = tmp_path / "part.step"
    result = io_tool.import_geometry(object(), "step", str(source), operation_id="known-id")
    assert result.ok and calls == ["known-id"]


def test_obj_large_streamed_bundle_and_pbr_maps(tmp_path, monkeypatch):
    from fusion_cad_mcp.tools.mesh_import import _bundle
    source = tmp_path / "large.obj"
    with source.open("wb") as stream:
        stream.write(b"mtllib fixture.mtl\n")
        for _ in range(18000):
            stream.write(b"#" + b"x" * 999 + b"\n")
    (tmp_path / "fixture.mtl").write_text("norm a.png\nmap_ao b.png\nmap_Pr c.png\n", encoding="utf-8")
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"fixture")
    assert source.stat().st_size > 16 * 1024 * 1024
    bundle = _bundle(str(source), None, "require_resources")
    assert len(bundle["dependencies"]) == 5


def test_obj_targeted_cleanup_verifies_old_meshes(fusion, tmp_path):
    source = tmp_path / "part.obj"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    class Mesh:
        objectType = "adsk::fusion::MeshBody"
        name = "Added"
        entityToken = "new"
        isValid = True
        parentComponent = fusion.root
        @property
        def boundingBox(self):
            raise RuntimeError("readback failed")
        def deleteMe(self):
            fusion.root.meshBodies.remove(self)
            return True
    class Meshes(_Collection):
        def add(self, path, units):
            created = Mesh()
            self.append(created)
            return _Collection([created])
    old = SimpleNamespace(
        entityToken="old", isValid=True, name="Old", nativeObject=None,
        mesh=SimpleNamespace(nodeCount=3, triangleCount=1, quadCount=0, polygonCount=0,
                             nodeCoordinatesAsDouble=[0, 0, 0, 1, 0, 0, 0, 1, 0],
                             triangleNodeIndices=[0, 1, 2]),
        boundingBox=SimpleNamespace(minPoint=SimpleNamespace(x=0, y=0, z=0),
                                    maxPoint=SimpleNamespace(x=1, y=1, z=0)),
    )
    fusion.root.meshBodies = Meshes([old])
    sys.modules["adsk.fusion"].MeshUnits = SimpleNamespace(MillimeterMeshUnit=11)
    env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert env.error == "mesh_readback_failed"
    assert env.result["cleanup"] == "verified_mesh_bodies_only"
    assert fusion.root.meshBodies == [old]
    assert "mutation_state" not in env.result


def test_obj_manifest_is_stable_and_content_addressed(tmp_path, monkeypatch):
    from fusion_cad_mcp.tools.mesh_import import build_obj_import
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    source = tmp_path / "part.obj"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    first = build_obj_import(str(source), None, "mm", None, "require_resources")
    second = build_obj_import(str(source), None, "mm", None, "require_resources")
    assert first == second
    artifacts = list((tmp_path / "local" / "fusion-cad-mcp" / "manifests-v1").iterdir())
    assert len(artifacts) == 1
    assert artifacts[0].stem == hashlib.sha256(artifacts[0].read_bytes()).hexdigest()


def test_obj_conflicting_dependency_roles_are_refused(tmp_path):
    from fusion_cad_mcp.tools.mesh_import import _bundle
    source = tmp_path / "part.obj"
    source.write_text("mtllib first.mtl\nmtllib second.mtl\n", encoding="utf-8")
    (tmp_path / "first.mtl").write_text("map_Kd second.mtl\n", encoding="utf-8")
    (tmp_path / "second.mtl").write_text("map_Kd ../outside.png\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting roles"):
        _bundle(str(source), None, "require_resources")


def test_obj_retargeted_dependency_alias_is_refused(fusion, tmp_path):
    from fusion_cad_mcp.tools.mesh_import import build_obj_import
    folder = tmp_path / "bundle"
    safe = folder / "safe"
    outside = tmp_path / "outside"
    safe.mkdir(parents=True)
    outside.mkdir()
    (safe / "paint.mtl").write_text("newmtl a\n", encoding="utf-8")
    (outside / "paint.mtl").write_text("newmtl a\n", encoding="utf-8")
    alias = folder / "alias"
    try:
        alias.symlink_to(safe, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable on this system")
    source = folder / "part.obj"
    source.write_text("mtllib alias/paint.mtl\n", encoding="utf-8")
    script = build_obj_import(str(source), None, "mm", None, "require_resources")
    alias.unlink()
    alias.symlink_to(outside, target_is_directory=True)
    env = io_tool._ok_runner(fusion.adapter, script, "import_geometry")
    assert env.error == "dependency_changed"
    assert env.result["mutation_state"] == "not_started"


@pytest.mark.parametrize("mode", ["missing_return", "duplicate_return", "foreign_return"])
def test_obj_rejects_incomplete_duplicate_or_foreign_return(fusion, tmp_path, mode):
    source = tmp_path / "part.obj"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    class Mesh:
        isValid = True
        def __init__(self, token, owner):
            self.entityToken, self.parentComponent = token, owner
    class Meshes(_Collection):
        def add(self, path, units):
            first, second = Mesh("one", fusion.root), Mesh("two", fusion.root)
            self.extend([first, second])
            if mode == "missing_return":
                return _Collection([first])
            if mode == "duplicate_return":
                return _Collection([first, first])
            return _Collection([first, Mesh("two", fusion.child)])
    fusion.root.meshBodies = Meshes()
    sys.modules["adsk.fusion"].MeshUnits = SimpleNamespace(MillimeterMeshUnit=11)
    env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert not env.ok and env.error == "mesh_readback_failed"
    assert env.result["cleanup"] == "not_attempted"
    assert "mutation_state" not in env.result
    assert len(fusion.root.meshBodies) == 2


def test_obj_rechecks_lexical_alias_even_if_hashed_target_is_unchanged(fusion, tmp_path, monkeypatch):
    import os

    from fusion_cad_mcp.tools.mesh_import import build_obj_import
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "alias").mkdir()
    material = folder / "alias" / "paint.mtl"
    material.write_text("newmtl a\n", encoding="utf-8")
    outside = tmp_path / "outside.mtl"
    outside.write_text("newmtl a\n", encoding="utf-8")
    source = folder / "part.obj"
    source.write_text("mtllib alias/paint.mtl\n", encoding="utf-8")
    script = build_obj_import(str(source), None, "mm", None, "require_resources")
    realpath = os.path.realpath
    def changed_alias(value, *args, **kwargs):
        if os.path.normcase(str(value)) == os.path.normcase(str(material)):
            return str(outside)
        return realpath(value, *args, **kwargs)
    monkeypatch.setattr(os.path, "realpath", changed_alias)
    fusion.root.meshBodies = _Collection()
    env = io_tool._ok_runner(fusion.adapter, script, "import_geometry")
    assert env.error == "dependency_changed"
    assert env.result["mutation_state"] == "not_started"


def test_obj_rejects_non_obj_source_even_with_declared_format(fusion, tmp_path):
    source = tmp_path / "looks-like-mesh.stl"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    result = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert result.error == "invalid_input"
    assert not fusion.adapter.scripts


def test_obj_detects_preexisting_mesh_modified_by_import(fusion, tmp_path):
    source = tmp_path / "part.obj"
    source.write_text("v 0 0 0\n", encoding="utf-8")
    old = SimpleNamespace(
        entityToken="old", isValid=True, name="Old", nativeObject=None,
        mesh=SimpleNamespace(nodeCount=3, triangleCount=1, quadCount=0, polygonCount=0,
                             nodeCoordinatesAsDouble=[0, 0, 0, 1, 0, 0, 0, 1, 0],
                             triangleNodeIndices=[0, 1, 2]),
        boundingBox=SimpleNamespace(minPoint=SimpleNamespace(x=0, y=0, z=0),
                                    maxPoint=SimpleNamespace(x=1, y=1, z=0)),
    )
    new = SimpleNamespace(entityToken="new", isValid=True, parentComponent=fusion.root)
    class Meshes(_Collection):
        def add(self, path, units):
            old.mesh.nodeCoordinatesAsDouble[3] = 2
            self.append(new)
            return _Collection([new])
    fusion.root.meshBodies = Meshes([old])
    sys.modules["adsk.fusion"].MeshUnits = SimpleNamespace(MillimeterMeshUnit=11)
    env = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert env.error == "mesh_readback_failed"
    assert env.result["cleanup"] == "not_attempted"
    assert "mutation_state" not in env.result


@pytest.mark.parametrize("content", [
    b"# comment\rmtllib ../outside.mtl\rv 0 0 0\r",
    b"# comment\x00mtllib ../outside.mtl\n",
    b"mtllib material.mtl\\   \n",
    b"v 0 0 0\x1b\n",
])
def test_obj_rejects_ambiguous_control_characters_and_continuations(fusion, tmp_path, content):
    source = tmp_path / "part.obj"
    source.write_bytes(content)
    result = io_tool.import_geometry(fusion.adapter, "obj", str(source), units="mm")
    assert result.error == "invalid_input"
    assert not fusion.adapter.scripts


def test_obj_bundle_accepts_crlf_and_tabs(tmp_path):
    from fusion_cad_mcp.tools.mesh_import import _bundle
    source = tmp_path / "part.obj"
    source.write_bytes(b"mtllib\tmaterial.mtl\r\nv 0 0 0\r\n")
    (tmp_path / "material.mtl").write_bytes(b"newmtl\tmaterial\r\n")
    assert len(_bundle(str(source), None, "require_resources")["dependencies"]) == 2
