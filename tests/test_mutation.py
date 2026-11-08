"""Run the embedded mutation helpers against controlled Fusion API doubles."""

from types import SimpleNamespace

import pytest

from fusion_cad_mcp.mutation import MUTATION_SCRIPT
from tests._assembly_fakes import Collection, point, root_with


class Entity:
    def __init__(self, name, collection, deleted, *, state=0, message=""):
        self.name = name
        self.healthState = state
        self.errorOrWarningMessage = message
        self.isValid = True
        self.collection = collection
        self.deleted = deleted
        collection.add(self)

    def deleteMe(self):
        self.deleted.append(self.name)
        self.collection.items.remove(self)
        self.isValid = False
        return True


@pytest.fixture
def mutation():
    namespace = {}
    exec(MUTATION_SCRIPT, namespace)
    return namespace["_Mutation"], namespace["_MutationError"]


def _design():
    root = root_with(name="Root", features=Collection(), sketches=Collection(), isValid=True)
    root.occurrences = root.allOccurrences
    return SimpleNamespace(rootComponent=root, userParameters=Collection(), computeAll=lambda: True)


def test_cleanup_is_reverse_order_and_preserves_existing_health(mutation):
    factory, error = mutation
    design = _design()
    root = design.rootComponent
    deleted = []
    warning = Entity("Prior warning", root.features, deleted, state=1, message="Known issue")
    tx = factory(design, root)
    tx.track(Entity("Sketch", root.sketches, deleted))
    tx.track(Entity("Feature", root.features, deleted, state=1, message="Bad cut"))

    with pytest.raises(error) as exc:
        tx.verify()
    assert exc.value.code == "created_entity_unhealthy"
    assert tx.rollback()["status"] == "verified"
    assert deleted == ["Feature", "Sketch"]
    assert root.features.items == [warning]
    assert warning.errorOrWarningMessage == "Known issue"


def test_delete_boolean_cannot_hide_a_surviving_entity(mutation):
    factory, _ = mutation
    design = _design()
    root = design.rootComponent
    tx = factory(design, root)
    created = tx.track(Entity("Survivor", root.features, []))
    created.deleteMe = lambda: True

    result = tx.rollback()

    assert result["status"] == "failed"
    assert result["deleted_count"] == 0
    assert result["checks"]["changed_groups"] == ["Root / features"]


def test_unchanged_body_count_does_not_hide_geometry_drift(mutation):
    factory, _ = mutation
    design = _design()
    root = design.rootComponent
    body = SimpleNamespace(
        name="Plate", isValid=True, volume=1.0,
        boundingBox=SimpleNamespace(minPoint=point(), maxPoint=point(1, 1, 1)),
    )
    root.bRepBodies.add(body)
    tx = factory(design, root)
    tx.track(Entity("Cut", root.features, []))
    body.volume = 0.9

    result = tx.rollback()

    assert root.bRepBodies.count == 1
    assert result["status"] == "failed"
    assert result["checks"]["changed_groups"] == ["Root / bRepBodies"]


def test_unrelated_existing_warning_is_reported_without_rejecting_new_feature(mutation):
    factory, _ = mutation
    design = _design()
    root = design.rootComponent
    Entity("Prior warning", root.features, [], state=1, message="Known issue")
    tx = factory(design, root)
    tx.track(Entity("New feature", root.features, []))

    assert tx.verify() == {"recomputed": True, "created_checked": 1,
                           "created_health_checked": 1, "existing_health_checked": 1,
                           "preexisting_unhealthy": 1}


def test_rollback_detects_an_occurrence_that_moved_even_with_unchanged_native_geometry(mutation):
    factory, _ = mutation
    design = _design()
    root = design.rootComponent
    pose = [1.0] * 16
    root.occurrences.add(SimpleNamespace(
        name="Lid:1", isValid=True, isGrounded=False, isGroundToParent=False,
        transform2=SimpleNamespace(asArray=lambda: pose),
    ))
    tx = factory(design, root)
    pose[3] = 2.0

    result = tx.rollback()

    assert result["status"] == "failed"
    assert result["checks"]["changed_groups"] == ["Root / occurrences"]


def test_absent_feature_health_is_refused_before_writing(mutation):
    factory, error = mutation
    design = _design()
    root = design.rootComponent
    root.features.add(SimpleNamespace(name="Uninspectable", isValid=True))

    with pytest.raises(error) as exc:
        factory(design, root)

    assert exc.value.code == "health_unavailable"


def test_rollback_detects_changed_joint_motion(mutation):
    factory, _ = mutation
    design = _design()
    root = design.rootComponent
    motion = SimpleNamespace(rotationValue=0.0)
    root.joints = Collection([SimpleNamespace(
        name="Hinge", isValid=True, isSuppressed=False, healthState=0, jointMotion=motion,
    )])
    tx = factory(design, root)
    motion.rotationValue = 0.1

    result = tx.rollback()

    assert result["status"] == "failed"
    assert result["checks"]["changed_groups"] == ["Root / joints"]


def test_rolled_back_timeline_is_refused_before_writing(mutation):
    factory, error = mutation
    design = _design()
    design.timeline = SimpleNamespace(markerPosition=1, count=2)

    with pytest.raises(error) as exc:
        factory(design, design.rootComponent)

    assert exc.value.code == "timeline_rolled_back"


def test_construction_axis_health_and_rollback_inventory(mutation):
    factory, error = mutation
    design = _design()
    root = design.rootComponent
    root.constructionAxes = Collection()
    axis = Entity("Driven axis", root.constructionAxes, [])
    tx = factory(design, root)
    axis.healthState = 2
    axis.errorOrWarningMessage = "axis failed"
    with pytest.raises(error) as exc:
        tx.verify()
    assert exc.value.code == "feature_health_regressed"
    result = tx.rollback()
    assert result["status"] == "failed"
    assert "Root / constructionAxes" in result["checks"]["changed_groups"]


def test_native_nested_occurrence_uses_all_root_context_proxies(mutation):
    factory, _ = mutation
    design = _design()
    parent = root_with(name="Parent", features=Collection(), sketches=Collection(), isValid=True)
    class NativeOccurrence:
        name = "Board:1"
        isValid = True
        transform2 = SimpleNamespace(asArray=lambda: [0.0] * 16)
        @property
        def isGrounded(self):
            raise RuntimeError("isGrounded property is not available for the occurrence of a sub component")
    native = NativeOccurrence()
    parent.occurrences = Collection([native])
    first = SimpleNamespace(name="Board:1", fullPathName="Parent:1+Board:1", nativeObject=native,
                            transform2=SimpleNamespace(asArray=lambda: [1.0] * 16), isGrounded=False,
                            isGroundToParent=False)
    second = SimpleNamespace(name="Board:1", fullPathName="Parent:2+Board:1", nativeObject=native,
                             transform2=SimpleNamespace(asArray=lambda: [2.0] * 16), isGrounded=False,
                             isGroundToParent=False)
    design.rootComponent.allOccurrences = Collection([first, second])
    tx = factory(design, parent)
    assert tx.verify()["recomputed"]
    second.isGrounded = True
    result = tx.rollback()
    assert result["status"] == "failed"
    assert result["checks"]["changed_groups"] == ["Parent / occurrences"]


def test_occurrence_without_root_context_fails_closed(mutation):
    factory, error = mutation
    design = _design()
    parent = root_with(name="Parent", features=Collection(), sketches=Collection(), isValid=True)
    parent.occurrences = Collection([SimpleNamespace(name="Lost", isValid=True,
        transform2=SimpleNamespace(asArray=lambda: [0.0] * 16))])
    with pytest.raises(error) as exc:
        factory(design, parent)
    assert exc.value.code == "occurrence_context_unavailable"
