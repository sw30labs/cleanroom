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

# Invisible marks are stripped (or mapped to space/newline). Visible Latin
# typography and diacritics are folded to ASCII (em dash → --, ñ → n, …).

_KEEP_CONTROLS = frozenset({"\t", "\n", "\r"})

# Blank-looking codepoints whose Unicode category is letter/mark/symbol,
# so a category check would miss them.
_INVISIBLE_EXTRA = frozenset({
    "\u034f",  # combining grapheme joiner
    "\u115f",  # hangul choseong filler
    "\u1160",  # hangul jungseong filler
    "\u17b4",  # khmer vowel inherent aq
    "\u17b5",  # khmer vowel inherent aa
    "\u2800",  # braille pattern blank
    "\u3164",  # hangul filler
    "\uffa0",  # halfwidth hangul filler
})

# Named set for the families watermarks commonly inject. Detection also uses
# Unicode categories below so siblings of these ranges are not skipped.
INVISIBLE = frozenset({
    # zero-width / joiners
    "\u200b",  # ZWSP
    "\u200c",  # ZWNJ
    "\u200d",  # ZWJ
    "\u2060",  # word joiner
    "\ufeff",  # BOM / ZWNBSP
    "\u00ad",  # soft hyphen
    "\u034f",  # CGJ
    "\u180e",  # mongolian vowel separator
    # bidi / isolates
    "\u200e",  # LRM
    "\u200f",  # RLM
    "\u061c",  # Arabic letter mark
    "\u202a",  # LRE
    "\u202b",  # RLE
    "\u202c",  # PDF
    "\u202d",  # LRO
    "\u202e",  # RLO
    "\u2066",  # LRI
    "\u2067",  # RLI
    "\u2068",  # FSI
    "\u2069",  # PDI
    # deprecated format / invisible math
    "\u2061",  # function application
    "\u2062",  # invisible times
    "\u2063",  # invisible separator
    "\u2064",  # invisible plus
    "\u206a",  # ISS
    "\u206b",  # ASS
    "\u206c",  # IAFS
    "\u206d",  # AAFS
    "\u206e",  # NAFFS
    "\u206f",  # NADS
    # line / paragraph separators
    "\u2028",
    "\u2029",
    # exotic spaces (regular U+0020 is not a mark)
    "\u00a0",  # NBSP
    "\u1680",  # ogham space mark
    "\u2000",  # en quad
    "\u2001",  # em quad
    "\u2002",  # en space
    "\u2003",  # em space
    "\u2004",  # three-per-em space
    "\u2005",  # four-per-em space
    "\u2006",  # six-per-em space
    "\u2007",  # figure space
    "\u2008",  # punctuation space
    "\u2009",  # thin space
    "\u200a",  # hair space
    "\u202f",  # narrow NBSP
    "\u205f",  # MMSP
    "\u3000",  # ideographic space
}) | _INVISIBLE_EXTRA

# Zero-width tag characters (U+E0000+): also used for watermark payloads.
TAG_CHARS_START = 0xE0000
TAG_CHARS_END = 0xE007F
_VS_START, _VS_END = 0xFE00, 0xFE0F
_VS_SUP_START, _VS_SUP_END = 0xE0100, 0xE01EF

# Visible punctuation → ASCII. Letters with diacritics go through NFKD below;
# this dict covers punctuation that does not decompose, plus a few Latin leftovers.
VISIBLE_FOLD: dict[str, str] = {
    # dashes / minus
    "\u2010": "-",    # hyphen
    "\u2011": "-",    # non-breaking hyphen
    "\u2012": "-",    # figure dash
    "\u2013": "-",    # en dash
    "\u2014": "--",   # em dash
    "\u2015": "--",   # horizontal bar
    "\u2212": "-",    # minus sign
    "\u2e3a": "---",  # two-em dash
    "\u2e3b": "----", # three-em dash
    "\uff0d": "-",    # fullwidth hyphen-minus
    # quotes
    "\u2018": "'",    # left single
    "\u2019": "'",    # right single / apostrophe
    "\u201a": "'",    # single low-9
    "\u201b": "'",    # single high-reversed-9
    "\u201c": '"',    # left double
    "\u201d": '"',    # right double
    "\u201e": '"',    # double low-9
    "\u201f": '"',    # double high-reversed-9
    "\u00ab": '"',    # «
    "\u00bb": '"',    # »
    "\u2039": "'",    # ‹
    "\u203a": "'",    # ›
    "\u2032": "'",    # prime
    "\u2033": '"',    # double prime
    "\u00b4": "'",    # acute accent
    "\uff02": '"',    # fullwidth quotation mark
    "\uff07": "'",    # fullwidth apostrophe
    # ellipsis / dots
    "\u2026": "...",
    "\u2024": ".",
    "\u2025": "..",
    "\u22ef": "...",
    # bullets
    "\u2022": "*",
    "\u2023": "*",
    "\u2043": "-",
    "\u00b7": "*",
    "\u2219": "*",
    "\u25e6": "*",
}

# Latin letters that NFKD does not reduce to ASCII on their own.
LATIN_FOLD: dict[str, str] = {
    "\u00df": "ss",  # ß
    "\u00e6": "ae", "\u00c6": "AE",
    "\u0153": "oe", "\u0152": "OE",
    "\u00f8": "o",  "\u00d8": "O",
    "\u00f0": "d",  "\u00d0": "D",
    "\u00fe": "th", "\u00de": "Th",
    "\u0142": "l",  "\u0141": "L",
    "\u0131": "i",  # dotless i
    "\u00f1": "n",  "\u00d1": "N",  # ñ — also handled by NFKD
}


def _is_invisible(ch: str) -> bool:
    """True for format/control marks, exotic spaces, tags, and blank extras."""
    if ch in _KEEP_CONTROLS:
        return False
    if ch in INVISIBLE:
        return True
    cp = ord(ch)
    if TAG_CHARS_START <= cp <= TAG_CHARS_END:
        return True
    if _VS_START <= cp <= _VS_END or _VS_SUP_START <= cp <= _VS_SUP_END:
        return True
    cat = unicodedata.category(ch)
    if cat in {"Cf", "Zl", "Zp"}:
        return True
    if cat == "Zs" and ch != " ":
        return True
    if cat == "Cc":
        return True
    return False


def _mark_replacement(ch: str) -> str:
    """Replace exotic spaces with ASCII space, separators with newline; else drop."""
    cat = unicodedata.category(ch)
    if cat == "Zs":
        return " "
    if cat in {"Zl", "Zp"}:
        return "\n"
    return ""


def _fold_visible(ch: str) -> str:
    """Map visible non-ASCII Latin typography/letters to ASCII; keep other scripts."""
    if ord(ch) < 128:
        return ch
    mapped = VISIBLE_FOLD.get(ch)
    if mapped is not None:
        return mapped
    mapped = LATIN_FOLD.get(ch)
    if mapped is not None:
        return mapped
    name = unicodedata.name(ch, "")
    if name.startswith("LATIN"):
        pieces: list[str] = []
        for d in unicodedata.normalize("NFKD", ch):
            if unicodedata.category(d) == "Mn":
                continue
            if ord(d) < 128:
                pieces.append(d)
            elif d in LATIN_FOLD:
                pieces.append(LATIN_FOLD[d])
            elif d in VISIBLE_FOLD:
                pieces.append(VISIBLE_FOLD[d])
        return "".join(pieces)
    cat = unicodedata.category(ch)
    if cat == "Pd":
        return "--" if ("EM DASH" in name or "HORIZONTAL BAR" in name) else "-"
    if cat in {"Pi", "Pf"}:
        return "'" if ("SINGLE" in name or "APOSTROPHE" in name) else '"'
    if "ELLIPSIS" in name:
        return "..."
    return ch


def _needs_fold(ch: str) -> bool:
    return _fold_visible(ch) != ch


def detect_marks(text: str) -> dict[str, Any]:
    """Return a structured report of editable Unicode marks in ``text``."""
    by_cp: dict[str, int] = {}
    invisible = 0
    visible = 0
    for ch in text:
        if _is_invisible(ch):
            by_cp[ch] = by_cp.get(ch, 0) + 1
            invisible += 1
        elif _needs_fold(ch):
            by_cp[ch] = by_cp.get(ch, 0) + 1
            visible += 1
    total = invisible + visible
    examples = sorted(by_cp.items(), key=lambda kv: -kv[1])[:10]
    return {
        "total_marked_chars": total,
        "invisible_chars": invisible,
        "visible_fold_chars": visible,
        "distinct_codepoints": len(by_cp),
        "codepoints": [
            {"codepoint": f"U+{ord(c):04X}", "char": repr(c), "count": n}
            for c, n in examples
        ],
        "class": "clean" if total == 0 else "marked",
    }


def strip_marks(text: str) -> str:
    """Strip invisible marks and fold visible Latin typography to ASCII.

    Exotic spaces become a normal space; line/paragraph separators become ``\\n``.
    Em dashes, curly quotes, ñ, and other Latin diacritics become ASCII.
    Non-Latin scripts are left alone.
    """
    out: list[str] = []
    for ch in text:
        if _is_invisible(ch):
            repl = _mark_replacement(ch)
            if repl:
                out.append(repl)
            continue
        out.append(_fold_visible(ch))
    return "".join(out)


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

    Returns an action log. For text: strips invisible Unicode and folds
    visible Latin typography to ASCII. For
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
        if before.get("invisible_chars", 0) > 0:
            actions.append(
                {
                    "op": "strip_invisible_unicode",
                    "removed": before["invisible_chars"],
                    "verifiable": True,
                }
            )
        if before.get("visible_fold_chars", 0) > 0:
            actions.append(
                {
                    "op": "fold_visible_unicode",
                    "folded": before["visible_fold_chars"],
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