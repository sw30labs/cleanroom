# ADR-0006: Binary, image, PDF and office files: detect and flag metadata, copy through unchanged, never claim removal

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

README: "this path never claims it removed what it did not"; container metadata strip is "pending a format-aware tool (exiftool/c2patool)". Every action carries a `verifiable` flag.

## Decision

`classify()` uses magic bytes (PNG, JPEG, PDF, ZIP, WEBP) and extensions; DOCX/ODT/EPUB become `office`. Office text and core properties are extracted from the zip; PDFs use `pypdf` if installed, else a naive printable-byte scan. For non-text kinds, Remediate emits `metadata_note` / `metadata_strip_pending` actions (`verifiable: false`) and writes a byte-identical copy as the `.cleaned` output.

## Consequences

### Positive

- No risky in-place binary edits.
- Users see exactly what was not done.

### Negative

- The `.cleaned` copy of a binary is identical to the input, which could be mistaken for a cleaned file.
- The baseline plan still lists a `strip_metadata` action as `safe`/`low` risk for these kinds even though nothing implements it (A-016).

## Alternatives considered

- Shelling out to exiftool/c2patool (named as the future path, not implemented).

## Evidence

- cleanroom/files.py (classify, _magic_kind, extract_text, _extract_office, _extract_pdf, remediate_deterministic)
- cleanroom/phases/phase3_remediate.py (binary pass-through)
- cleanroom/phases/phase2_recommend.py (_baseline_plan)
- README.md ("honest limits" table)
- commit cde8a38
