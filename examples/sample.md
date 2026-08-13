# Sample document for the Cleanroom smoke test and dashboard demo.
#
# This file carries two kinds of "marks" that Cleanroom's deterministic path
# can verify and remove:
#   1. a leading AI-provenance frontmatter key (ai: true / generator)
#   2. invisible Unicode characters (real zero-width spaces) inline below

---
title: Example Report
ai: true
generator: deepseek
---

# Quarterly Operations Brief

This is a sample report that has been generated with the help of an AI
assistant and carries synthetic provenance markers.

## Highlights

- Revenue grew 12% year over year
- Operational incidents declined 8%
- Client satisfaction held steady at 4.2/5

## The invisible marks

The two lines below contain zero-width spaces (U+200B) that a provenance
watermark may inject. They are invisible to the eye but present in the bytes.

This line has an invisible space after the word invisible​.
Another marker​is placed mid-sentence here to exercise the scanner.

## Conclusion

Cleanroom should classify this file, detect the Unicode marks, strip them, and
flag the `ai:` / `generator:` frontmatter keys for removal — all
deterministically, with no model required.