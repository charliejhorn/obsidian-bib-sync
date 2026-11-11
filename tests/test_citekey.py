from pathlib import Path

from obsidian_bib_sync.citekey import Registry, accept_rename, base_key, resolve
from obsidian_bib_sync.notes import Paper


def paper(
    name="a.md",
    citekey=None,
    authors=("Vaswani, Ashish",),
    title="Attention Is All You Need",
    year="2017",
    doi=None,
):
    fields = {"title": title, "year": year}
    return Paper(Path("/v") / name, "article", citekey, list(authors), fields, doi=doi)


def test_base_key():
    assert base_key(paper()) == "vaswani2017attention"
    assert (
        base_key(paper(authors=("Müller, Jürgen",), title="The Art of Things")) == "muller2017art"
    )
    assert base_key(paper(authors=("{OpenAI}",), title="GPT-4 Report")) == "openai2017gpt4"
    assert base_key(paper(title="")) is None


def test_generate_and_collision():
    reg = Registry()
    r = resolve([paper("a.md"), paper("b.md")], reg)
    assert r.keys[Path("/v/a.md")] == "vaswani2017attention"
    assert r.keys[Path("/v/b.md")] == "vaswani2017attentiona"
    assert set(r.writeback.values()) == set(r.keys.values())


def test_key_stable_when_metadata_changes():
    reg = Registry()
    resolve([paper()], reg)
    p = paper(citekey="vaswani2017attention", title="Totally New Title", year="2018")
    r = resolve([p], reg)
    assert r.keys[p.path] == "vaswani2017attention"
    assert not r.writeback and not r.warnings


def test_deleted_key_never_reused():
    reg = Registry()
    resolve([paper("a.md")], reg)
    r = resolve([paper("b.md")], reg)  # a.md deleted, b.md is a new paper with same base
    assert r.keys[Path("/v/b.md")] == "vaswani2017attentiona"


def test_manual_rename_keeps_alias():
    reg = Registry()
    resolve([paper(doi="10.1/x")], reg)
    r = resolve([paper(citekey="attention", doi="10.1/x")], reg)
    assert r.aliases == {"vaswani2017attention": "attention"}
    assert [w.kind for w in r.warnings] == ["rename"]
    # alias persists on later passes until accepted
    r2 = resolve([paper(citekey="attention", doi="10.1/x")], reg)
    assert r2.aliases == {"vaswani2017attention": "attention"} and not r2.warnings
    accept_rename(reg, "vaswani2017attention")
    assert resolve([paper(citekey="attention", doi="10.1/x")], reg).aliases == {}
    assert "vaswani2017attention" in reg.keys  # still reserved


def test_rename_detected_by_path_without_doi():
    reg = Registry()
    resolve([paper()], reg)
    r = resolve([paper(citekey="attn")], reg)
    assert r.aliases == {"vaswani2017attention": "attn"}


def test_note_move_keeps_key():
    reg = Registry()
    resolve([paper("a.md", doi="10.1/x")], reg)
    r = resolve([paper("sub/a.md", citekey="vaswani2017attention", doi="10.1/x")], reg)
    assert r.keys[Path("/v/sub/a.md")] == "vaswani2017attention" and not r.warnings


def test_duplicate_claims_conflict():
    reg = Registry()
    r = resolve([paper("a.md", citekey="k"), paper("b.md", citekey="k")], reg)
    assert not r.keys and len(r.conflicts) == 2
    assert r.warnings[0].kind == "conflict"


def test_key_issued_to_other_note_conflicts():
    reg = Registry()
    resolve([paper("a.md")], reg)
    r = resolve(
        [
            paper("a.md", citekey="vaswani2017attention"),
            paper("b.md", citekey="vaswani2017attention", title="Other", doi="10.9/z"),
        ],
        reg,
    )
    assert len(r.conflicts) == 2  # both claim it -> conflict


def test_pending_until_metadata_available():
    reg = Registry()
    p = paper(title="", doi="10.1/x")
    r = resolve([p], reg)
    assert r.pending == [p.path] and not r.keys


def test_registry_roundtrip(tmp_path):
    reg = Registry(tmp_path / "state" / "keys.json")
    resolve([paper()], reg)
    reg.save()
    assert Registry.load(reg.path).keys == reg.keys


def test_issued_key_reused_if_writeback_missed():
    reg = Registry()
    resolve([paper()], reg)
    r = resolve([paper()], reg)  # citekey still absent from the note
    assert r.keys[Path("/v/a.md")] == "vaswani2017attention"
    assert r.writeback == {Path("/v/a.md"): "vaswani2017attention"}


def test_claiming_key_issued_to_other_live_note_conflicts():
    reg = Registry()
    resolve([paper("a.md")], reg)
    r = resolve(
        [
            paper("a.md", citekey="vaswani2017attention"),
            paper("b.md", citekey="other", doi="10.9/z"),
        ],
        reg,
    )
    assert r.keys[Path("/v/a.md")] == "vaswani2017attention"
    r = resolve(
        [
            paper("a.md", citekey="vaswani2017attention"),
            paper("c.md", citekey="vaswani2017attentiona", title="x"),
        ],
        reg,
    )
    assert Path("/v/c.md") in r.keys
