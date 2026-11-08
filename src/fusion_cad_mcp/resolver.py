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

def _select_component(root, selector, for_edit=False):
    if selector is None or selector == 'root':
        return root, None, None
    occ, error = _select_occurrence(root, selector)
    if error:
        return None, None, error
    comp = occ.component
    if for_edit and sum(1 for i in range(root.allOccurrences.count)
                        if root.allOccurrences.item(i).component == comp) > 1:
        return None, None, {'ok': False, 'error': 'shared_component_edit_unsupported', 'name': selector}
    return comp, occ, None

def _select_feature(root, selector, for_edit=False):
    candidates = []
    for i in range(root.features.count):
        obj = root.features.item(i)
        candidates.append((obj.name, obj, root))
    for i in range(root.allOccurrences.count):
        occ = root.allOccurrences.item(i)
        for j in range(occ.component.features.count):
            obj = occ.component.features.item(j)
            candidates.append((_occ_path(occ) + ' / ' + obj.name, obj, occ.component))
    matches = [(path, obj, owner) for path, obj, owner in candidates
               if selector == path or selector == obj.name]
    if len(matches) != 1:
        return None, None, {'ok': False, 'error': 'feature_' + ('not_found' if not matches else 'ambiguous'),
                            'name': selector, 'matches': [m[0] for m in matches]}
    path, obj, owner = matches[0]
    if for_edit and owner != root and sum(1 for i in range(root.allOccurrences.count)
                         if root.allOccurrences.item(i).component == owner) > 1:
        return None, None, {'ok': False, 'error': 'shared_component_edit_unsupported', 'name': selector}
    return obj, owner, None

def _select_named(collection, selector, kind):
    return _pick([(collection.item(i).name, collection.item(i)) for i in range(collection.count)], selector, kind)

def _select_body(root, selector):
    return _pick(_body_instances(root), selector, 'body')

def _select_occurrence(root, selector):
    return _pick([(_occ_path(root.allOccurrences.item(i)), root.allOccurrences.item(i)) for i in range(root.allOccurrences.count)], selector, 'occurrence')
"""
