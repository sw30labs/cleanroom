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


def test_detect_and_strip_missing_format_and_spaces():
    """Holes in the old hand list: word joiner, en/hair space, soft hyphen, CGJ."""
    text = "a\u2060b\u2002c\u200ad\u00ade\u034ff"
    report = detect_marks(text)
    assert report["class"] == "marked"
    assert report["total_marked_chars"] == 5
    assert strip_marks(text) == "ab c def"


def test_strip_marks_maps_nbsp_and_line_separator():
    assert strip_marks("hello\u00a0world") == "hello world"
    assert strip_marks("one\u2028two") == "one\ntwo"


def test_strip_marks_hangul_filler_and_variation_selector():
    assert strip_marks("a\u3164b\ufe0fc") == "abc"


def test_visible_typography_and_letters_fold_to_ascii():
    text = "Jalapeño — “custom AI silicon”"
    report = detect_marks(text)
    assert report["class"] == "marked"
    assert report["visible_fold_chars"] > 0
    assert report["invisible_chars"] == 0
    assert strip_marks(text) == 'Jalapeno -- "custom AI silicon"'


def test_fold_keeps_non_latin_scripts():
    text = "你好世界"
    assert detect_marks(text)["class"] == "clean"
    assert strip_marks(text) == text


def test_remediate_folds_visible_unicode():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "typo.md"
        p.write_text("Jalapeño — “win”\n", encoding="utf-8")
        prof = classify(p)
        r = remediate_deterministic(prof, p.read_bytes())
        assert any(a["op"] == "fold_visible_unicode" for a in r["actions"])
        assert r["content"] == 'Jalapeno -- "win"\n'
        assert detect_marks(r["content"])["total_marked_chars"] == 0


def test_category_fallback_catches_unlisted_format_chars():
    # Arabic letter mark is Cf; musical begin beam is Cf outside the BMP list.
    text = "x\u061cy\U0001D173z"
    assert detect_marks(text)["total_marked_chars"] == 2
    assert strip_marks(text) == "xyz"


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