"""Bounded, versioned scoped workspace observations and strict comparison."""

import math
import re

_FIELDS = {
    "body": {"bounds_mm": "mm", "volume_mm3": "mm^3"},
    "mesh": {"witness": "native"},
    "feature": {"health": "state"},
    "sketch": {"health": "state"},
    "parameter": {"expression": "text", "value_mm": "mm", "units": "text"},
    "occurrence": {"transform_cm": "cm"},
}
_MAX_RECORDS = 256


def _evidence_finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def prepare_evidence_scope(scope):
    if (
        not isinstance(scope, dict)
        or set(scope) != {"version", "path", "sections"}
        or scope["version"] != 1
        or type(scope["version"]) is not int
    ):
        raise ValueError("scope must have version 1, path and sections")
    path = scope["path"]
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 256
        or (path != "root" and not re.fullmatch(r"[^+]+:[0-9]+(?:\+[^+]+:[0-9]+)*", path))
    ):
        raise ValueError("scope path must be root or a full occurrence path")
    sections = scope["sections"]
    if not isinstance(sections, list) or not sections or len(sections) > len(_FIELDS):
        raise ValueError("scope sections must be a nonempty bounded list")
    seen = set()
    for section in sections:
        if not isinstance(section, dict) or set(section) != {
            "kind",
            "names",
            "fields",
            "tolerances",
        }:
            raise ValueError("invalid scope section")
        kind, names, fields, tolerances = (
            section[k] for k in ("kind", "names", "fields", "tolerances")
        )
        if kind not in _FIELDS or kind in seen:
            raise ValueError("invalid or duplicate section kind")
        seen.add(kind)
        if (
            not isinstance(names, list)
            or len(names) > _MAX_RECORDS
            or any(not isinstance(n, str) or not n.strip() or len(n) > 256 for n in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError("section names must be unique bounded strings")
        if (
            not isinstance(fields, list)
            or not fields
            or len(set(fields)) != len(fields)
            or any(f not in _FIELDS[kind] for f in fields)
        ):
            raise ValueError("section fields invalid")
        numeric = {
            f for f in fields if f in ("bounds_mm", "volume_mm3", "value_mm", "transform_cm")
        }
        required_tolerances = numeric | ({"witness"} if "witness" in fields else set())
        if (
            not isinstance(tolerances, dict)
            or set(tolerances) != required_tolerances
            or any(
                not _evidence_finite(v) or v < 0 for k, v in tolerances.items() if k != "witness"
            )
        ):
            raise ValueError("numeric fields need finite nonnegative tolerances")
        if "witness" in fields:
            witness_tolerances = tolerances["witness"]
            if (
                not isinstance(witness_tolerances, dict)
                or set(witness_tolerances) != {"bounds_mm", "transform_cm"}
                or any(not _evidence_finite(v) or v < 0 for v in witness_tolerances.values())
            ):
                raise ValueError("mesh witness needs bounds_mm and transform_cm tolerances")
    if sum(len(s["names"]) for s in sections) > _MAX_RECORDS:
        raise ValueError("scope too large")
    return scope


def _evidence_numeric_equal(a, b, tolerance, shape):
    if shape:
        return (
            isinstance(a, list)
            and isinstance(b, list)
            and len(a) == shape[0]
            and len(b) == shape[0]
            and all(
                _evidence_numeric_equal(a[i], b[i], tolerance, shape[1:]) for i in range(shape[0])
            )
        )
    return _evidence_finite(a) and _evidence_finite(b) and abs(a - b) <= tolerance


def _evidence_valid_bounds(value):
    return _evidence_numeric_equal(value, value, 0, [2, 3]) and all(
        value[0][i] <= value[1][i] for i in range(3)
    )


def _evidence_valid_pose(value):
    if not _evidence_numeric_equal(value, value, 0, [16]):
        return False
    tolerance = 1e-7
    if any(abs(value[i]) > tolerance for i in (12, 13, 14)) or abs(value[15] - 1) > tolerance:
        return False
    rows = [[value[4 * r + c] for c in range(3)] for r in range(3)]
    for r in range(3):
        for s in range(3):
            if (
                abs(sum(rows[r][i] * rows[s][i] for i in range(3)) - (1 if r == s else 0))
                > tolerance
            ):
                return False
    determinant = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    return abs(determinant - 1) <= tolerance


def compare_scoped_evidence(scope, before, after, *, allow_rebind=False):
    """Compare exact logical inventory and required fields, returning only differences."""
    scope = prepare_evidence_scope(scope)
    differences = []

    def add(kind, name, field, reason):
        if len(differences) < 1024:
            differences.append({"kind": kind, "name": name, "field": field, "reason": reason})

    def index(snapshot, label):
        result = {}
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("version") != 1
            or snapshot.get("scope") != scope
            or not isinstance(snapshot.get("binding"), dict)
            or not isinstance(snapshot["binding"].get("document"), str)
            or not snapshot["binding"].get("document")
            or not isinstance(snapshot["binding"].get("target_handle"), str)
            or not snapshot["binding"].get("target_handle")
        ):
            add("*", "", label, "invalid_snapshot")
            return None
        rows = snapshot.get("records")
        if not isinstance(rows, list) or len(rows) > _MAX_RECORDS:
            add("*", "", label, "invalid_records")
            return None
        counts = snapshot.get("counts")
        if not isinstance(counts, dict) or set(counts) != {s["kind"] for s in scope["sections"]}:
            add("*", "", label, "invalid_counts")
        else:
            for section in scope["sections"]:
                count = counts[section["kind"]]
                if type(count) is not int or count != len(section["names"]):
                    add(section["kind"], "", label, "count_mismatch")
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("kind"), str)
                or not isinstance(row.get("name"), str)
            ):
                add("*", "", label, "invalid_record")
                continue
            key = (row["kind"], row["name"])
            if key in result:
                add(*key, label, "duplicate_entity")
            else:
                result[key] = row
        if isinstance(counts, dict):
            for section in scope["sections"]:
                kind = section["kind"]
                if counts.get(kind) != sum(
                    1 for row in rows if isinstance(row, dict) and row.get("kind") == kind
                ):
                    add(kind, "", label, "count_record_mismatch")
        return result

    left, right = index(before, "baseline"), index(after, "result")
    if (
        not allow_rebind
        and isinstance(before, dict)
        and isinstance(after, dict)
        and before.get("binding") != after.get("binding")
    ):
        add("*", "", "binding", "binding_mismatch")
    expected = {(s["kind"], n) for s in scope["sections"] for n in s["names"]}
    for label, data in (("baseline", left), ("result", right)):
        if data is None:
            continue
        for kind, name in sorted(expected - data.keys()):
            add(kind, name, label, "missing_entity")
        for kind, name in sorted(data.keys() - expected):
            add(kind, name, label, "extra_entity")
    for section in scope["sections"]:
        kind = section["kind"]
        for name in section["names"]:
            a, b = (left or {}).get((kind, name)), (right or {}).get((kind, name))
            if not a or not b:
                continue
            af = a.get("fields") if isinstance(a.get("fields"), dict) else {}
            bf = b.get("fields") if isinstance(b.get("fields"), dict) else {}
            for field in section["fields"]:
                av, bv = af.get(field), bf.get(field)
                if (
                    not isinstance(av, dict)
                    or not isinstance(bv, dict)
                    or av.get("status") != "available"
                    or bv.get("status") != "available"
                    or av.get("units") != _FIELDS[kind][field]
                    or bv.get("units") != _FIELDS[kind][field]
                ):
                    add(kind, name, field, "unavailable_or_units_mismatch")
                    continue
                x, y = av.get("value"), bv.get("value")
                if field == "volume_mm3" and (
                    av.get("accuracy") != "LowCalculationAccuracy"
                    or bv.get("accuracy") != "LowCalculationAccuracy"
                ):
                    add(kind, name, field, "unavailable_or_units_mismatch")
                    continue
                if field == "witness":
                    equal = _evidence_mesh_equal(x, y, section["tolerances"][field])
                elif (
                    field == "bounds_mm"
                    and not (_evidence_valid_bounds(x) and _evidence_valid_bounds(y))
                    or field == "transform_cm"
                    and not (_evidence_valid_pose(x) and _evidence_valid_pose(y))
                ) or field == "volume_mm3" and (
                    not _evidence_finite(x) or not _evidence_finite(y) or x < 0 or y < 0
                ):
                    equal = False
                elif field in section["tolerances"]:
                    shape = {"bounds_mm": [2, 3], "transform_cm": [16]}.get(field, [])
                    equal = _evidence_numeric_equal(x, y, section["tolerances"][field], shape)
                else:
                    equal = isinstance(x, str) and bool(x) and x == y
                if not equal:
                    add(kind, name, field, "different_or_invalid")
    unknown_reasons = {
        "invalid_snapshot",
        "invalid_records",
        "invalid_counts",
        "invalid_record",
        "unavailable_or_units_mismatch",
    }
    status = (
        "passed"
        if not differences
        else "failed"
        if any(d["reason"] not in unknown_reasons for d in differences)
        else "incomplete"
    )
    return {
        "version": 1,
        "status": status,
        "differences": differences,
        "truncated": len(differences) >= 1024,
    }


def _evidence_mesh_equal(a, b, tolerances):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    keys = {
        "geometry_sha256",
        "node_count",
        "triangle_count",
        "quad_count",
        "polygon_count",
        "frame",
        "units",
        "bounds_mm",
        "occurrence_transform_cm",
        "source",
        "appearance",
    }
    if set(a) != keys or set(b) != keys:
        return False
    if not all(
        isinstance(v.get("geometry_sha256"), str)
        and re.fullmatch("[0-9a-f]{64}", v["geometry_sha256"])
        and v.get("frame") == "component_local"
        and v.get("units") == "cm"
        and v.get("source") == "MeshBody.mesh"
        and v.get("appearance") == "unknown"
        and all(
            type(v.get(k)) is int and v[k] >= 0
            for k in ("node_count", "triangle_count", "quad_count", "polygon_count")
        )
        and v["node_count"] > 0
        and v["triangle_count"] + v["quad_count"] + v["polygon_count"] > 0
        for v in (a, b)
    ):
        return False
    return (
        _evidence_valid_bounds(a["bounds_mm"])
        and _evidence_valid_bounds(b["bounds_mm"])
        and _evidence_valid_pose(a["occurrence_transform_cm"])
        and _evidence_valid_pose(b["occurrence_transform_cm"])
        and all(
            a[k] == b[k]
            for k in (
                "geometry_sha256",
                "node_count",
                "triangle_count",
                "quad_count",
                "polygon_count",
            )
        )
        and _evidence_numeric_equal(a["bounds_mm"], b["bounds_mm"], tolerances["bounds_mm"], [2, 3])
        and _evidence_numeric_equal(
            a["occurrence_transform_cm"],
            b["occurrence_transform_cm"],
            tolerances["transform_cm"],
            [16],
        )
    )


SCOPED_CAPTURE_SCRIPT = r"""
def _capture_scoped(entry, handle, scope):
    prepare_evidence_scope(scope)
    root, design = entry['root'], entry['design']
    occurrence = None if scope['path'] == 'root' else _strict_occurrence(root, scope['path'])
    component = root if occurrence is None else occurrence.component
    rows = []
    counts = {}
    for section in scope['sections']:
        kind = section['kind']
        collection = {'body': lambda: root.bRepBodies if occurrence is None else occurrence.bRepBodies,
                      'mesh': lambda: root.meshBodies if occurrence is None else occurrence.meshBodies,
                      'feature': lambda: component.features,
                      'sketch': lambda: component.sketches,
                      'parameter': lambda: design.userParameters,
                      'occurrence': lambda: root.occurrences if occurrence is None else occurrence.childOccurrences}[kind]()
        count = getattr(collection, 'count', None)
        count = count if type(count) is int else len(collection)
        if count > _MAX_RECORDS or len(rows) + count > _MAX_RECORDS:
            raise _WorkspaceError('evidence_scope_too_large')
        counts[kind] = count
        for i in range(count):
            entity = collection.item(i) if hasattr(collection, 'item') else collection[i]
            name = entity.fullPathName if kind == 'occurrence' else entity.name
            row = {'kind': kind, 'name': name, 'fields': {}}
            for field in section['fields']:
                item = {'status': 'unavailable', 'units': _FIELDS[kind][field]}
                try:
                    if not entity.isValid: raise ValueError('entity invalid')
                    if field == 'bounds_mm':
                        box = entity.preciseBoundingBox
                        value = [[getattr(box.minPoint, axis) * 10 for axis in ('x','y','z')], [getattr(box.maxPoint, axis) * 10 for axis in ('x','y','z')]]
                        if not _evidence_valid_bounds(value): raise ValueError('invalid bounds')
                    elif field == 'volume_mm3':
                        if not entity.isSolid: raise ValueError('volume requires solid')
                        props = entity.getPhysicalProperties(adsk.fusion.CalculationAccuracy.LowCalculationAccuracy)
                        if props is None or props.accuracy != adsk.fusion.CalculationAccuracy.LowCalculationAccuracy:
                            raise ValueError('volume accuracy unavailable')
                        value = props.volume * 1000
                        item['accuracy'] = 'LowCalculationAccuracy'
                        if not _evidence_numeric_equal(value, value, 0, []) or value < 0: raise ValueError('invalid volume')
                    elif field == 'health':
                        state = entity.healthState
                        value = {0:'healthy', 1:'warning', 2:'error'}.get(state)
                        if value is None: raise ValueError('health unknown')
                    elif field == 'expression': value = entity.expression
                    elif field == 'units': value = entity.unit
                    elif field == 'value_mm':
                        _, parameter = _parameter(design, entity.name)
                        value = parameter['value_mm']
                    elif field == 'transform_cm':
                        value = list(entity.transform2.asArray())
                        if not _evidence_valid_pose(value): raise ValueError('invalid transform')
                    elif field == 'witness': value = _mesh_witness(entity, occurrence)
                    else: raise ValueError('unsupported field')
                    if value is None: raise ValueError('field unavailable')
                    item.update(status='available', value=value)
                except Exception as exc:
                    item['reason'] = str(exc)[:160]
                row['fields'][field] = item
            rows.append(row)
    return {'version': 1, 'binding': {'target_handle': handle, 'document': entry['document'].name}, 'scope': scope, 'counts': counts, 'records': rows}
"""
