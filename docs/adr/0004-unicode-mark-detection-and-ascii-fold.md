# ADR-0004: Detect marks by named sets plus Unicode-category fallback; strip invisibles and fold visible Latin typography to ASCII

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

Invisible Unicode (zero-width, bidi, tags, exotic spaces) is a common watermark/provenance channel. Commit f0ac0be ("expand files.py helpers") replaced a hand list that had holes (test docstring: "Holes in the old hand list") and added visible-typography folding.

## Decision

`_is_invisible()` flags a named set, tag characters U+E0000–E007F, variation selectors, and any character in categories Cf/Zl/Zp/Cc or non-ASCII Zs (keeping tab/CR/LF). Exotic spaces become a space, line/paragraph separators a newline, the rest are dropped. `_fold_visible()` maps dashes, quotes, ellipses and bullets via `VISIBLE_FOLD`, special Latin letters via `LATIN_FOLD`, and other `LATIN*` characters via NFKD minus combining marks; non-Latin scripts are left alone. `detect_marks()` reports invisible vs visible-fold counts and the top codepoints.

## Consequences

### Positive

- Category fallback catches unlisted format characters (tested with U+061C and U+1D173).
- Counts are exact, so removal is verifiable (README "✅ counted").

### Negative

- Folding is lossy: `ñ → n`, `— → --`, curly quotes → straight, so names and non-English Latin text change; it is still classed as `safe` (A-007).
- Emoji ZWJ sequences and variation selectors are stripped, which alters emoji rendering.

## Alternatives considered

- Hand-maintained codepoint list only (the pre-f0ac0be approach; replaced because it missed characters).

## Evidence

- cleanroom/files.py (INVISIBLE, VISIBLE_FOLD, LATIN_FOLD, _is_invisible, _fold_visible, detect_marks, strip_marks)
- tests/test_core.py (mark tests)
- README.md ("What it removes")
- commits cde8a38, f0ac0be
