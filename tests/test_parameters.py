"""Parameter validation and generated script behavior against a local collection."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.tools import parameters as p
from tests._assembly_fakes import Collection, NoScriptAdapter, ScriptAdapter, point, root_with


class Parameter:
    isValid = True

    def __init__(self, name, expression, value=1.25, unit="", comment=""):
        self.name = name
        self.expression = expression
        self.value = value
        self.unit = unit
        self.comment = comment


class Parameters:
    def __init__(self):
        self.items = {}
        self.add_calls = []
        self.delete_calls = []

    def itemByName(self, name):
        return self.items.get(name)

    def item(self, index):
        return list(self.items.values())[index]

    @property
    def count(self):
        return len(self.items)

    def evaluate(self, expression, units):
        if expression == "base_width / 2":
            return self.items["base_width"].value / 2
        return float(expression.split()[0]) / (10 if units == "mm" else 1)

    def add(self, name, value_input, units, comment):
        self.add_calls.append((name, value_input, units, comment))
        expression = value_input.expression
        param = Parameter(name, expression, value=self.evaluate(expression, units), unit=units, comment=comment)

        def delete():
            self.delete_calls.append(name)
            if any(name in other.expression for other in self.items.values() if other is not param):
                return True  # Fusion can report true despite a dependent parameter.
            del self.items[name]
            param.isValid = False
            return True

        param.deleteMe = delete
        self.items[name] = param
        return param


def _adapter(monkeypatch, params, *, validator=None, compute=None, root=None, evaluate=None):
    if root is None:
        root = root_with(name="Root", features=Collection(), sketches=Collection())
    root.isValid = True
    root.occurrences = root.allOccurrences
    return ScriptAdapter(
        monkeypatch,
        SimpleNamespace(
            userParameters=params, allComponents=Collection([root]), rootComponent=root,
            unitsManager=SimpleNamespace(
                isValidExpression=validator or Mock(return_value=True),
                standardizeExpression=lambda expression, units: " ".join(expression.split()),
                evaluateExpression=evaluate or params.evaluate,
            ),
            computeAll=compute or Mock(return_value=True),
        ),
        core={"ValueInput": SimpleNamespace(
            createByString=lambda expression: SimpleNamespace(expression=expression)
        )},
    )


def test_add_parameters_preserves_existing_and_round_trips_definitions(monkeypatch):
    params = Parameters()
    params.items["length"] = Parameter("length", "12 mm", unit="mm")
    adapter = _adapter(monkeypatch, params)
    note = 'quoted "note" \\ with Ω and a newline\n'
    defs = [
        {"name": "length", "expression": "100 mm"},
        {"name": "width", "expression": "20 mm", "units": "mm", "comment": note},
        {"name": "height", "expression": "15", "units": None, "comment": None,
         "extra": {"flag": True}},
    ]

    result = p.add_parameters(adapter, defs)
    repeated = p.add_parameters(adapter, defs)

    assert result.ok is True
    assert result.result["added"] == [
        {"name": "width", "expression": "20 mm", "units": "mm", "value": 2, "value_type": "numeric"},
        {"name": "height", "expression": "15", "units": "", "value": 15, "value_type": "numeric"},
    ]
    assert result.result["skipped"] == [{"name": "length", "current_expression": "12 mm"}]
    assert result.result["total_params"] == 3
    assert [(name, value.expression, units, comment)
            for name, value, units, comment in params.add_calls] == [
        ("width", "20 mm", "mm", note), ("height", "15", "", ""),
    ]
    assert repeated.ok is True
    assert repeated.result["added"] == []
    assert repeated.result["skipped"] == [
        {"name": "length", "current_expression": "12 mm"},
        {"name": "width", "current_expression": "20 mm"},
        {"name": "height", "current_expression": "15"},
    ]
    assert params.items["length"].expression == "12 mm"


def test_build_update_quotes_safely(monkeypatch):
    params = Parameters()
    # These sentinel strings check the script boundary. The double does not
    # evaluate expressions or validate the Fusion parameter naming grammar.
    name, expr = 'parameter"\\name', 'text "quote" \\ Ω\n'
    param = Parameter(name, "100 mm", value=12, unit="mm")
    params.items[name] = param

    env = p.update_parameter(_adapter(monkeypatch, params, evaluate=lambda *_: 12), name, expr)

    assert env.ok is True
    assert env.result["before"] == "100 mm"
    assert env.result["after"] == expr
    assert env.result["name"] == name
    assert env.result["value"] == 12
    assert env.result["verification"]["changed"] is True
    assert param.expression == expr


@pytest.mark.parametrize("bad", ["pi", "e", "sin", "cos", "sqrt", "log"])
def test_reserved_math_name_is_rejected(bad):
    env = p.add_parameters(NoScriptAdapter(), [{"name": bad, "expression": "1 mm"}])
    assert env.error == "invalid_defs"
    assert f"{bad!r} is a reserved math identifier" in env.message


@pytest.mark.parametrize(
    ("defs", "message"),
    [
        ([], "defs must be non-empty"),
        ([{"name": "length"}], "defs[0].expression"),
        ([{"name": "2bad", "expression": "1 mm"}], "start with a letter or underscore"),
        ([{"name": "width", "expression": "1 mm", "comment": True}], "defs[0].comment"),
    ],
    ids=["empty", "missing_expression", "leading_digit", "invalid_comment"],
)
def test_add_parameters_short_circuits_on_bad_def(defs, message):
    env = p.add_parameters(NoScriptAdapter(), defs)
    assert env.ok is False
    assert env.error == "invalid_defs"
    assert message in env.message


def test_update_parameter_rejects_empty_name():
    env = p.update_parameter(NoScriptAdapter(), "", "100 mm")
    assert env.ok is False
    assert env.error == "invalid_name"


def test_update_parameter_returns_param_not_found_from_script(monkeypatch):
    params = Parameters()
    params.itemByName = Mock(return_value=None)
    env = p.update_parameter(_adapter(monkeypatch, params), "missing", "100 mm")

    assert env.ok is False
    assert env.error == "param_not_found"
    assert env.result == {"ok": False, "error": "param_not_found", "name": "missing"}
    params.itemByName.assert_called_once_with("missing")


def test_list_parameters_reads_collection_fields(monkeypatch):
    params = Parameters()
    params.items["width"] = SimpleNamespace(
        name="width", expression="50 mm", value=5, unit="mm", comment="outer"
    )
    params.items["count"] = SimpleNamespace(
        name="count", expression="3", value=3, unit="", comment=""
    )

    env = p.list_parameters(_adapter(monkeypatch, params))

    assert env.ok is True
    assert env.result["count"] == 2
    assert env.result["parameters"] == [
        {"name": "width", "expression": "50 mm", "value": 5, "units": "mm", "comment": "outer"},
        {"name": "count", "expression": "3", "value": 3, "units": "", "comment": ""},
    ]


def test_duplicate_names_refused_before_any_fusion_call():
    env = p.add_parameters(NoScriptAdapter(), [
        {"name": "width", "expression": "1 mm"}, {"name": "width", "expression": "2 mm"},
    ])
    assert env.error == "invalid_defs"
    assert "duplicated" in env.message


def test_add_accepts_dependencies_and_reverses_batch_on_invalid_expression(monkeypatch):
    params = Parameters()
    prior = Parameter("existing", "12 mm", unit="mm")
    params.items[prior.name] = prior

    def validate(expression, units):
        assert units == "mm"
        if expression == "base_width / 2":
            assert params.itemByName("base_width") is not None
        return expression != "3 deg"

    env = p.add_parameters(_adapter(monkeypatch, params, validator=validate), [
        {"name": "base_width", "expression": "20 mm", "units": "mm"},
        {"name": "lid_width", "expression": "base_width / 2", "units": "mm"},
        {"name": "bad", "expression": "3 deg", "units": "mm"},
    ])

    assert env.error == "invalid_expression"
    assert env.result["name"] == "bad"
    assert env.result["rollback"]["status"] == "verified"
    assert params.items == {"existing": prior}
    assert params.delete_calls == ["lid_width", "base_width"]


def test_add_removes_a_parameter_created_before_the_api_raises(monkeypatch):
    params = Parameters()
    original_add = params.add

    def partial_add(*args):
        original_add(*args)
        raise RuntimeError("injected API failure after creation")

    params.add = partial_add
    env = p.add_parameters(_adapter(monkeypatch, params), [
        {"name": "width", "expression": "3 mm", "units": "mm"},
    ])

    assert env.error == "parameter_add_failed"
    assert env.result["rollback"]["status"] == "verified"
    assert params.items == {}


def test_update_restores_expression_and_geometry_after_a_health_regression(monkeypatch):
    params = Parameters()
    param = Parameter("width", "12 mm", unit="mm", value=1.2)
    params.items[param.name] = param
    feature = SimpleNamespace(name="Box", healthState=0, errorOrWarningMessage="", isValid=True)
    body = SimpleNamespace(
        name="Base", volume=1.2, isValid=True,
        boundingBox=SimpleNamespace(minPoint=point(), maxPoint=point(1.2, 1, 1)),
    )
    root = root_with(name="Root", features=Collection([feature]), sketches=Collection(), bodies=[body])

    def compute():
        invalid = param.expression == "-4 mm"
        feature.healthState = 2 if invalid else 0
        feature.errorOrWarningMessage = "Negative extent" if invalid else ""
        param.value = -0.4 if invalid else 1.2
        body.volume = 0 if invalid else 1.2
        body.boundingBox.maxPoint.x = body.volume
        return True

    env = p.update_parameter(_adapter(monkeypatch, params, compute=compute, root=root), "width", "-4 mm")

    assert env.error == "feature_health_regressed"
    assert env.result["after"] == [2, "Negative extent"]
    assert env.result["rollback"]["status"] == "verified"
    assert param.expression == "12 mm"
    assert param.value == body.volume == body.boundingBox.maxPoint.x == 1.2


def test_update_leaves_unchanged_expression_without_recomputing(monkeypatch):
    params = Parameters()
    params.items["width"] = Parameter("width", "12 mm", unit="mm")
    compute = Mock(return_value=True)
    validator = Mock(return_value=True)
    env = p.update_parameter(_adapter(monkeypatch, params, compute=compute, validator=validator),
                             "width", "12 mm")

    assert env.ok
    assert env.result["verification"] == {"changed": False, "recomputed": False}
    compute.assert_not_called()
    validator.assert_not_called()


def test_update_rejects_wrong_units_before_writing(monkeypatch):
    params = Parameters()
    param = Parameter("width", "12 mm", unit="mm")
    params.items[param.name] = param
    validator = Mock(return_value=False)
    env = p.update_parameter(_adapter(monkeypatch, params, validator=validator), "width", "90 deg")

    assert env.error == "invalid_expression"
    assert "rollback" not in env.result
    assert param.expression == "12 mm"
    validator.assert_called_once_with("90 deg", "mm")


def test_text_parameters_are_read_and_updated_without_numeric_evaluation(monkeypatch):
    params = Parameters()
    param = Parameter("label", "'board'", unit="Text")
    param.valueType = 1
    param.textValue = "board"
    params.items[param.name] = param
    validator = Mock(side_effect=AssertionError("numeric evaluator cannot parse text"))
    adapter = _adapter(monkeypatch, params, validator=validator)

    assert p.list_parameters(adapter).result["parameters"][0]["value"] == "board"
    env = p.update_parameter(adapter, "label", "'board' + ' A'")
    assert env.ok
    assert env.result["after"] == "'board' + ' A'"


def test_add_reports_fusion_normalization_instead_of_echoing_input(monkeypatch):
    params = Parameters()
    original_add = params.add

    def normalize_add(*args):
        param = original_add(*args)
        param.expression = "20 mm"
        return param

    params.add = normalize_add
    env = p.add_parameters(_adapter(monkeypatch, params), [
        {"name": "width", "expression": "20   mm", "units": "mm"},
    ])

    assert env.ok
    assert env.result["added"][0]["expression"] == "20 mm"


def test_equal_numeric_value_cannot_hide_a_lost_formula_link(monkeypatch):
    params = Parameters()
    original_add = params.add

    def substitute_add(*args):
        param = original_add(*args)
        if param.name == "lid_width":
            param.expression = "10 mm"
        return param

    params.add = substitute_add
    env = p.add_parameters(_adapter(monkeypatch, params), [
        {"name": "base_width", "expression": "20 mm", "units": "mm"},
        {"name": "lid_width", "expression": "base_width / 2", "units": "mm"},
    ])

    assert env.error == "parameter_readback_failed"
    assert env.result["name"] == "lid_width"
    assert env.result["rollback"]["status"] == "verified"
    assert params.items == {}


def test_update_restores_prior_expression_when_fusion_stores_wrong_value(monkeypatch):
    params = Parameters()
    param = Parameter("width", "12 mm", unit="mm", value=1.2)
    params.items[param.name] = param

    def compute():
        param.value = 1.2
        return True

    env = p.update_parameter(_adapter(monkeypatch, params, compute=compute), "width", "20 mm")

    assert env.error == "parameter_readback_failed"
    assert env.result["rollback"]["status"] == "verified"
    assert param.expression == "12 mm"
