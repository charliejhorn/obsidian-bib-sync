"""Cite key generation and the lock-file registry that keeps keys stable."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .notes import Paper

DEFAULT_REGISTRY_PATH = Path.home() / ".local" / "state" / "obsidian-bib-sync" / "keys.json"

STOPWORDS = frozenset(
    "a an the on of for in and to with is are at by from as via into using towards toward".split()
)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _surname(author: str) -> str:
    if author.startswith("{"):  # corporate author, e.g. {OpenAI}
        return _fold(author)
    return _fold(author.split(",")[0])


def base_key(paper: Paper) -> str | None:
    """authorYearWord, or None while the paper lacks authors/title/year (e.g. awaiting fetch)."""
    title = paper.fields.get("title", "")
    year = paper.fields.get("year", "")
    if not (paper.authors and title and year):
        return None
    words = [_fold(w) for w in re.split(r"\s+", title)]
    words = [w for w in words if w]
    word = next((w for w in words if w not in STOPWORDS), words[0] if words else "")
    return f"{_surname(paper.authors[0]) or 'anon'}{_fold(year)}{word}"


def _suffixes():
    letters = "abcdefghijklmnopqrstuvwxyz"
    yield from letters
    for a in letters:
        for b in letters:
            yield a + b


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Registry:
    """Every key ever issued: key -> {path, doi, arxiv, first_issued, renamed_to}.

    Keys are never reused for a different paper, even after the note is deleted.
    """

    def __init__(self, path: Path | None = None):
        self.path = path
        self.keys: dict[str, dict] = {}

    @classmethod
    def load(cls, path: Path) -> Registry:
        reg = cls(path)
        if path.exists():
            reg.keys = json.loads(path.read_text())["keys"]
        return reg

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "keys": self.keys}, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def register(self, key: str, paper: Paper) -> None:
        self.keys[key] = {
            "path": str(paper.path),
            "doi": paper.doi,
            "arxiv": paper.arxiv,
            "first_issued": self.keys.get(key, {}).get("first_issued", _now()),
            "renamed_to": None,
        }

    def unique_key(self, base: str) -> str:
        if base not in self.keys:
            return base
        for s in _suffixes():
            if base + s not in self.keys:
                return base + s
        raise RuntimeError(f"no free suffix for {base}")

    def find_old_key(self, paper: Paper, exclude: set[str]) -> str | None:
        """Key previously issued to this paper (matched by doi/arxiv, then path), if any."""
        for match in (
            lambda r: paper.doi and r.get("doi") == paper.doi,
            lambda r: paper.arxiv and r.get("arxiv") == paper.arxiv,
            lambda r: r.get("path") == str(paper.path),
        ):
            for key, rec in self.keys.items():
                if key not in exclude and not rec.get("renamed_to") and match(rec):
                    return key
        return None


@dataclass
class Warning_:
    kind: str  # "rename" | "conflict"
    message: str
    paths: list[Path] = field(default_factory=list)


@dataclass
class Resolution:
    keys: dict[Path, str] = field(default_factory=dict)  # note path -> key to emit
    writeback: dict[Path, str] = field(
        default_factory=dict
    )  # notes whose `citekey` must be written
    aliases: dict[str, str] = field(default_factory=dict)  # old key -> new key, still emitted
    warnings: list[Warning_] = field(default_factory=list)
    pending: list[Path] = field(default_factory=list)  # not enough metadata to key yet
    conflicts: list[Path] = field(default_factory=list)  # excluded from output


def resolve(papers: list[Paper], reg: Registry) -> Resolution:
    """Assign each paper a stable key, mutating `reg` (caller saves it)."""
    res = Resolution()
    scanned = {str(p.path) for p in papers}

    claims: dict[str, list[Paper]] = {}
    for p in papers:
        if p.citekey:
            claims.setdefault(p.citekey, []).append(p)

    for key, ps in claims.items():
        if len(ps) > 1:
            res.conflicts += [p.path for p in ps]
            res.warnings.append(
                Warning_(
                    "conflict",
                    f"{len(ps)} notes claim citekey '{key}'; none are written until resolved",
                    [p.path for p in ps],
                )
            )
    conflicted = set(res.conflicts)
    claimed = set(claims)

    for p in sorted(papers, key=lambda p: str(p.path)):
        if p.path in conflicted:
            continue
        if p.citekey:
            key = p.citekey
            rec = reg.keys.get(key)
            if rec is None:
                old = reg.find_old_key(p, exclude=claimed)
                reg.register(key, p)
                if old:
                    reg.keys[old]["renamed_to"] = key
                    res.aliases[old] = key
                    res.warnings.append(
                        Warning_(
                            "rename",
                            f"citekey '{old}' changed to '{key}'; '{old}' is kept as an alias "
                            "so existing \\cite commands keep working",
                            [p.path],
                        )
                    )
            elif same_paper(rec, p) or rec["path"] not in scanned:
                reg.register(key, p)  # unchanged, or the note file was moved
            else:
                res.conflicts.append(p.path)
                res.warnings.append(
                    Warning_(
                        "conflict",
                        f"citekey '{key}' was issued to {rec['path']}, not {p.path}",
                        [p.path, Path(rec["path"])],
                    )
                )
                continue
            res.keys[p.path] = key
        else:
            issued = reg.find_old_key(p, exclude=claimed)
            if issued:  # key was issued but never written back to the note: reuse it
                reg.register(issued, p)
                res.keys[p.path] = issued
                res.writeback[p.path] = issued
                continue
            base = base_key(p)
            if base is None:
                res.pending.append(p.path)
                continue
            key = reg.unique_key(base)
            reg.register(key, p)
            res.keys[p.path] = key
            res.writeback[p.path] = key

    # Pending renames from earlier passes stay visible until accepted.
    for old, rec in reg.keys.items():
        if rec.get("renamed_to") and rec["renamed_to"] in reg.keys:
            res.aliases.setdefault(old, rec["renamed_to"])
    return res


def same_paper(rec: dict, p: Paper) -> bool:
    if rec.get("path") == str(p.path):
        return True
    return bool((p.doi and rec.get("doi") == p.doi) or (p.arxiv and rec.get("arxiv") == p.arxiv))


def accept_rename(reg: Registry, old: str) -> None:
    """Drop the alias for `old`; the key stays reserved so it is never reissued."""
    reg.keys[old]["renamed_to"] = None
    reg.keys[old]["retired"] = True
