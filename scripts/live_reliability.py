"""Opt-in live checks on owned, disposable Fusion documents; never imports a benchmark.

Run with --run. The report contains measurements and check outcomes without design identifiers. Original documents are
preserved. Unknown operations are not retried and uncertain fixtures are left open.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import tempfile
import textwrap
import uuid
from pathlib import Path

from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.envelope import parse_status_json
from fusion_cad_mcp.receipts import get_status, run_typed
from fusion_cad_mcp.tools import (
    assembly,
    inspection,
    io,
    local_files,
    operations,
    parameters,
    parametric_sketch,
    placement,
    primitives,
    verify,
)


def checked(env):
    if not env.ok:
        raise RuntimeError("Tool failed: " + str(env.error).splitlines()[0][:80])
    return env.result or parse_status_json(env) or {}


class Guarded:
    def __init__(self, transport):
        self.transport = transport
        self.identity = None
        self.url = transport.url
        self.receipt_journal_dir = transport.receipt_journal_dir

    def execute_typed_script(self, script, **kwargs):
        if self.identity is None:
            raise RuntimeError("No confirmed active fixture")
        tree = ast.parse(script)
        entry = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        guard = ast.parse(
            "if not __debug__: raise RuntimeError('Fusion assertions are disabled')\n"
            "_test_doc = __import__('adsk.core', fromlist=['Application']).Application.get().activeDocument\n"
            f"if _test_doc is None or _test_doc.creationId != {self.identity!r} or _test_doc.isSaved:\n"
            "    raise RuntimeError('Active document is not an owned unsaved fixture')\n"
        ).body
        entry.body[0:0] = guard
        return self.transport.execute_typed_script(ast.unparse(ast.fix_missing_locations(tree)), **kwargs)


def raw(transport, body, **kwargs):
    script = "import adsk.core, adsk.fusion, json\ndef run(_ctx):\n    app=adsk.core.Application.get()\n"
    return transport.execute_typed_script(script + textwrap.indent(textwrap.dedent(body).strip(), "    "), **kwargs)


def run(report_path):
    if not __debug__:
        raise RuntimeError("Live verification requires Python assertions")
    checks = []
    owned = []
    previous = None
    def record(name, **evidence):
        item = dict(name=name, status="passed", evidence=evidence)
        checks.append(item)
        print(json.dumps(item), flush=True)

    folder = tempfile.mkdtemp(prefix="fusion-live-reliability-")
    class IsolatedAdapter(FusionAdapter):
        receipt_journal_dir = str(Path(folder) / 'receipts')
        def execute_typed_script(self, script, **kwargs):
            return run_typed(self.execute_script, script, self.url, self.timeout,
                             journal_dir=self.receipt_journal_dir, **kwargs)
    with IsolatedAdapter(timeout=60) as transport:
        adapter = Guarded(transport)
        start = checked(raw(transport, '''
            prior = app.activeDocument.creationId if app.activeDocument else None
            doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            doc.name = 'MCP owned reliability fixture'
            print(json.dumps({'ok': True, 'id': doc.creationId, 'prior': prior, 'version': app.version}))
        '''))
        adapter.identity = start['id']
        owned.append(adapter.identity)
        previous = start['prior']
        primary_failed = False
        try:
            checked(parameters.add_parameters(adapter, [dict(name='fixture_width', expression='40 mm', units='mm')]))
            rect = checked(parametric_sketch.create_parametric_rectangle(
                adapter, 'Rectangle XY', 'fixture_width', '20 mm', x='3 mm', y='4 mm'))
            assert rect['fully_constrained'] and rect['profile_count'] == 1
            for plane in ('xz', 'yz'):
                other = checked(parametric_sketch.create_parametric_rectangle(
                    adapter, 'Rectangle ' + plane, '12 mm', '8 mm', x='2 mm', y='3 mm', z='4 mm',
                    plane=plane, centered=True))
                assert other['fully_constrained'] and other['profile_count'] == 1
            record('constrained_rectangles', planes=3, centered_non_xy=True)
            checked(primitives.create_parametric_enclosure(
                adapter, 'Fixture Case', 'fixture_width', '30 mm', '15 mm', '2 mm', '2 mm'))
            operation_id = str(uuid.uuid4())
            update = parameters.update_parameter(adapter, 'fixture_width', '50 mm', operation_id)
            checked(update)
            duplicate = parameters.update_parameter(adapter, 'fixture_width', '50 mm', operation_id)
            checked(duplicate)
            assert duplicate.state['recovered_from_receipt']
            conflict = parameters.update_parameter(adapter, 'fixture_width', '60 mm', operation_id)
            assert not conflict.ok and conflict.error == 'receipt_conflict'
            bounds = checked(verify.bounding_box(adapter))['bodies']
            assert len(bounds) == 1
            box = bounds[0]['bbox_mm']
            assert math.isclose(box['max'][0] - box['min'][0], 50, abs_tol=1e-5)
            volume = checked(verify.volume(adapter))['bodies'][0]['volume_mm3']
            assert math.isclose(volume, 50*30*15 - 46*26*13, abs_tol=1e-4)
            record('parameter_receipt_and_brep', width_mm=50, volume_mm3=round(volume, 6),
                   duplicate_redispatched=False, conflict_refused=True,
                   recovered_from_receipt=bool(update.state['recovered_from_receipt']))
            parent = checked(placement.create_component(adapter, 'Fixture Parent'))['occurrence']['path']
            child = checked(placement.create_component(adapter, 'Fixture Child', parent))['occurrence']['path']
            sibling = checked(placement.create_component(adapter, 'Fixture Sibling'))['occurrence']['path']
            for path in (parent, child, sibling):
                checked(assembly.unground_component(adapter, path))
            preview = checked(assembly.create_rigid_group(adapter, [parent, sibling], 'Fixture Group',
                                                         include_children=True, preview=True))
            assert preview['selected_count'] == 2 and preview['expanded_count'] == 3 and preview['actual_count'] is None
            group = checked(assembly.create_rigid_group(adapter, [parent, sibling], 'Fixture Group',
                                                       include_children=False, max_occurrences=2))
            actual = checked(assembly.inspect_rigid_group(adapter, 'Fixture Group', limit=1))
            assert group['actual_count'] == 2 and actual['actual_count'] == 2
            compact = checked(inspection.inspect_assembly(adapter, include=['occurrences'], fields=[], limit=1))
            summary = checked(inspection.inspect_assembly(adapter, detail='summary'))
            assert compact['sections']['occurrences']['returned_count'] == 1
            assert compact['sections']['occurrences']['next_offset'] == 1
            assert summary['sections']['occurrences']['count'] == 3
            record('rigid_membership_and_compact_inspection', selected=2, actual=2, descendants=3,
                   preview_count=preview.get('expanded_count'), page_size=1)
            p2 = checked(placement.create_component(adapter, 'Expanded Parent'))['occurrence']['path']
            c2 = checked(placement.create_component(adapter, 'Expanded Child', p2))['occurrence']['path']
            s2 = checked(placement.create_component(adapter, 'Expanded Sibling'))['occurrence']['path']
            for path in (p2, c2, s2):
                checked(assembly.unground_component(adapter, path))
            expanded = checked(assembly.create_rigid_group(adapter, [p2, s2], 'Expanded Group',
                                                           include_children=True, max_occurrences=3))
            assert expanded['actual_count'] == 3 and expanded['is_suppressed'] is False
            record('native_descendant_membership', actual=3, suppressed=False)
            code = "import adsk.core,adsk.fusion,json\ndef run(_ctx):\n r=adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct).rootComponent\n r.occurrences.addNewComponent(adsk.core.Matrix3D.create())\n print(json.dumps({'ok':True,'count':r.allOccurrences.count}))"
            counter_id = str(uuid.uuid4())
            checked(adapter.execute_typed_script(code, operation_id=counter_id))
            checked(adapter.execute_typed_script(code, operation_id=counter_id))
            final_count = checked(inspection.inspect_assembly(adapter, include=['occurrences'], detail='summary'))['sections']['occurrences']['count']
            assert final_count == 7
            record('non_idempotent_duplicate_refusal', actual_occurrences=7, expected_without_duplicate=7)
            image = Path(folder) / 'viewport.png'
            screenshot = checked(local_files.save_viewport(adapter, str(image), 1200, 800,
                                                           direction='iso-top-right', fit=True))
            assert screenshot['width'] == 1200 and screenshot['height'] == 800
            assert screenshot['camera_restored'] and image.stat().st_size == screenshot['bytes_written']
            record('viewport_local_png', width=1200, height=800, bytes=screenshot['bytes_written'],
                   camera_restored=True)
            archive = Path(folder) / 'fixture.f3d'
            exported = checked(io.export(adapter, 'f3d', str(archive)))
            assert exported['bytes_written'] == archive.stat().st_size > 0
            opened = checked(local_files.open_local_archive(adapter, str(archive)))
            assert opened['source_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
            assert opened['source_bytes'] == archive.stat().st_size
            new_id = opened['document']['creation_id']
            owned.append(new_id)
            adapter.identity = new_id
            assert opened['document']['is_active'] and not opened['document']['is_saved']
            reopened = checked(verify.volume(adapter))['bodies'][0]['volume_mm3']
            assert math.isclose(reopened, volume, abs_tol=1e-4)
            reopened_group = checked(assembly.inspect_rigid_group(adapter, 'Expanded Group'))
            assert reopened_group['actual_count'] == 3 and reopened_group['is_suppressed'] is False
            record('archive_reopen', same_volume=True, source_hash_matches=True,
                   bytes=exported['bytes_written'], previous_document_preserved=opened['previous_document_still_open'])
            # A bounded, deliberately declared failure exercises the write barrier.
            failed = adapter.execute_typed_script('import json\ndef run(_ctx):\n    print(json.dumps({"ok":False,"error":"fixture_declared_failure"}))')
            assert not failed.ok and failed.error == 'fixture_declared_failure'
            blocked = parameters.update_parameter(adapter, 'fixture_width', '55 mm')
            assert blocked.error == 'receipt_barrier'
            status = checked(get_status(failed.state['operation_id'], journal_dir=transport.receipt_journal_dir))
            assert status['declared_result']['error'] == 'fixture_declared_failure'
            after_failure = checked(verify.volume(adapter))['bodies'][0]['volume_mm3']
            assert math.isclose(after_failure, volume, abs_tol=1e-4)
            checked(operations.reconcile_operation(adapter, failed.state['operation_id'], adapter.identity,
                                                   True, 'Owned fixture: deliberate no-op declaration returned; inspected unchanged B-rep'))
            checked(parameters.update_parameter(adapter, 'fixture_width', '52 mm'))
            after_recovery = checked(verify.bounding_box(adapter))['bodies'][0]['bbox_mm']
            assert math.isclose(after_recovery['max'][0]-after_recovery['min'][0], 52, abs_tol=1e-5)
            record('recovery_barrier', blocked_write=True, read_available=True, explicit_reconciliation=True)
        except Exception:
            primary_failed = True
            checks.append(dict(name='live_scenario', status='failed'))
            raise
        finally:
            try:
                # Only close positively identified fixtures; never close an unknown or saved document.
                cleanup = checked(raw(transport, f'''
                    ids = {owned!r}
                    docs = [app.documents.item(i) for i in range(app.documents.count)]
                    selected = [d for d in docs if d.creationId in ids]
                    if len(selected) != len(ids) or any(d.isSaved for d in selected):
                        raise RuntimeError('Fixture cleanup identity check failed')
                    previous = [d for d in docs if d.creationId == {previous!r}]
                    for doc in reversed(selected):
                        if not doc.close(False): raise RuntimeError('Fixture close failed')
                    if previous:
                        previous[0].activate()
                        if app.activeDocument.creationId != {previous!r}:
                            raise RuntimeError('Previous document activation was not verified')
                    print(json.dumps({{'ok': True, 'closed': len(selected), 'previous_restored': bool(previous)}}))
                '''))
                record('fixture_cleanup', **{k:v for k,v in cleanup.items() if k != 'ok'})
            except Exception:
                checks.append(dict(name='fixture_cleanup', status='unverified',
                                   reason='Receipt barrier or document ownership check prevented cleanup'))
                if not primary_failed:
                    raise
            finally:
                Path(report_path).write_text(json.dumps(dict(fusion_version=start['version'], checks=checks), indent=2)+'\n', encoding='utf-8')
    report = dict(fusion_version=start['version'], checks=checks)
    Path(report_path).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--report', default='live-reliability.json')
    args = parser.parse_args()
    if not args.run:
        parser.error('Explicit --run is required')
    run(args.report)
