import shutil
from pathlib import Path

from obsidian_bib_sync.notes import (
    Invalid,
    Paper,
    Skipped,
    normalize_author,
    parse_note,
    scan,
    update_frontmatter,
)

VAULT = Path(__file__).parent / "fixtures" / "vault"


def test_parse_full_note():
    p = parse_note(VAULT / "vaswani.md")
    assert isinstance(p, Paper)
    assert p.entry_type == "article"
    assert p.authors == ["Vaswani, Ashish", "Shazeer, Noam"]
    assert p.fields["year"] == "2017"
    assert p.doi == "10.48550/arXiv.1706.03762"
    assert p.arxiv == "1706.03762"
    assert p.fields["eprint"] == "1706.03762"
    assert p.citekey is None
    assert not p.needs_fetch
    assert "tags" not in p.fields


def test_id_only_needs_fetch():
    p = parse_note(VAULT / "idonly.md")
    assert isinstance(p, Paper)
    assert p.needs_fetch


def test_scan_classifies_notes():
    r = scan(VAULT)
    assert sorted(p.path.name for p in r.papers) == ["idonly.md", "vaswani.md"]
    assert {s.path.name for s in r.skipped} == {"daily.md", "plain.md"}
    assert {i.path.name for i in r.invalid} == {"incomplete.md", "broken.md"}
    assert not any(".obsidian" in str(p.path) for p in r.papers + r.skipped + r.invalid)


def test_invalid_reasons():
    inc = parse_note(VAULT / "sub" / "incomplete.md")
    assert isinstance(inc, Invalid) and "authors" in inc.reason and "year" in inc.reason
    assert isinstance(parse_note(VAULT / "plain.md"), Skipped)


def test_normalize_author():
    assert normalize_author("Ada Lovelace") == "Lovelace, Ada"
    assert (
        normalize_author("Jan van der Berg") == "Berg, Jan van der"
    )  # use "Last, First" for particles
    assert normalize_author("Lovelace, Ada") == "Lovelace, Ada"
    assert normalize_author("{OpenAI}") == "{OpenAI}"


def test_content_hash_changes_with_frontmatter_only(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("---\ntype: misc\ntitle: T\nauthors: A B\nyear: 2000\n---\nbody1\n")
    h1 = parse_note(f).content_hash
    f.write_text("---\ntype: misc\ntitle: T\nauthors: A B\nyear: 2000\n---\nbody2\n")
    assert parse_note(f).content_hash == h1
    f.write_text("---\ntype: misc\ntitle: T2\nauthors: A B\nyear: 2000\n---\nbody2\n")
    assert parse_note(f).content_hash != h1


def test_update_frontmatter_preserves_everything(tmp_path):
    f = tmp_path / "v.md"
    shutil.copy(VAULT / "vaswani.md", f)
    before = f.read_text()
    update_frontmatter(f, {"citekey": "vaswani2017attention"})
    after = f.read_text()
    assert "citekey: vaswani2017attention" in after
    assert after.endswith("# Notes\nBody text is ignored.\n")
    assert "tags: [paper, transformers]" in after
    assert after.index("type: article") < after.index("title:")
    assert parse_note(f).citekey == "vaswani2017attention"
    assert before.splitlines()[:3] == after.splitlines()[:3]
