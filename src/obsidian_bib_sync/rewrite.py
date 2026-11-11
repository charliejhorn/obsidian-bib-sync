"""Rewrite \\cite-family commands in .tex files when a cite key is renamed."""

from __future__ import annotations

import difflib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

# \cite, \citep*, \textcite[see][p. 3]{k}, \nocite{a,b}, \Autocite{k}, \parencite, ...
_CITE_RE = re.compile(
    r"(\\[A-Za-z]*(?:cite|Cite)[A-Za-z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{)([^}]*)(\})"
)
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", "build", "_build"}


@dataclass
class FileChange:
    path: Path
    old_text: str
    new_text: str
    count: int

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.old_text.splitlines(keepends=True),
                self.new_text.splitlines(keepends=True),
                f"a/{self.path.name}",
                f"b/{self.path.name}",
            )
        )


def _rewrite_line(line: str, old: str, new: str) -> tuple[str, int]:
    m = re.search(r"(?<!\\)%", line)
    code, comment = (line[: m.start()], line[m.start() :]) if m else (line, "")
    count = 0

    def sub(match: re.Match) -> str:
        nonlocal count
        keys = match.group(2).split(",")
        out = []
        for k in keys:
            if k.strip() == old:
                count += 1
                out.append(k.replace(old, new))
            else:
                out.append(k)
        return match.group(1) + ",".join(out) + match.group(3)

    return _CITE_RE.sub(sub, code) + comment, count


def rewrite_text(text: str, old: str, new: str) -> tuple[str, int]:
    total = 0
    out = []
    for line in text.splitlines(keepends=True):
        new_line, n = _rewrite_line(line, old, new)
        out.append(new_line)
        total += n
    return "".join(out), total


def iter_tex(roots: list[Path]):
    seen: set[Path] = set()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if fn.endswith(".tex") and p not in seen:
                    seen.add(p)
                    yield p


def preview(roots: list[Path], old: str, new: str) -> list[FileChange]:
    changes = []
    for p in iter_tex(roots):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text, n = rewrite_text(text, old, new)
        if n:
            changes.append(FileChange(p, text, new_text, n))
    return changes


def apply(changes: list[FileChange]) -> None:
    """Write each change atomically, keeping the original as `<file>.obib.bak`."""
    for c in changes:
        current = c.path.read_text(encoding="utf-8")
        if current != c.old_text:
            raise RuntimeError(f"{c.path} changed since the preview; run the preview again")
        c.path.with_name(c.path.name + ".obib.bak").write_text(c.old_text, encoding="utf-8")
        fd, tmp = tempfile.mkstemp(dir=c.path.parent, prefix=".obib-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(c.new_text)
            os.replace(tmp, c.path)
        except BaseException:
            os.unlink(tmp)
            raise
