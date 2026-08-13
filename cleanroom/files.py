"""Cleanroom — File Hygiene Core

Classify, extract, and deterministically remediate any input file.

All writes go through safe atomic temp-file + rename; refuses symlinked destinations.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Mark detection ──────────────────────────────────────────────

# Invisible / exotic Unicode that AI provenance marks commonly inject.
INVISIBLE = {
    "\u200b",  # ZWSP
    "\u200c",  # ZWNJ
    "\u200d",  # ZWJ
    "\u200e",  # LRM
    "\u200f",  # RLM
    "\u202a",  # LRE
    "\u202b",  # RLE
    "\u202c",  # PDF
    "\u202d",  # LRO
    "\u202e",  # RLO
    "\u2066",  # LRI
    "\u2067",  # RLI
    "\u2068",  # FSI
    "\u2069",  # PDI
    "\u206a",  # ISS
    "\u206b",  # ASS
    "\u206c",  # IAFS
    "\u206d",  # AAFS
    "\u206e",  # NAFFS
    "\u206f",  # NADS
    "\ufeff",  # BOM/ZWNBSP
    "\u00a0",  # NBSP (optionally exotic)
    "\u2007",  # figure space
    "\u202f",  # narrow NBSP
    "\u205f",  # MMSP
    "\u3000",  # ideographic space
}

# Zero-width tag characters (U+E0000+): also used for watermark payloads.
TAG_CHARS_START = 0xE0000
TAG_CHARS_END = 0xE007F


def _is_invisible(ch: str) -> bool:
    if ch in INVISIBLE:
        return True
    cp = ord(ch)
    return TAG_CHARS_START <= cp <= TAG_CHARS_END


def detect_marks(text: str) -> dict[str, Any]:
    """Return a structured report of editable Unicode marks in ``text``."""
    by_cp: dict[str, int] = {}
    for ch in text:
        if _is_invisible(ch):
            by_cp[ch] = by_cp.get(ch, 0) + 1
    total = sum(by_cp.values())
    examples = sorted(by_cp.items(), key=lambda kv: -kv[1])[:10]
    return {
        "total_marked_chars": total,
        "distinct_codepoints": len(by_cp),
        "codepoints": [
            {"codepoint": f"U+{ord(c):04X}", "char": repr(c), "count": n}
            for c, n in examples
        ],
        "class": "clean" if total == 0 else "marked",
    }


def strip_marks(text: str) -> str:
    """Remove invisible Unicode and tag characters (lossless for visible text)."""
    return "".join(ch for ch in text if not _is_invisible(ch))


# ── File classification ─────────────────────────────────────────

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".xml", ".svg",
    ".json", ".yaml", ".yml", ".csv", ".tsv", ".py", ".js", ".ts",
    ".css", ".sh", ".c", ".h", ".cpp", ".java", ".go", ".rs", ".rb",
    ".sql", ".toml", ".ini", ".cfg", ".env", ".log",
}

_METADATA_CONTAINERS = {
    ".png": "PNG (C2PA/EXIF/tEXt chunks)",
    ".jpg": "JPEG (APP1/EXIF/XMP)",
    ".jpeg": "JPEG (APP1/EXIF/XMP)",
    ".svg": "SVG (metadata/XMP)",
    ".pdf": "PDF (XMP/doc metadata)",
    ".docx": "DOCX (docProps/customXml)",
    ".odt": "ODT (meta.xml)",
    ".html": "HTML (meta/JSON-LD)",
    ".htm": "HTML (meta/JSON-LD)",
    ".md": "Markdown (YAML frontmatter)",
    ".markdown": "Markdown (YAML frontmatter)",
    ".json": "JSON (embedded metadata)",
}


@dataclass
class FileProfile:
    """What Cleanroom knows about an input file before any phase runs."""
    path: Path
    exists: bool
    size_bytes: int
    extension: str
    kind: str  # text | office | image | pdf | archive | binary | unknown
    is_text_like: bool
    container_note: str
    encodings_tried: list[str] = field(default_factory=list)
    detected_charset: str = ""
    extracted_text: str = ""
    extraction_error: str = ""
    mark_report: dict[str, Any] = field(default_factory=dict)
    metadata_hints: list[str] = field(default_factory=list)


def _magic_kind(raw: bytes) -> str | None:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if raw[:3] == b"\xff\xd8\xff":
        return "image"
    if raw[:5] == b"%PDF-":
        return "pdf"
    if raw[:4] == b"PK\x03\x04":
        return "archive"  # docx/odt/epub/zip
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image"
    return None


def classify(path: Path) -> FileProfile:
    """Build a FileProfile for ``path`` without reading the whole file."""
    exists = path.is_file()
    size = path.stat().st_size if exists else 0
    ext = path.suffix.lower()
    container_note = _METADATA_CONTAINERS.get(ext, "")

    kind = "unknown"
    is_text_like = False
    if exists and size > 0:
        with open(path, "rb") as fh:
            head = fh.read(12)
        m = _magic_kind(head)
        if m:
            kind = m
        elif ext in _TEXT_EXTENSIONS or ext in _METADATA_CONTAINERS:
            kind = "text"
            is_text_like = True
        elif m == "archive":
            kind = "archive"
        elif b"\x00" in head:
            kind = "binary"
        else:
            kind = "text"
            is_text_like = True
    elif exists:
        kind = "text"

    if kind == "archive" and ext in (".docx", ".odt", ".epub"):
        kind = "office"
        container_note = _METADATA_CONTAINERS.get(ext, "")

    return FileProfile(
        path=path,
        exists=exists,
        size_bytes=size,
        extension=ext,
        kind=kind,
        is_text_like=is_text_like,
        container_note=container_note,
    )


# ── Extraction ──────────────────────────────────────────────────

_MAX_EXTRACT_CHARS = 500_000


def _decode_text(data: bytes) -> tuple[str, str]:
    """Decode bytes to text, trying UTF-8 then common fallbacks."""
    attempts = [("utf-8", "strict"), ("utf-8-sig", "strict"), ("latin-1", "strict")]
    for enc, mode in attempts:
        try:
            return data.decode(enc, mode), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8-replace"


def extract_text(profile: FileProfile, data: bytes | None = None) -> FileProfile:
    """Populate extracted_text for a file. Best-effort; never throws."""
    try:
        if data is None:
            data = profile.path.read_bytes()
        if profile.kind == "office":
            _extract_office(profile, data)
            return profile
        if profile.kind == "pdf":
            profile.extracted_text = _extract_pdf(profile.path)
            profile.detected_charset = "pdf-inline"
            return profile
        if profile.kind == "image":
            profile.extraction_error = "image content — no text extraction"
            return profile
        if profile.kind in ("archive", "binary"):
            profile.extraction_error = f"{profile.kind} container — binary pass-through"
            return profile
        text, enc = _decode_text(data)
        profile.detected_charset = enc
        profile.encodings_tried = ["utf-8", "utf-8-sig", "latin-1"]
        profile.extracted_text = text[:_MAX_EXTRACT_CHARS]
        profile.mark_report = detect_marks(profile.extracted_text)
        return profile
    except Exception as exc:  # noqa: BLE001 — extraction must never kill a run
        profile.extraction_error = f"{type(exc).__name__}: {exc}"
        return profile


def _extract_office(profile: FileProfile, data: bytes) -> None:
    """Pull text out of DOCX/ODT zips. Returns extracted text + metadata hints."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            text_parts: list[str] = []
            hints: list[str] = []
            if profile.extension == ".odt":
                target = "content.xml"
                meta = "meta.xml"
            else:
                target = "word/document.xml"
                meta = "docProps/core.xml"
            if meta in names:
                try:
                    raw = zf.read(meta).decode("utf-8", "replace")
                    hints.append(f"doc-props: {_snippet(raw)}")
                except KeyError:
                    pass
            if target in names:
                xml = zf.read(target).decode("utf-8", "replace")
                # crude but robust text strip for w:t / text:node
                text_parts.append(_xml_text(xml))
            profile.extracted_text = "\n".join(text_parts)[:_MAX_EXTRACT_CHARS]
            profile.metadata_hints = hints
            profile.mark_report = detect_marks(profile.extracted_text)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        profile.extraction_error = f"{type(exc).__name__}: {exc}"


def _xml_text(xml: str) -> str:
    """Very light XML-to-text: drop tags, unescape entities."""
    xml = re.sub(r"<[^>]+>", " ", xml)
    xml = (
        xml.replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&apos;", "'")
        .replace("&amp;", "&")
    )
    return re.sub(r"[ \t]+", " ", xml).strip()


def _snippet(xml: str, length: int = 400) -> str:
    flat = re.sub(r"<[^>]+>", " ", xml)
    return re.sub(r"\s+", " ", flat).strip()[:length]


def _extract_pdf(path: Path) -> str:
    """Extract PDF text without a heavy dependency.

    Tries pypdf if installed; falls back to a naive stream scan so offline runs
    still produce *something* for the LLM to reason about.
    """
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        text = "\n".join(parts)
        if text.strip():
            return text[:_MAX_EXTRACT_CHARS]
    except Exception:  # noqa: BLE001
        pass
    # naive fallback
    raw = path.read_bytes()
    text = raw.decode("latin-1", "replace")
    return re.sub(r"[^ -~]+", " ", text)[:_MAX_EXTRACT_CHARS]


# ── Deterministic remediation ──────────────────────────────────

METADATA_STRIP_BY_EXT: dict[str, list[str]] = {
    ".md": ["frontmatter"],
    ".markdown": ["frontmatter"],
}


def remediate_deterministic(profile: FileProfile, data: bytes | None = None) -> dict[str, Any]:
    """Apply safe, deterministic remediation for a file.

    Returns an action log. For text: strips invisible Unicode marks. For
    metadata containers: strips frontmatter AI keys (md), and reports the
    container types whose metadata segments need exiftool/c2patool for a full
    strip (kept honest: this path never claims it removed what it did not).
    """
    if data is None:
        data = profile.path.read_bytes()
    actions: list[dict[str, Any]] = []

    if profile.is_text_like or profile.kind == "text":
        text, enc = _decode_text(data)
        before = detect_marks(text)
        cleaned = strip_marks(text)
        after = detect_marks(cleaned)
        if before["total_marked_chars"] > 0:
            actions.append(
                {
                    "op": "strip_invisible_unicode",
                    "removed": before["total_marked_chars"],
                    "distinct": before["distinct_codepoints"],
                    "verifiable": True,
                }
            )
        if profile.extension in METADATA_STRIP_BY_EXT:
            md_actions = _strip_frontmatter_ai_keys(cleaned)
            cleaned = md_actions["text"]
            actions.extend(md_actions["actions"])
        if before["total_marked_chars"] == 0 and not actions:
            actions.append(
                {"op": "noop", "reason": "no editable Unicode marks or AI frontmatter", "verifiable": True}
            )
        return {"content": cleaned, "encoding": enc, "actions": actions, "after_marks": after}

    # Binary / image / pdf / office: deterministic in-place edits are risky.
    for hint in profile.metadata_hints or []:
        actions.append({"op": "metadata_note", "hint": hint, "verifiable": False})
    actions.append(
        {
            "op": "metadata_strip_pending",
            "note": (
                f"{profile.kind} metadata strip requires a format-aware tool "
                "(exiftool / c2patool / zip rewrite) — see Recommend phase."
            ),
            "verifiable": False,
        }
    )
    return {"content": None, "encoding": "", "actions": actions, "after_marks": profile.mark_report}


_AI_FRONTMATTER_KEYS = (
    "ai", "aigen", "aigenerated", "generator", "model", "modelname",
    "created_by_ai", "llm", "watermark", "provenance",
)


def _strip_frontmatter_ai_keys(text: str) -> dict[str, Any]:
    """Drop AI-provenance keys from a leading YAML frontmatter block.

    The opening ``---`` delimiter may be preceded by comment lines (some tools
    emit a comment header before frontmatter), so we scan for the first
    ``---`` that is followed by a closing ``---`` before any non-comment,
    non-frontmatter body.
    """
    actions: list[dict[str, Any]] = []
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            start = i
            break
        # only comments and blanks may precede the opening delimiter
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#"):
            return {"text": text, "actions": actions}
    if start is None:
        return {"text": text, "actions": actions}
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {"text": text, "actions": actions}
    kept: list[str] = []
    removed_keys: list[str] = []
    for line in lines[start + 1:end]:
        if ":" in line and not line.lstrip().startswith("#"):
            key = line.split(":", 1)[0].strip().strip('"\'')
            if key.lower() in _AI_FRONTMATTER_KEYS:
                removed_keys.append(key)
                continue
        kept.append(line)
    if removed_keys:
        actions.append(
            {
                "op": "strip_frontmatter_ai_keys",
                "removed_keys": removed_keys,
                "verifiable": True,
            }
        )
        return {
            "text": "---\n" + "\n".join(kept) + "\n---\n" + "\n".join(lines[end + 1:]),
            "actions": actions,
        }
    return {"text": text, "actions": actions}


# ── Safe atomic writes ──────────────────────────────────────────

_MAX_WRITE_BYTES = 512 * 1024 * 1024


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, refusing symlink redirects.

    Resolves the target first and refuses pre-placed symlinks so a clean write
    can never be redirected onto an arbitrary file.
    """
    target = path.resolve()
    if target.is_symlink():
        raise OSError(f"refusing to overwrite symlink target {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(data) > _MAX_WRITE_BYTES:
        raise OSError(f"output exceeds {_MAX_WRITE_BYTES} bytes")
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".cleanroom-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    target = path.resolve()
    _write_atomic(target, text.encode(encoding))
    return target


def safe_write_bytes(path: Path, data: bytes) -> Path:
    return _write_atomic(path, data)


def write_backup(path: Path) -> Path | None:
    """Write a .bak copy of an existing file through the safe path."""
    if not path.exists():
        return None
    data = path.read_bytes()
    bak = path.with_name(path.name + ".bak")
    _write_atomic(bak, data)
    return bak