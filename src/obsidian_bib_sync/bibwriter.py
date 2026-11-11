"""Render BibTeX entries and splice them into the managed block of a .bib file."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from .notes import Paper

BEGIN = "% >>> obsidian-bib-sync (do not edit between markers) >>>"
END = "% <<< obsidian-bib-sync <<<"
_BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
_KEY_RE = re.compile(r"^[ \t]*@(?!comment|string|preamble)\w+\s*[{(]\s*([^,\s]+)\s*,", re.I | re.M)

_ORDER = (
    "title",
    "journal",
    "booktitle",
    "year",
    "month",
    "volume",
    "number",
    "pages",
    "publisher",
    "doi",
    "eprint",
    "archiveprefix",
    "url",
)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_TEXT_FIELDS = {"title", "journal", "booktitle", "publisher"}


def _escape(s: str) -> str:
    return re.sub(r"(?<!\\)([&%#])", r"\\\1", s)


def render_entry(paper: Paper, key: str) -> str:
    lines = [f"@{paper.entry_type}{{{key},"]
    if paper.authors:
        lines.append(f"  author = {{{_escape(' and '.join(paper.authors))}}},")
    for name in _ORDER:
        value = paper.fields.get(name)
        if not value:
            continue
        if name == "month" and value.isdigit() and 1 <= int(value) <= 12:
            lines.append(f"  month = {_MONTHS[int(value) - 1]},")
        elif name == "title":
            lines.append(
                f"  title = {{{{{_escape(value)}}}}},"
            )  # double braces keep capitalisation
        else:
            lines.append(f"  {name} = {{{_escape(value) if name in _TEXT_FIELDS else value}}},")
    lines.append("}")
    return "\n".join(lines)


def rekey(entry: str, new_key: str) -> str:
    return re.sub(r"^(@\w+\{)[^,]+,", lambda m: f"{m.group(1)}{new_key},", entry, count=1)


def render_block(entries: dict[str, str]) -> str:
    """Deterministic managed block: entries sorted by key."""
    body = "\n\n".join(entries[k] for k in sorted(entries))
    return f"{BEGIN}\n\n{body}\n\n{END}\n" if entries else f"{BEGIN}\n{END}\n"


def splice(existing: str, block: str) -> str:
    if _BLOCK_RE.search(existing):
        return _BLOCK_RE.sub(lambda _: block, existing, count=1)
    if not existing.strip():
        return block
    return existing.rstrip("\n") + "\n\n" + block


def keys_outside_block(text: str) -> set[str]:
    return set(_KEY_RE.findall(_BLOCK_RE.sub("", text)))


def write_target(path: Path, block: str) -> tuple[bool, set[str]]:
    """Write `block` into `path`. Returns (changed, hand-written keys outside the block)."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    new = splice(existing, block)
    outside = keys_outside_block(new)
    if new == existing:
        return False, outside
    if existing:
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".obib-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return True, outside
