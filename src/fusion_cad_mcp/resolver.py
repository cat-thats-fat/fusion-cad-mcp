"""Shared Fusion script helpers for selecting occurrence-context entities."""

RESOLVER_SCRIPT = """
def _occ_path(occ):
    return occ.fullPathName

def _body_instances(root):
    out = []
    for i in range(root.bRepBodies.count):
        b = root.bRepBodies.item(i)
        out.append((b.name, b))
    for i in range(root.allOccurrences.count):
        occ = root.allOccurrences.item(i)
        for j in range(occ.bRepBodies.count):
            b = occ.bRepBodies.item(j)
            out.append((_occ_path(occ) + ' / ' + b.name, b))
    return out

def _pick(candidates, selector, kind):
    matches = [(path, obj) for path, obj in candidates if selector == path or selector == obj.name or (kind == 'occurrence' and selector == obj.component.name)]
    if len(matches) != 1:
        return None, {'ok': False, 'error': kind + ('_not_found' if not matches else '_ambiguous'), 'name': selector, 'matches': [p for p, _ in matches]}
    return matches[0][1], None

def _select_body(root, selector):
    return _pick(_body_instances(root), selector, 'body')

def _select_occurrence(root, selector):
    return _pick([(_occ_path(root.allOccurrences.item(i)), root.allOccurrences.item(i)) for i in range(root.allOccurrences.count)], selector, 'occurrence')
"""
