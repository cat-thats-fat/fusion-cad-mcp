"""Missing script results must never turn into silent mutation retries."""

from unittest.mock import Mock

import pytest

from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.envelope import from_fusion_response
from fusion_cad_mcp.tools import (
    execute,
    inspection,
    io,
    parameters,
    placement,
    primitives,
    sketch,
    validation,
)


def _empty_script_adapter():
    adapter = Mock(spec=FusionAdapter)
    adapter.execute_script.return_value = from_fusion_response({
        "result": {"content": [{"type": "text", "text": '{"success":true,"message":""}'}]},
    })
    return adapter


@pytest.mark.parametrize(("invoke", "error"), [
    (lambda a: placement.create_component(a, "Fixture"), "placement_parse_failed"),
    (lambda a: primitives.create_parametric_enclosure(a, "Case", "60 mm", "40 mm", "25 mm", "2 mm", "2 mm"),
     "enclosure_parse_failed"),
    (lambda a: parameters.update_parameter(a, "width", "30 mm"), "parse_failed"),
    (lambda a: sketch.add_line(a, "Profile", [0, 0], [1, 1]), "add_line_parse_failed"),
])
def test_missing_mutation_readback_is_unknown_and_never_replayed(invoke, error):
    adapter = _empty_script_adapter()

    result = invoke(adapter)

    assert not result.ok
    assert result.error == error
    assert result.state == {
        "failure_kind": "protocol", "execution_outcome": "unknown",
        "model_may_have_changed": True, "retry_safe": False,
    }
    adapter.execute_script.assert_called_once()


@pytest.mark.parametrize(("invoke", "error"), [
    (lambda a: inspection.inspect_assembly(a), "inspect_assembly_parse_failed"),
    (lambda a: validation.validate_design(a, [{"type": "clearance", "body_a": "Lid", "body_b": "PCB", "min_mm": 1}]),
     "validation_parse_failed"),
    (lambda a: parameters.list_parameters(a), "parse_failed"),
])
def test_missing_read_result_can_be_retried_without_model_effects(invoke, error):
    adapter = _empty_script_adapter()

    result = invoke(adapter)

    assert not result.ok
    assert result.error == error
    assert result.state == {
        "failure_kind": "protocol", "execution_outcome": "unknown",
        "model_may_have_changed": False, "retry_safe": True,
    }
    adapter.execute_script.assert_called_once()
    assert adapter.execute_script.call_args.kwargs == {"read_only": True}


def test_export_can_write_files_even_when_the_model_is_unchanged(tmp_path):
    adapter = _empty_script_adapter()

    result = io.export(adapter, "step", str(tmp_path / "case.step"))

    assert result.error == "export_parse_failed"
    assert result.state["model_may_have_changed"] is False
    assert result.state["retry_safe"] is False
    adapter.execute_script.assert_called_once()


def test_raw_script_may_successfully_return_without_printing():
    adapter = _empty_script_adapter()

    result = execute.run(adapter, "def run(_ctx):\n    pass\n")

    assert result.ok
    assert result.state is None
    adapter.execute_script.assert_called_once()
