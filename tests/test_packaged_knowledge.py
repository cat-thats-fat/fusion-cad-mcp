"""Package knowledge resources are present and resolve in a checkout.

find_tool, find_pattern and find_gotcha answer from markdown shipped inside the
Python package, so a `pip install` works with no repo checkout. The canonical
copies live in ../src/ and build.ps1 syncs them across.

The isolated wheel installation smoke test is in wheel_knowledge_smoke.py.
"""

from __future__ import annotations

import pytest

from fusion_cad_mcp.tools.knowledge import PACKAGED_KNOWLEDGE_DIR

KNOWLEDGE_FILES = ["tools.md", "patterns.md", "gotchas.md"]

@pytest.mark.parametrize("name", KNOWLEDGE_FILES)
def test_packaged_copy_exists(name):
    packaged = PACKAGED_KNOWLEDGE_DIR / name
    assert packaged.exists(), (
        f"{name} is missing from the package at {PACKAGED_KNOWLEDGE_DIR}. "
        f"find_* would fall back to the repo copy in development and fail "
        f"outright for an installed user. Run build.ps1."
    )


def test_knowledge_tools_resolve_to_a_real_file():
    """Every find_* tool must have a document to search."""
    from fusion_cad_mcp.tools.knowledge import _resolve_skill_doc

    for name in KNOWLEDGE_FILES:
        assert _resolve_skill_doc(name) is not None, (
            f"_resolve_skill_doc({name!r}) found nothing. The matching find_* "
            f"tool would return a *_not_found error."
        )
