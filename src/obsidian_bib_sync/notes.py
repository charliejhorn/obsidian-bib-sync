"""Scan a papers directory and parse note frontmatter into Paper models."""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

BIB_FIELDS = (
    "title",
    "year",
    "month",
    "journal",
    "booktitle",
    "volume",
    "number",
    "pages",
    "publisher",
    "doi",
    "url",
    "eprint",
)
ENTRY_TYPES = {
    "article",
    "inproceedings",
    "book",
    "inbook",
    "incollection",
    "misc",
    "techreport",
    "phdthesis",
    "mastersthesis",
    "unpublished",
    "proceedings",
}
_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass
class Paper:
    path: Path
    entry_type: str
    citekey: str | None
    authors: list[str]
    fields: dict[str, str]
    doi: str | None = None
    arxiv: str | None = None
    content_hash: str = ""

    @property
    def needs_fetch(self) -> bool:
        """True when an identifier is present but core fields (or the type) are missing."""
        core_missing = not (self.fields.get("title") and self.authors and self.fields.get("year"))
        return bool(self.doi or self.arxiv) and (core_missing or not self.entry_type)


@dataclass
class Skipped:
    """A markdown file that is not a paper note (no frontmatter / no `type`)."""

    path: Path
    reason: str


@dataclass
class Invalid:
    """A paper note that cannot be used; the previous entry should be retained."""

    path: Path
    reason: str


@dataclass
class ScanResult:
    papers: list[Paper] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    invalid: list[Invalid] = field(default_factory=list)


def _yaml() -> YAML:
    y = YAML()  # round-trip: preserves order and comments
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)  # Obsidian style: `tags:\n  - item`
    return y


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter yaml, body); frontmatter is None if absent."""
    m = _FM_RE.match(text.lstrip("﻿"))
    if not m:
        return None, text
    return m.group(1), text.lstrip("﻿")[m.end() :]


def normalize_author(name: str) -> str:
    """Normalise to BibTeX "Last, First"; leave "Last, First" and corporate {names} alone."""
    name = " ".join(name.split())
    if not name or "," in name or name.startswith("{"):
        return name
    parts = name.split(" ")
    if len(parts) == 1:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def _authors(raw: Any) -> list[str]:
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        for part in re.split(r"\s+and\s+", str(item)):
            part = part.strip()
            if part:
                out.append(normalize_author(part))
    return out


def _clean_doi(doi: str) -> str:
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)


def _clean_arxiv(arxiv: str) -> str:
    arxiv = re.sub(r"^(https?://arxiv\.org/(abs|pdf)/|arxiv:)", "", arxiv.strip(), flags=re.I)
    return re.sub(r"\.pdf$", "", arxiv)


def _scalar(v: Any) -> str:
    return "" if v is None else str(v).strip()


def parse_note(path: Path, text: str | None = None) -> Paper | Skipped | Invalid:
    if text is None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return Invalid(path, f"cannot read: {e}")
    fm_text, _ = split_frontmatter(text)
    if fm_text is None:
        return Skipped(path, "no frontmatter")
    try:
        data = _yaml().load(fm_text)
    except YAMLError as e:
        return Invalid(path, f"invalid YAML frontmatter: {str(e).splitlines()[0]}")
    if not isinstance(data, dict):
        return Skipped(path, "frontmatter is not a mapping")

    entry_type = _scalar(data.get("type")).lower()
    if entry_type and entry_type not in ENTRY_TYPES:
        return Skipped(path, f"`type: {entry_type}` is not a BibTeX entry type")

    fields = {k: _scalar(data.get(k)) for k in BIB_FIELDS if _scalar(data.get(k))}
    authors = _authors(data.get("authors"))
    doi = _clean_doi(_scalar(data.get("doi"))) or None
    arxiv = _clean_arxiv(_scalar(data.get("arxiv"))) or None
    if doi:
        fields["doi"] = doi
    if arxiv:
        fields["eprint"] = arxiv
        fields.setdefault("archiveprefix", "arXiv")

    if not entry_type and not (doi or arxiv):
        if not any((fields.get("title"), authors, fields.get("year"))):
            return Skipped(path, "no `type` field")
        return Invalid(path, "missing `type` (e.g. article, inproceedings, book, misc)")

    has_core = fields.get("title") and authors and fields.get("year")
    if not (has_core or doi or arxiv):
        if not any((fields.get("title"), authors, fields.get("year"))):
            return Skipped(path, "no title/authors/year and no doi/arxiv")
        missing = [
            n
            for n, ok in (
                ("title", fields.get("title")),
                ("authors", authors),
                ("year", fields.get("year")),
            )
            if not ok
        ]
        return Invalid(path, f"missing required field(s): {', '.join(missing)}")

    return Paper(
        path=path,
        entry_type=entry_type,
        citekey=_scalar(data.get("citekey")) or None,
        authors=authors,
        fields=fields,
        doi=doi,
        arxiv=arxiv,
        content_hash=hashlib.sha256(fm_text.encode()).hexdigest(),
    )


def iter_notes(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]  # .obsidian, .trash, ...
        for fn in sorted(filenames):
            if fn.endswith(".md") and not fn.startswith("."):
                yield Path(dirpath) / fn


def scan(root: Path) -> ScanResult:
    result = ScanResult()
    for path in iter_notes(root):
        parsed = parse_note(path)
        if isinstance(parsed, Paper):
            result.papers.append(parsed)
        elif isinstance(parsed, Skipped):
            result.skipped.append(parsed)
        else:
            result.invalid.append(parsed)
    return result


def update_frontmatter(path: Path, updates: dict[str, Any]) -> None:
    """Set frontmatter keys (existing key order, comments and the body are preserved)."""
    text = path.read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        raise ValueError(f"{path} has no frontmatter")
    y = _yaml()
    data = y.load(fm_text)
    for k, v in updates.items():
        data[k] = v
    buf = io.StringIO()
    y.dump(data, buf)
    new = f"---\n{buf.getvalue()}---\n{body}"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".obib-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
