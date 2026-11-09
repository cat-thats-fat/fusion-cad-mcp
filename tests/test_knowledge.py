"""Tier 1b corpus search regression tests + unit tests on the scorers.

Search tests use a small synthetic corpus so they run without Autodesk content.
They do not evaluate ranking against a real, locally acquired corpus.
"""

from __future__ import annotations

import json

import pytest

from fusion_cad_mcp.tools import knowledge as kn


@pytest.fixture
def synthetic_corpus(tmp_path, monkeypatch):
    """Small invented corpus: deterministic, portable, and no Autodesk content."""
    records = [
        {"slug": "MeshBody_calculateCollisionsWithRay.htm", "title": "MeshBody ray method", "body_md": "ray collisions", "namespace": "fusion"},
        {"slug": "UserCoordinateSystem.htm", "title": "UserCoordinateSystem", "body_md": "coordinate system", "namespace": "fusion"},
        {"slug": "FusionExport.htm", "title": "Export", "body_md": "export", "namespace": "fusion"},
        {"slug": "CoreExport.htm", "title": "Export", "body_md": "export", "namespace": "core"},
    ]
    (tmp_path / "corpus.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    monkeypatch.setattr(kn, "_resolve_corpus_dir", lambda: tmp_path)
    kn._api_index.cache_clear()
    yield tmp_path
    kn._api_index.cache_clear()


# ---------- pure-unit (no corpus required) ----------


def test_tokenize_strips_punct_and_lowercases():
    assert kn._tokenize("MeshBody.calculateCollisionsWithRay") == [
        "meshbody",
        "calculatecollisionswithray",
    ]


def test_tokenize_empty_returns_empty():
    assert kn._tokenize("") == []
    assert kn._tokenize("   ") == []


def test_score_record_exact_slug_match_dominates():
    rec_match = {
        "slug": "MeshBody_calculateCollisionsWithRay.htm",
        "title": "MeshBody.calculateCollisionsWithRay Method",
        "body_md": "Finds all points that are intersected by the specified ray.",
    }
    rec_unrelated = {
        "slug": "AccessibilityAnalysis.htm",
        "title": "AccessibilityAnalysis Object",
        "body_md": "Accessibility analysis represents results of an accessibility check.",
    }
    terms = kn._tokenize("calculateCollisionsWithRay")
    assert kn._score_record(terms, rec_match) > kn._score_record(terms, rec_unrelated)


def test_score_record_preview_penalty():
    rec = {
        "slug": "UserCoordinateSystem.htm",
        "title": "UserCoordinateSystem Object",
        "body_md": "represents a user coordinate system",
        "is_preview": True,
    }
    rec_stable = {**rec, "is_preview": False}
    terms = kn._tokenize("UserCoordinateSystem")
    assert kn._score_record(terms, rec_stable) > kn._score_record(terms, rec)


def test_parse_sections_splits_on_hash_headings():
    md = "# Title\nintro\n## A\nbody of A\n## B\nbody of B\n"
    sections = kn._parse_sections(md)
    headings = [s["heading"] for s in sections]
    assert headings == ["Title", "A", "B"]


def test_snippet_returns_context_around_match():
    body = "alpha beta gamma delta epsilon foo zeta eta theta"
    s = kn._snippet(body, ["foo"], window=20)
    assert "foo" in s
    assert len(s) <= 60


def test_snippet_falls_back_to_head_when_no_match():
    body = "no match here at all just plain text"
    s = kn._snippet(body, ["xyz"], window=10)
    assert s.startswith("no match")


# ---------- corpus search against portable synthetic records ----------


def test_find_api_returns_meshbody_ray_for_known_query(synthetic_corpus):
    env = kn.find_api("MeshBody calculateCollisionsWithRay")
    assert env.ok is True
    hits = env.result["hits"]
    assert len(hits) > 0
    top_slug = hits[0]["slug"]
    assert "calculateCollisionsWithRay" in top_slug


def test_find_api_returns_ucs_for_user_coordinate_system_query(synthetic_corpus):
    env = kn.find_api("UserCoordinateSystem")
    assert env.ok is True
    slugs = [h["slug"] for h in env.result["hits"]]
    assert any("UserCoordinateSystem" in s for s in slugs)


def test_find_api_namespace_filter(synthetic_corpus):
    env = kn.find_api("export", namespace="fusion", limit=10)
    assert env.ok is True
    assert env.result["matched"] == 1
    assert [h["slug"] for h in env.result["hits"]] == ["FusionExport.htm"]


def test_find_api_empty_query_is_structured_error(synthetic_corpus):
    env = kn.find_api("   ")
    assert env.ok is False
    assert env.error == "empty_query"


def test_find_api_returns_corpus_not_built_when_missing(monkeypatch, tmp_path):
    """Force the resolver to find nothing; verify the structured error."""
    monkeypatch.setattr(kn, "_resolve_corpus_dir", lambda: None)
    kn._api_index.cache_clear()
    env = kn.find_api("anything")
    assert env.ok is False
    assert env.error == "corpus_not_built"
    kn._api_index.cache_clear()


# ---------- patterns / gotchas (regression if files present) ----------


def test_find_pattern_returns_hits_for_constrained_rect_query():
    env = kn.find_pattern("constrained rectangle")
    assert env.ok is True
    assert env.result["matched"] > 0


def test_find_gotcha_returns_lock_badge_g9():
    env = kn.find_gotcha("lock badge")
    assert env.ok is True
    headings = [h["heading"].lower() for h in env.result["hits"]]
    assert any("lock-badge" in h or "lock badge" in h or "g9" in h for h in headings)


def test_find_tool_import_geometry_reports_active_design_and_target(monkeypatch):
    monkeypatch.setattr(kn, "_resolve_skill_doc", lambda name: kn.PACKAGED_KNOWLEDGE_DIR / name)
    env = kn.find_tool("import_geometry")
    assert env.ok is True
    hit = env.result["hits"][0]
    assert "target_component=None" in hit["heading"]
    assert "active design" in hit["snippet"]
    assert "importToNewDocument" in hit["snippet"]


@pytest.mark.parametrize("present", [True, False])
def test_capabilities_are_offline_compact_and_reflect_corpus(monkeypatch, tmp_path, present):
    monkeypatch.setattr(kn, "_resolve_corpus_dir", lambda: tmp_path if present else None)
    result = kn.get_capabilities()
    assert result.ok and result.result["local_api_corpus_present"] is present
    assert result.result["api_documentation_fallback"]["queryType"] == "apiDocumentation"
    assert "f3d" in result.result["active_design_import_formats"]
    assert result.result["separate_document_archive_format"] == "f3d"
    assert result.result["solidworks_import_supported"] is False
    assert result.result["receipts"]["normal_manifest_limit"] == 1968
    assert result.result["receipts"]["recovery_probe_reserve"] == 32
    assert len(json.dumps(result.result)) < 3000


def test_every_registered_tool_has_a_discoverable_reference_heading():
    import ast
    import re
    from pathlib import Path

    package = Path(kn.__file__).resolve().parent.parent
    tree = ast.parse((package / "server.py").read_text(encoding="utf-8"))
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                     and dec.func.attr == "tool" for dec in node.decorator_list)}
    reference = (kn.PACKAGED_KNOWLEDGE_DIR / "tools.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^#{1,6}[^\n]*", reference, re.MULTILINE))
    missing = {name for name in names if not any(re.search(r"`" + re.escape(name) + r"\(", h) for h in headings)}
    assert not missing, f"Tools missing reference headings: {sorted(missing)}"
