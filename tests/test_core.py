"""Cleanroom — core tests.

Covers classification, mark detection, deterministic remediation, and the
full offline pipeline run.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cleanroom.files import (
    classify,
    detect_marks,
    remediate_deterministic,
    safe_write_text,
    strip_marks,
)


def test_detect_marks_finds_zwsp():
    report = detect_marks("hello\u200bworld")
    assert report["total_marked_chars"] == 1
    assert report["class"] == "marked"


def test_detect_marks_clean():
    report = detect_marks("plain visible text")
    assert report["total_marked_chars"] == 0
    assert report["class"] == "clean"


def test_strip_marks_removes_invisible():
    cleaned = strip_marks("a\u200bb\u200cc\u200dd")
    assert cleaned == "abcd"


def test_strip_marks_removes_tag_chars():
    tag = "\U000E0000"  # tag char start
    cleaned = strip_marks(f"x{tag}y")
    assert cleaned == "xy"


def test_classify_text():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "notes.md"
        p.write_text("# hello\nworld\n")
        prof = classify(p)
        assert prof.kind in ("text",)
        assert prof.is_text_like


def test_remediate_text_strips_marks():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "marked.md"
        content = "title\u200b\u200bbody with \u200cmarks"
        p.write_text(content, encoding="utf-8")
        prof = classify(p)
        r = remediate_deterministic(prof, p.read_bytes())
        assert any(a["op"] == "strip_invisible_unicode" for a in r["actions"])
        assert "\u200b" not in r["content"]
        assert "\u200c" not in r["content"]


def test_remediate_strips_ai_frontmatter():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "doc.md"
        p.write_text("---\ntitle: T\nai: true\ngenerator: gpt\n---\nbody\n")
        prof = classify(p)
        r = remediate_deterministic(prof, p.read_bytes())
        ai = [a for a in r["actions"] if a["op"] == "strip_frontmatter_ai_keys"]
        assert ai, f"expected frontmatter action, got {r['actions']}"
        assert "ai: true" not in r["content"]
        assert "generator: gpt" not in r["content"]
        assert "title: T" in r["content"]


def test_safe_write_text_atomic():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.txt"
        safe_write_text(out, "hello")
        assert out.read_text() == "hello"


def test_classify_missing():
    prof = classify(Path("/nonexistent/file.txt"))
    assert not prof.exists


def test_pipeline_runs_offline(tmp_path: Path):
    """Full LangGraph run offline on a marked file (skip_llm=True)."""
    from cleanroom.pipeline import run_cleanroom

    inp = tmp_path / "in.md"
    inp.write_text("---\nai: true\n---\nhello\u200bworld\n")
    result = run_cleanroom(inp, output_dir=tmp_path / "out", apply=True, skip_llm=True)
    assert result.state.get("analyze", {}).get("status") in ("ok", "degraded")
    qa = result.state.get("qa", {})
    # deterministic path should have removed the invisible mark
    assert qa.get("deterministic", {}).get("marks_after", 0) == 0
    # a cleaned file should have been written
    assert (tmp_path / "out" / "in.cleaned.md").exists()