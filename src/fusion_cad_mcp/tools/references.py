"""Small portable mechanical reference metadata, stored locally in Fusion attributes.

Geometry import remains the responsibility of import_geometry. This module does
not fetch models, collect catalogs, or infer mounting/connector locations.
"""

from __future__ import annotations

import json
import math
import re

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..resolver import RESOLVER_SCRIPT

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _finite(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def _vector(value, label, *, direction=False):
    if (not isinstance(value, list) or len(value) != 3
            or any(not _finite(x) for x in value)):
        raise ValueError(label + " must be a finite [x,y,z] vector")
    if direction and not math.isclose(sum(x*x for x in value), 1, abs_tol=1e-6):
        raise ValueError(label + " must be a unit vector")


def validate_metadata(metadata: dict) -> dict:
    """Validate v1 without guessing units or silently dropping unknown fields."""
    if not isinstance(metadata, dict) or set(metadata) - {
        "schema_version", "reference_id", "units", "mounting_points", "connectors", "keepouts", "source_sha256"
    }:
        raise ValueError("metadata must be a v1 reference object with supported fields")
    if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1 or metadata.get("units") != "mm":
        raise ValueError("schema_version must be 1 and units must be mm")
    if not isinstance(metadata.get("reference_id"), str) or not _ID.fullmatch(metadata["reference_id"]):
        raise ValueError("reference_id must be a portable identifier (1..64 letters, digits, ._-)")
    if "source_sha256" in metadata and (not isinstance(metadata["source_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata["source_sha256"])):
        raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
    seen = set()
    for category, fields, required in (
        ("mounting_points", {"id", "position_mm", "axis", "hole_diameter_mm"}, {"id", "position_mm", "axis"}),
        ("connectors", {"id", "origin_mm", "x_axis", "y_axis"}, {"id", "origin_mm", "x_axis", "y_axis"}),
        ("keepouts", {"id", "min_mm", "max_mm"}, {"id", "min_mm", "max_mm"}),
    ):
        rows = metadata.get(category, [])
        if not isinstance(rows, list) or len(rows) > 128:
            raise ValueError(category + " must be a list with at most 128 entries")
        for row in rows:
            if not isinstance(row, dict) or set(row) - fields or not required <= set(row):
                raise ValueError("invalid " + category + " entry")
            identity = row["id"]
            if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in seen:
                raise ValueError("entry identifiers must be portable and unique across the reference")
            seen.add(identity)
            for key in fields - {"id", "hole_diameter_mm"}:
                _vector(row[key], key, direction=key in {"axis", "x_axis", "y_axis"})
            if category == "connectors" and abs(sum(a*b for a,b in zip(row["x_axis"], row["y_axis"], strict=True))) > 1e-6:
                raise ValueError("connector x_axis and y_axis must be perpendicular")
            if category == "keepouts" and any(a >= b for a,b in zip(row["min_mm"], row["max_mm"], strict=True)):
                raise ValueError("keepout minima must be below maxima on every axis")
            if "hole_diameter_mm" in row:
                v = row["hole_diameter_mm"]
                if not _finite(v) or v <= 0:
                    raise ValueError("hole_diameter_mm must be positive and finite")
    # A copy prevents callers from changing the validated payload while dispatching.
    return json.loads(json.dumps(metadata, allow_nan=False))


def build_reference_metadata(component: str | None, metadata: dict | None = None) -> str:
    if component is not None and (not isinstance(component, str) or not component):
        raise ValueError("component must be root or an occurrence selector")
    if metadata is not None:
        metadata = validate_metadata(metadata)
    return "import adsk.core, adsk.fusion, json\n" + RESOLVER_SCRIPT + f"\nCOMPONENT={component!r}\nDATA={metadata!r}\n" + _SCRIPT


_SCRIPT = """
def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({'ok': False, 'error': 'no_active_design'})); return
    comp, occ, error = _select_component(design.rootComponent, COMPONENT, for_edit=DATA is not None)
    if error: print(json.dumps(error)); return
    if DATA is not None:
        current = occ; seen = set()
        while current is not None:
            path = current.fullPathName
            if current.isReferencedComponent:
                print(json.dumps({'ok': False, 'error': 'referenced_component_edit_unsupported', 'name': path})); return
            if path in seen:
                print(json.dumps({'ok': False, 'error': 'occurrence_context_invalid'})); return
            seen.add(path)
            current = getattr(current, 'assemblyContext', None)
    attrs = comp.attributes
    group, key = 'fusion_cad_mcp', 'reference_metadata_v1'
    old = attrs.itemByName(group, key)
    before = old.value if old else None
    encoded = json.dumps(DATA, sort_keys=True, separators=(',', ':')) if DATA is not None else None
    if encoded is not None and before != encoded:
        written = None
        try:
            written = attrs.add(group, key, encoded)
            current = attrs.itemByName(group, key)
            if written is None or current is None or json.loads(current.value) != DATA:
                raise RuntimeError('Reference metadata readback did not match')
        except Exception as exc:
            restored = False
            try:
                if before is not None:
                    attrs.add(group, key, before)
                else:
                    current = attrs.itemByName(group, key)
                    if current: current.deleteMe()
                current = attrs.itemByName(group, key)
                restored = (current.value if current else None) == before
            except Exception:
                pass
            print(json.dumps({'ok': False, 'error': 'reference_metadata_write_failed',
                              'detail': str(exc), 'rollback': {'status': 'verified' if restored else 'failed',
                              'scope': 'reference metadata attribute only'}})); return
    attr = attrs.itemByName(group, key)
    if attr is None:
        print(json.dumps({'ok': False, 'error': 'reference_metadata_not_found'})); return
    try:
        data = json.loads(attr.value)
    except Exception:
        print(json.dumps({'ok': False, 'error': 'reference_metadata_invalid'})); return
    # Optional geometry evidence must not turn a verified attribute edit into
    # an unknown write if a body or occurrence cannot be inspected.
    bounds = []
    body_count = None; bounds_error = None
    try:
        bodies = occ.bRepBodies if occ else comp.bRepBodies
        body_count = bodies.count
        for i in range(min(body_count, 128)):
            body = bodies.item(i); box = body.boundingBox
            bounds.append({'body': body.name, 'min_mm': [box.minPoint.x*10,box.minPoint.y*10,box.minPoint.z*10],
                           'max_mm': [box.maxPoint.x*10,box.maxPoint.y*10,box.maxPoint.z*10]})
    except Exception as exc:
        bounds = []; bounds_error = str(exc)
    matrix = None; matrix_error = None
    if occ:
        try: matrix = list(occ.transform2.asArray())
        except Exception as exc: matrix_error = str(exc)
    print(json.dumps({'ok': True, 'metadata': data, 'metadata_frame': 'component_local',
                      'metadata_scope': 'component_definition', 'occurrence_path': occ.fullPathName if occ else None,
                      'component_to_root_matrix_cm': matrix, 'component_to_root_error': matrix_error,
                      'body_bounds_frame': 'root', 'body_bounds': bounds, 'body_bounds_error': bounds_error,
                      'body_count': body_count, 'bounds_truncated': body_count > 128 if body_count is not None else None,
                      'changed': DATA is not None and before != attr.value}))
"""


def _run(adapter, component, metadata):
    try:
        script = build_reference_metadata(component, metadata)
    except (ValueError, TypeError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    env = adapter.execute_script(script, read_only=metadata is None)
    if not env.ok:
        return env
    result = parse_status_json(env)
    if result is None:
        return script_parse_failure(env, "reference_metadata_parse_failed", read_only=metadata is None)
    if result.get("ok") and metadata is None:
        try:
            validate_metadata(result["metadata"])
        except (ValueError, TypeError, KeyError):
            return Envelope(ok=False, error="reference_metadata_invalid")
    return Envelope(ok=result.get("ok") is True, result=result, error=result.get("error"))


def set_reference_metadata(adapter: FusionAdapter, component: str, metadata: dict) -> Envelope:
    if metadata is None:
        return Envelope(ok=False, error="invalid_input", message="metadata is required")
    return _run(adapter, component, metadata)


def get_reference_metadata(adapter: FusionAdapter, component: str) -> Envelope:
    return _run(adapter, component, None)
