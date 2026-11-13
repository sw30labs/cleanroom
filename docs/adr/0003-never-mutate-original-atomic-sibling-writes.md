# ADR-0003: Never mutate the original: write a `.cleaned` sibling atomically, back up any previous output

## Status

Accepted (retrospective, inferred 2026-11-13)

## Context

A hygiene tool that edits user files must not destroy the original. README: "writing a `*.cleaned.*` sibling — never mutating your original in place"; files.py: "All writes go through safe atomic temp-file + rename; refuses symlinked destinations."

## Decision

Remediate writes to `<output_dir>/<stem>.cleaned<ext>` (default output dir `generated/`). If that path exists it is first copied to `.bak`. `_write_atomic()` writes to a `mkstemp` file in the target directory and `os.replace`s it, with a 512 MiB cap. Preview mode (`--preview` / dashboard toggle off) computes actions without writing.

## Consequences

### Positive

- Originals are never touched; repeated runs keep one backup.
- Crash-safe writes (no half-written outputs).

### Negative

- The symlink guard calls `path.resolve()` before `is_symlink()`, so a symlinked destination is followed rather than refused, contrary to the docstring (A-001).
- `safe_write_bytes` returns `None` despite the `-> Path` annotation (harmless today).

## Alternatives considered

- In-place editing with a backup (rejected by the README promise).

## Evidence

- cleanroom/files.py (_write_atomic, safe_write_text, safe_write_bytes, write_backup)
- cleanroom/phases/phase3_remediate.py (_suggest_output, preview)
- README.md (workflow table)
- commit cde8a38
