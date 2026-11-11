"""Look up metadata for a DOI (Crossref) or arXiv id, with a persistent cache."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from .notes import Paper, normalize_author

DEFAULT_CACHE_PATH = Path.home() / ".cache" / "obsidian-bib-sync" / "fetch.json"
USER_AGENT = "obsidian-bib-sync/0.1 (BibTeX sync for Obsidian notes)"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
FAILURE_RETRY = timedelta(hours=1)
# Seconds to wait between requests to the same host (arXiv asks for ~3s).
MIN_INTERVAL = {"api.crossref.org": 0.0, "export.arxiv.org": 3.0}


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def parse_crossref(data: dict, entry_type: str) -> dict:
    msg = data["message"]
    out: dict = {}
    if msg.get("title"):
        out["title"] = _strip_tags(msg["title"][0])
    authors = []
    for a in msg.get("author", []):
        if a.get("family"):
            authors.append(f"{a['family']}, {a['given']}" if a.get("given") else a["family"])
        elif a.get("name"):
            authors.append("{" + a["name"] + "}")
    if authors:
        out["authors"] = authors
    parts = None
    for k in ("published-print", "published", "issued", "published-online"):
        parts = (msg.get(k) or {}).get("date-parts", [[None]])[0]
        if parts and parts[0]:
            break
    if parts and parts[0]:
        out["year"] = parts[0]
        if len(parts) > 1 and parts[1]:
            out["month"] = parts[1]
    container = (msg.get("container-title") or [""])[0]
    if container:
        out["journal" if entry_type == "article" else "booktitle"] = _strip_tags(container)
    for k in ("volume", "publisher"):
        if msg.get(k):
            out[k] = msg[k]
    if msg.get("issue"):
        out["number"] = msg["issue"]
    if msg.get("page"):
        out["pages"] = msg["page"].replace("-", "--") if "--" not in msg["page"] else msg["page"]
    if msg.get("DOI"):
        out["doi"] = msg["DOI"]
    return out


def parse_arxiv(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    entry = root.find(f"{_ATOM}entry")
    if entry is None or entry.findtext(f"{_ATOM}title", "").strip().lower() == "error":
        raise ValueError("arXiv returned no entry")
    out: dict = {"title": " ".join(entry.findtext(f"{_ATOM}title", "").split())}
    names = [a.findtext(f"{_ATOM}name", "") for a in entry.findall(f"{_ATOM}author")]
    out["authors"] = [normalize_author(n) for n in names if n]
    published = entry.findtext(f"{_ATOM}published", "")
    if published[:4].isdigit():
        out["year"] = int(published[:4])
        out["month"] = int(published[5:7])
    doi = entry.findtext(f"{_ARXIV}doi")
    if doi:
        out["doi"] = doi.strip()
    return out


class Fetcher:
    def __init__(
        self,
        cache_path: Path | None = DEFAULT_CACHE_PATH,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        min_interval: dict[str, float] | None = None,
    ):
        self.cache_path = cache_path
        self.client = client or httpx.Client(
            timeout=10, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._sleep = sleep
        self._min_interval = MIN_INTERVAL if min_interval is None else min_interval
        self._last: dict[str, float] = {}
        self._cache: dict[str, dict] = {}
        if cache_path and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except (OSError, ValueError):
                self._cache = {}

    def _save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.cache_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self._cache, f)
        os.replace(tmp, self.cache_path)

    def _get(self, url: str) -> httpx.Response:
        host = httpx.URL(url).host
        wait = self._min_interval.get(host, 0) - (time.monotonic() - self._last.get(host, -1e9))
        if wait > 0:
            self._sleep(wait)
        self._last[host] = time.monotonic()
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp

    def _lookup(self, kind: str, ident: str, entry_type: str) -> dict:
        if kind == "doi":
            url = f"https://api.crossref.org/works/{quote(ident, safe='/')}"
            return parse_crossref(self._get(url).json(), entry_type)
        url = f"https://export.arxiv.org/api/query?id_list={quote(ident)}"
        return parse_arxiv(self._get(url).text)

    def fetch(self, paper: Paper) -> dict | None:
        """Metadata for the paper's DOI (preferred) or arXiv id; None if unavailable now."""
        candidates = [("doi", paper.doi)] if paper.doi else []
        if paper.arxiv:
            candidates.append(("arxiv", paper.arxiv))
        for kind, ident in candidates:
            ck = f"{kind}:{ident}"
            cached = self._cache.get(ck)
            if cached and "data" in cached:
                return cached["data"]
            if cached and datetime.fromisoformat(cached["retry_after"]) > datetime.now(UTC):
                continue
            try:
                data = self._lookup(kind, ident, paper.entry_type)
            except (httpx.HTTPError, ValueError, KeyError, ET.ParseError):
                retry = datetime.now(UTC) + FAILURE_RETRY
                self._cache[ck] = {"retry_after": retry.isoformat()}
                self._save()
                continue
            self._cache[ck] = {"data": data}
            self._save()
            return data
        return None


def missing_updates(paper: Paper, data: dict) -> dict:
    """Frontmatter keys to write: only fields the note leaves empty (never overwrite the user)."""
    updates: dict = {}
    for k, v in data.items():
        if k == "authors":
            if not paper.authors:
                updates["authors"] = list(v)
        elif not paper.fields.get(k):
            updates[k] = v
    return updates
