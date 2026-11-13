# ADR-0005: Strip AI-provenance keys from Markdown YAML frontmatter, allowing a comment header before it

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

Generated Markdown often carries keys such as `ai`, `model`, `generator` in frontmatter. The sample file places comment lines before the `---` block ("some tools emit a comment header before frontmatter").

## Decision

For `.md`/`.markdown`, after Unicode cleanup, `_strip_frontmatter_ai_keys()` finds the first `---` preceded only by blank or `#` lines, finds the closing `---`, and drops lines whose key (case-insensitive) is in `ai, aigen, aigenerated, generator, model, modelname, created_by_ai, llm, watermark, provenance`.

## Consequences

### Positive

- Deterministic and counted (`removed_keys`).

### Negative

- The rewritten text starts at `---`, so any comment header before the frontmatter is dropped from the output (A-008).
- A line-based parser: nested YAML or multi-line values under a removed key are not handled.
- A legitimate key named `model` (e.g. in a technical doc) is removed.

## Alternatives considered

- Full YAML parsing (not chosen; inferred, no rationale recorded).

## Evidence

- cleanroom/files.py (_AI_FRONTMATTER_KEYS, _strip_frontmatter_ai_keys)
- examples/sample.md
- tests/test_core.py::test_remediate_strips_ai_frontmatter
- commit cde8a38
