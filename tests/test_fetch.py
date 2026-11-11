import json
from pathlib import Path

import httpx

from obsidian_bib_sync.citekey import Registry
from obsidian_bib_sync.config import Config
from obsidian_bib_sync.fetch import Fetcher, missing_updates, parse_arxiv, parse_crossref
from obsidian_bib_sync.notes import parse_note
from obsidian_bib_sync.sync import run_sync

CROSSREF = {
    "message": {
        "title": ["Deep <i>Learning</i>"],
        "author": [
            {"family": "LeCun", "given": "Yann"},
            {"name": "Some Consortium"},
        ],
        "published-print": {"date-parts": [[2015, 5, 28]]},
        "container-title": ["Nature"],
        "volume": "521",
        "issue": "7553",
        "page": "436-444",
        "publisher": "Springer",
        "DOI": "10.1038/nature14539",
    }
}
ARXIV = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry>
  <published>2017-06-12T17:57:34Z</published>
  <title>Attention Is
  All You Need</title>
  <author><name>Ashish Vaswani</name></author>
  <author><name>Noam Shazeer</name></author>
 </entry>
</feed>"""


def make_fetcher(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(tmp_path / "cache.json", client, sleep=lambda s: None)


def test_parse_crossref():
    d = parse_crossref(CROSSREF, "article")
    assert d["title"] == "Deep Learning"
    assert d["authors"] == ["LeCun, Yann", "{Some Consortium}"]
    assert (d["year"], d["month"], d["journal"], d["pages"]) == (2015, 5, "Nature", "436--444")
    assert parse_crossref(CROSSREF, "inproceedings")["booktitle"] == "Nature"


def test_parse_arxiv():
    d = parse_arxiv(ARXIV)
    assert d == {
        "title": "Attention Is All You Need",
        "authors": ["Vaswani, Ashish", "Shazeer, Noam"],
        "year": 2017,
        "month": 6,
    }


def test_fetch_caches_and_negative_caches(tmp_path):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json=CROSSREF)

    note = tmp_path / "n.md"
    note.write_text("---\ntype: article\ndoi: 10.1038/nature14539\n---\n")
    paper = parse_note(note)
    f = make_fetcher(tmp_path, handler)
    assert f.fetch(paper)["year"] == 2015
    assert make_fetcher(tmp_path, handler).fetch(paper)["year"] == 2015  # from disk cache
    assert len(calls) == 1

    bad = tmp_path / "bad.md"
    bad.write_text("---\ntype: article\ndoi: 10.9/none\n---\n")
    p2 = parse_note(bad)
    f2 = make_fetcher(tmp_path, lambda r: httpx.Response(404))
    assert f2.fetch(p2) is None
    n = []
    f3 = make_fetcher(tmp_path, lambda r: n.append(1) or httpx.Response(404))
    assert f3.fetch(p2) is None and n == []  # inside retry window: no request


def test_missing_updates_never_overwrites(tmp_path):
    note = tmp_path / "n.md"
    note.write_text("---\ntype: article\ntitle: Mine\ndoi: 10.1/x\n---\n")
    updates = missing_updates(parse_note(note), parse_crossref(CROSSREF, "article"))
    assert "title" not in updates and updates["year"] == 2015 and "authors" in updates


def test_sync_autofills_note_and_bib(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "dl.md").write_text(
        "---\ntype: article\ntags: [x]\ndoi: 10.1038/nature14539\n---\nmy notes\n"
    )
    cfg = Config(
        papers_dir=vault,
        targets=[tmp_path / "refs.bib"],
        registry_path=tmp_path / "keys.json",
    )
    f = make_fetcher(tmp_path, lambda r: httpx.Response(200, json=CROSSREF))
    r = run_sync(cfg, fetcher=f)
    assert r.ok and r.entries == 1
    note = (vault / "dl.md").read_text()
    assert "citekey: lecun2015deep" in note and "tags: [x]" in note and note.endswith("my notes\n")
    bib = (tmp_path / "refs.bib").read_text()
    assert "@article{lecun2015deep," in bib and "volume = {521}" in bib


def test_sync_survives_fetch_failure(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "dl.md").write_text("---\ntype: article\ndoi: 10.1/x\n---\n")
    cfg = Config(
        papers_dir=vault, targets=[tmp_path / "refs.bib"], registry_path=tmp_path / "k.json"
    )
    f = make_fetcher(tmp_path, lambda r: httpx.Response(500))
    r = run_sync(cfg, fetcher=f)
    assert r.ok and r.entries == 0 and any(w.kind == "fetch" for w in r.warnings)
    assert json.loads(Path(tmp_path / "cache.json").read_text())
    assert not Registry.load(tmp_path / "k.json").keys
