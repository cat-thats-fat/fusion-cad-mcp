"""Group 6 / verify tools.

bounding_box, volume, mass, center_of_mass.

Each tool accepts an optional body_name. If omitted, returns measurements for
ALL bodies in the design (root + every occurrence). If given, returns just the
matching body or a structured 'body_not_found' error.

All distances reported in mm. Internal Fusion units are cm; conversion happens
in the generator so the agent sees mm everywhere.
"""

from __future__ import annotations

import json

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json
from ..execution import execute_typed_script
from ..resolver import RESOLVER_SCRIPT


def _body_walker_script(measurement_block: str, body_name: str | None) -> str:
    """Build a script that walks every body in root + all occurrences and yields
    measurements. measurement_block is Python that operates on a name `b` (the
    BRepBody) and emits per-body dict entries into a list named `entries`.
    """
    name_filter = "None" if body_name is None else json.dumps(body_name)
    return f"""
import adsk.core
import adsk.fusion
import json

NAME_FILTER = {name_filter}

{RESOLVER_SCRIPT}

def walk(root, entries):
    if NAME_FILTER is None:
        instances = _body_instances(root)
    else:
        instances = [(path, b) for path, b in _body_instances(root) if NAME_FILTER in (path, b.name)]
        body, error = _pick(instances, NAME_FILTER, "body")
        if error:
            return error
    for path, b in instances:
        try:
{measurement_block}
        except Exception as e:
            entries.append({{"path": path, "error": str(e)}})
    return None


def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}}))
        return
    entries = []
    error = walk(design.rootComponent, entries)
    if error:
        print(json.dumps(error)); return
    if NAME_FILTER is not None and not entries:
        print(json.dumps({{"ok": False, "error": "body_not_found", "name": NAME_FILTER}}))
        return
    print(json.dumps({{"ok": True, "bodies": entries, "count": len(entries)}}))
""".strip()


def build_bounding_box(body_name: str | None = None) -> str:
    return _body_walker_script(
        """\
            bb = b.boundingBox
            entries.append({
                "path": path,
                "name": b.name,
                "bbox_mm": {
                    "min": [round(bb.minPoint.x*10, 4), round(bb.minPoint.y*10, 4), round(bb.minPoint.z*10, 4)],
                    "max": [round(bb.maxPoint.x*10, 4), round(bb.maxPoint.y*10, 4), round(bb.maxPoint.z*10, 4)],
                },
                "extent_mm": [
                    round((bb.maxPoint.x - bb.minPoint.x)*10, 4),
                    round((bb.maxPoint.y - bb.minPoint.y)*10, 4),
                    round((bb.maxPoint.z - bb.minPoint.z)*10, 4),
                ],
            })""",
        body_name,
    )


def build_volume(body_name: str | None = None) -> str:
    return _body_walker_script(
        """\
            v_cm3 = b.volume
            entries.append({
                "path": path,
                "name": b.name,
                "volume_cm3": round(v_cm3, 6),
                "volume_mm3": round(v_cm3 * 1000.0, 3),
            })""",
        body_name,
    )


def build_mass(body_name: str | None = None) -> str:
    return _body_walker_script(
        """\
            pp = b.physicalProperties
            # Fusion's density is kg per internal cm^3; convert to kg/m^3 by *1e6 for readability
            density_kg_m3 = (pp.density * 1_000_000) if hasattr(pp, "density") else None
            entries.append({
                "path": path,
                "name": b.name,
                "mass_kg": round(pp.mass, 6),
                "volume_cm3": round(pp.volume, 6),
                "material": b.material.name if b.material else None,
                "density_kg_m3": round(density_kg_m3, 3) if density_kg_m3 is not None else None,
            })""",
        body_name,
    )


def build_center_of_mass(body_name: str | None = None) -> str:
    return _body_walker_script(
        """\
            pp = b.physicalProperties
            c = pp.centerOfMass
            entries.append({
                "path": path,
                "name": b.name,
                "center_of_mass_mm": [round(c.x*10, 4), round(c.y*10, 4), round(c.z*10, 4)],
            })""",
        body_name,
    )


def build_audit_feature_health(
    component_name: str | None = None,
    include_healthy: bool = False,
) -> str:
    """Recompute and inspect feature and sketch health, including unknown states."""
    comp_filter = "None" if component_name is None else json.dumps(component_name)
    include = "True" if include_healthy else "False"
    return f"""
import adsk.core, adsk.fusion, json
{RESOLVER_SCRIPT}

HEALTH_LABELS = {{0: 'healthy', 1: 'warning', 2: 'failed', 3: 'suppressed', 4: 'rolled_back', 5: 'unknown'}}
COMPONENT_FILTER = {comp_filter}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{'ok': False, 'error': 'no_active_design'}})); return
    try:
        computed = bool(design.computeAll())
    except Exception as exc:
        print(json.dumps({{'ok': False, 'error': 'recompute_failed', 'detail': str(exc)}})); return
    if not computed:
        print(json.dumps({{'ok': False, 'error': 'recompute_failed'}})); return
    root = design.rootComponent
    components = [(root.name, root)]
    for i in range(root.allOccurrences.count):
        occ = root.allOccurrences.item(i)
        components.append((_occ_path(occ), occ.component))
    summary = {{'total': 0, 'healthy': 0, 'warning': 0, 'failed': 0, 'suppressed': 0, 'rolled_back': 0, 'unknown': 0}}
    out = []
    matched = 0
    for path, comp in components:
        if COMPONENT_FILTER is not None and COMPONENT_FILTER not in (path, comp.name):
            continue
        matched += 1
        for category, collection in (('feature', comp.features), ('sketch', comp.sketches)):
            for i in range(collection.count):
                entity = collection.item(i)
                try:
                    state = int(entity.healthState)
                except Exception:
                    state = -1
                label = HEALTH_LABELS.get(state, 'unknown')
                summary['total'] += 1
                summary[label] += 1
                if state == 0 and not {include}:
                    continue
                try:
                    msg = entity.errorOrWarningMessage or ''
                except Exception:
                    msg = ''
                try:
                    cls = entity.classType().split('::')[-1]
                except Exception:
                    cls = '?'
                out.append({{'path': path + ' / ' + entity.name, 'component': comp.name,
                            'name': entity.name, 'classType': cls, 'category': category, 'healthState': state,
                            'healthLabel': label, 'isSuppressed': getattr(entity, 'isSuppressed', None),
                            'errorOrWarningMessage': msg[:500]}})
    if not matched:
        print(json.dumps({{'ok': False, 'error': 'component_not_found', 'name': COMPONENT_FILTER}})); return
    if COMPONENT_FILTER is not None and matched > 1:
        print(json.dumps({{'ok': False, 'error': 'component_ambiguous', 'name': COMPONENT_FILTER}})); return
    print(json.dumps({{'ok': summary['unknown'] == 0, 'error': 'health_unknown' if summary['unknown'] else None,
                      'summary': summary, 'features': out, 'recomputed': True}}))
""".strip()


# ---------- run wrappers ----------


def _run_walker(adapter: FusionAdapter, script: str, label: str) -> Envelope:
    env = execute_typed_script(adapter, script, read_only=label != "audit_feature_health")
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return Envelope(state=env.state, ok=False, error=f"{label}_parse_failed", message=env.message)
    if parsed.get("ok") is False:
        return Envelope(state=env.state,
            ok=False,
            error=parsed.get("error", f"{label}_failed"),
            message=env.message,
            result=parsed,
        )
    return Envelope(state=env.state, ok=True, message=env.message, result=parsed)


def bounding_box(adapter: FusionAdapter, body_name: str | None = None) -> Envelope:
    return _run_walker(adapter, build_bounding_box(body_name), "bounding_box")


def volume(adapter: FusionAdapter, body_name: str | None = None) -> Envelope:
    return _run_walker(adapter, build_volume(body_name), "volume")


def mass(adapter: FusionAdapter, body_name: str | None = None) -> Envelope:
    return _run_walker(adapter, build_mass(body_name), "mass")


def center_of_mass(adapter: FusionAdapter, body_name: str | None = None) -> Envelope:
    return _run_walker(adapter, build_center_of_mass(body_name), "center_of_mass")


def audit_feature_health(
    adapter: FusionAdapter,
    component_name: str | None = None,
    include_healthy: bool = False,
) -> Envelope:
    """Sweep the feature tree and report broken features.

    Call this as a postcondition after any tool that mutates sketch geometry,
    user parameters, or features. If summary.failed or summary.warning > 0,
    surface to the user before claiming success.
    """
    return _run_walker(
        adapter,
        build_audit_feature_health(component_name, include_healthy),
        "audit_feature_health",
    )


__all__ = [
    "audit_feature_health",
    "bounding_box",
    "build_audit_feature_health",
    "build_bounding_box",
    "build_center_of_mass",
    "build_mass",
    "build_volume",
    "center_of_mass",
    "mass",
    "volume",
]
