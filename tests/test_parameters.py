"""Parameter validation and generated script behavior against a local collection."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.tools import parameters as p
from tests._assembly_fakes import NoScriptAdapter, ScriptAdapter


class Parameters:
    def __init__(self):
        self.items = {}
        self.add_calls = []

    def itemByName(self, name):
        return self.items.get(name)

    def item(self, index):
        return list(self.items.values())[index]

    @property
    def count(self):
        return len(self.items)

    def add(self, name, value_input, units, comment):
        self.add_calls.append((name, value_input, units, comment))
        self.items[name] = SimpleNamespace(
            name=name, expression=value_input.expression, value=1.25, unit=units, comment=comment
        )


def _adapter(monkeypatch, params):
    return ScriptAdapter(
        monkeypatch,
        SimpleNamespace(userParameters=params),
        core={"ValueInput": SimpleNamespace(
            createByString=lambda expression: SimpleNamespace(expression=expression)
        )},
    )


def test_add_parameters_preserves_existing_and_round_trips_definitions(monkeypatch):
    params = Parameters()
    params.items["length"] = SimpleNamespace(expression="12 mm")
    adapter = _adapter(monkeypatch, params)
    note = 'quoted "note" \\ with Ω and a newline\n'
    defs = [
        {"name": "length", "expression": "100 mm"},
        {"name": "width", "expression": "20 mm", "units": "mm", "comment": note},
        {"name": "height", "expression": "15 mm", "units": None, "comment": None,
         "extra": {"flag": True}},
    ]

    result = p.add_parameters(adapter, defs)
    repeated = p.add_parameters(adapter, defs)

    assert result.ok is True
    assert result.result["added"] == [
        {"name": "width", "expression": "20 mm"}, {"name": "height", "expression": "15 mm"},
    ]
    assert result.result["skipped"] == [{"name": "length", "current_expression": "12 mm"}]
    assert result.result["total_params"] == 3
    assert [(name, value.expression, units, comment)
            for name, value, units, comment in params.add_calls] == [
        ("width", "20 mm", "mm", note), ("height", "15 mm", "", ""),
    ]
    assert repeated.ok is True
    assert repeated.result["added"] == []
    assert repeated.result["skipped"] == [
        {"name": "length", "current_expression": "12 mm"},
        {"name": "width", "current_expression": "20 mm"},
        {"name": "height", "current_expression": "15 mm"},
    ]
    assert params.items["length"].expression == "12 mm"


def test_build_update_quotes_safely(monkeypatch):
    params = Parameters()
    # These sentinel strings check the script boundary. The double does not
    # evaluate expressions or validate the Fusion parameter naming grammar.
    name, expr = 'parameter"\\name', 'text "quote" \\ Ω\n'
    param = SimpleNamespace(expression="100 mm", value=12)
    params.items[name] = param

    env = p.update_parameter(_adapter(monkeypatch, params), name, expr)

    assert env.ok is True
    assert env.result == {"ok": True, "name": name, "before": "100 mm", "after": expr, "value": 12}
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
