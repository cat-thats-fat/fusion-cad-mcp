"""Native Fusion mesh witnesses shared by workspace and inspection scripts.

The original PolygonMesh is used, not its display tessellation. SHA-256 encodes
native double coordinates and all polygon index streams in fixed endian order.
This is an exact, order-sensitive witness, not a mesh equivalence algorithm.
"""

MESH_EVIDENCE_SCRIPT = r'''
def _mesh_pose_valid(value):
    import math
    if len(value) != 16 or not all(math.isfinite(v) for v in value): return False
    if any(abs(value[i]) > 1e-7 for i in (12, 13, 14)) or abs(value[15]-1) > 1e-7: return False
    rows = [[value[4*r+c] for c in range(3)] for r in range(3)]
    if any(abs(sum(rows[r][i]*rows[s][i] for i in range(3)) - (1 if r == s else 0)) > 1e-7 for r in range(3) for s in range(3)): return False
    det = (rows[0][0]*(rows[1][1]*rows[2][2]-rows[1][2]*rows[2][1]) - rows[0][1]*(rows[1][0]*rows[2][2]-rows[1][2]*rows[2][0]) + rows[0][2]*(rows[1][0]*rows[2][1]-rows[1][1]*rows[2][0]))
    return abs(det - 1) <= 1e-7

def _mesh_witness(body, occurrence=None):
    import hashlib, math, struct
    native = body.nativeObject or body
    mesh = native.mesh
    if mesh is None: raise ValueError('original mesh unavailable')
    counts = [mesh.nodeCount, mesh.triangleCount, mesh.quadCount, mesh.polygonCount]
    if any(type(v) is not int or v < 0 for v in counts) or counts[0] == 0 or sum(counts[1:]) == 0:
        raise ValueError('invalid mesh counts')
    if counts[0] > 1000000 or sum(counts[1:]) > 2000000:
        raise ValueError('mesh witness too large')
    nodes = list(mesh.nodeCoordinatesAsDouble)
    tri = list(mesh.triangleNodeIndices) if mesh.triangleCount else []
    quad = list(mesh.quadNodeIndices) if mesh.quadCount else []
    poly = list(mesh.polygonNodeIndices) if mesh.polygonCount else []
    per = list(mesh.nodeCountPerPolygon) if mesh.polygonCount else []
    if len(nodes) > 3000000 or sum(map(len, (tri, quad, poly, per))) > 6000000:
        raise ValueError('mesh witness too large')
    if len(nodes) != mesh.nodeCount * 3 or len(tri) != mesh.triangleCount * 3 or len(quad) != mesh.quadCount * 4 or len(per) != mesh.polygonCount or sum(per) != len(poly):
        raise ValueError('invalid mesh array lengths')
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in nodes):
        raise ValueError('nonfinite mesh coordinate')
    if not all(type(v) is int and 0 <= v < mesh.nodeCount for v in tri + quad + poly):
        raise ValueError('invalid mesh index')
    if not all(type(v) is int and v >= 3 for v in per): raise ValueError('invalid polygon size')
    digest = hashlib.sha256(b'fusion-original-polygon-mesh-v1\0')
    for values, fmt in ((nodes, '>d'), (tri, '>Q'), (quad, '>Q'), (per, '>Q'), (poly, '>Q')):
        digest.update(struct.pack('>Q', len(values)))
        for value in values: digest.update(struct.pack(fmt, value))
    pose = list(occurrence.transform2.asArray()) if occurrence is not None else [1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
    if not _mesh_pose_valid(pose): raise ValueError('invalid occurrence transform')
    box = native.boundingBox
    bounds = [[getattr(box.minPoint, axis) * 10 for axis in ('x','y','z')], [getattr(box.maxPoint, axis) * 10 for axis in ('x','y','z')]]
    if not all(math.isfinite(v) for row in bounds for v in row) or any(bounds[0][i] > bounds[1][i] for i in range(3)): raise ValueError('invalid mesh bounds')
    return {'geometry_sha256': digest.hexdigest(), 'node_count': mesh.nodeCount,
            'triangle_count': mesh.triangleCount, 'quad_count': mesh.quadCount,
            'polygon_count': mesh.polygonCount, 'frame': 'component_local', 'units': 'cm',
            'bounds_mm': bounds, 'occurrence_transform_cm': pose,
            'source': 'MeshBody.mesh', 'appearance': 'unknown'}
'''
