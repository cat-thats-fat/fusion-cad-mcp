"""Cache identities include the exact native helper source used by the generator."""

import hashlib

import pytest

from fusion_cad_mcp import workspace, workspace_native


@pytest.mark.parametrize("helper", ["RESOLVER_SCRIPT", "_PARAMETER_SCRIPT", "_VALIDATION_SCRIPT", "_WORKSPACE_SCRIPT"])
def test_generated_helper_change_invalidates_environment_and_verifier(monkeypatch, helper):
    native = {"environment": {"fusion_version": "test", "registry_version": 1}}
    checks = [{"id": "width"}]
    before = workspace_native.runtime_digest()
    assert before == hashlib.sha256(workspace_native.build_workspace_script("bind", {}).encode()).hexdigest()
    prior_environment = workspace._environment(native)
    prior_verifier = workspace._verifier(checks)
    monkeypatch.setattr(workspace_native, helper, "\n# changed embedded helper\n" + getattr(workspace_native, helper))
    assert workspace_native.runtime_digest() != before
    assert workspace._environment(native) != prior_environment
    assert workspace._verifier(checks) != prior_verifier
