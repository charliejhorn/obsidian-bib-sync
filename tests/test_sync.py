import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obsidian_bib_sync import bibwriter
from obsidian_bib_sync.citekey import Registry
from obsidian_bib_sync.cli import main
from obsidian_bib_sync.config import Config
from obsidian_bib_sync.notes import parse_note
from obsidian_bib_sync.sync import run_sync

NOTE = (
    '---\ntype: article\ntitle: "{title}"\nauthors:\n  - {author}\nyear: {year}\n{extra}---\nbody\n'
)


def write_note(
    vault: Path,
    name: str,
    title="Attention Is All You Need",
    author="Vaswani, Ashish",
    year=2017,
    extra="",
):
    (vault / name).write_text(NOTE.format(title=title, author=author, year=year, extra=extra))


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    proj_a, proj_b = tmp_path / "a", tmp_path / "b"
    proj_a.mkdir(), proj_b.mkdir()
    cfg = Config(
        papers_dir=vault,
        targets=[proj_a / "refs.bib", proj_b / "refs.bib"],
        registry_path=tmp_path / "state" / "keys.json",
    )
    return vault, cfg, tmp_path


def test_end_to_end_identical_targets(env):
    vault, cfg, _ = env
    write_note(vault, "a.md")
    write_note(vault, "b.md", title="BERT: Pre-training & Stuff", author="Devlin, Jacob", year=2019)
    r = run_sync(cfg)
    assert r.ok and r.entries == 2 and len(r.written) == 2
    a, b = (t.read_bytes() for t in cfg.targets)
    assert a == b
    text = a.decode()
    assert "@article{vaswani2017attention," in text
    assert "title = {{BERT: Pre-training \\& Stuff}}," in text
    assert parse_note(vault / "a.md").citekey == "vaswani2017attention"  # written back
    # idempotent: nothing written second time
    r2 = run_sync(cfg)
    assert not r2.written and len(r2.unchanged) == 2


def test_preserves_hand_written_entries_and_warns_on_duplicates(env):
    vault, cfg, _ = env
    write_note(vault, "a.md")
    cfg.targets[0].write_text(
        "@book{mine,\n  title={Mine},\n}\n\n@misc{vaswani2017attention, title={dup}}\n"
    )
    r = run_sync(cfg)
    text = cfg.targets[0].read_text()
    assert text.startswith("@book{mine,")
    assert [w.kind for w in r.warnings] == ["duplicate"]
    assert cfg.targets[0].with_name("refs.bib.bak").exists()


def test_key_survives_title_change(env):
    vault, cfg, _ = env
    write_note(vault, "a.md")
    run_sync(cfg)
    text = (vault / "a.md").read_text().replace("Attention Is All You Need", "Brand New Title")
    (vault / "a.md").write_text(text)
    run_sync(cfg)
    bib = cfg.targets[0].read_text()
    assert "@article{vaswani2017attention," in bib and "Brand New Title" in bib


def test_rename_keeps_old_key_as_alias(env):
    vault, cfg, _ = env
    write_note(vault, "a.md", extra="doi: 10.1/x\n")
    run_sync(cfg)
    p = vault / "a.md"
    p.write_text(p.read_text().replace("citekey: vaswani2017attention", "citekey: attn"))
    r = run_sync(cfg)
    bib = cfg.targets[0].read_text()
    assert "@article{attn," in bib and "@article{vaswani2017attention," in bib
    assert any(w.kind == "rename" for w in r.warnings)


def test_deleted_note_kept_through_grace_then_removed(env):
    vault, cfg, _ = env
    write_note(vault, "a.md")
    write_note(vault, "b.md", title="Other Paper", author="Smith, Jo", year=2020)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    run_sync(cfg, now=t0)
    (vault / "b.md").unlink()
    r = run_sync(cfg, now=t0 + timedelta(days=1))
    assert r.orphans == ["smith2020other"] and "smith2020other" in cfg.targets[0].read_text()
    r = run_sync(cfg, now=t0 + timedelta(days=30))
    assert "smith2020other" not in cfg.targets[0].read_text() and r.entries == 1
    # key stays reserved
    write_note(vault, "c.md", title="Other Paper", author="Smith, Jo", year=2020)
    run_sync(cfg, now=t0 + timedelta(days=31))
    assert "smith2020othera" in cfg.targets[0].read_text()


def test_broken_note_keeps_previous_entry(env):
    vault, cfg, _ = env
    write_note(vault, "a.md")
    run_sync(cfg)
    (vault / "a.md").write_text("---\ntype: [oops\n---\n")
    r = run_sync(cfg)
    assert "@article{vaswani2017attention," in cfg.targets[0].read_text()
    assert any(w.kind == "invalid" for w in r.warnings)
    assert r.orphans == []


def test_missing_papers_dir_never_wipes_bibs(env):
    vault, cfg, _ = env
    write_note(vault, "a.md")
    run_sync(cfg)
    before = cfg.targets[0].read_text()
    shutil.rmtree(vault)
    r = run_sync(cfg)
    assert not r.ok and cfg.targets[0].read_text() == before


def test_missing_target_dir_reported_not_fatal(env):
    vault, cfg, tmp = env
    write_note(vault, "a.md")
    cfg.targets.append(tmp / "nope" / "refs.bib")
    r = run_sync(cfg)
    assert len(r.written) == 2 and len(r.errors) == 1


def test_block_helpers():
    block = bibwriter.render_block({"b": "@misc{b,\n}", "a": "@misc{a,\n}"})
    assert block.index("@misc{a") < block.index("@misc{b")
    s = bibwriter.splice("before\n", block)
    assert bibwriter.splice(s, block) == s
    assert bibwriter.splice(s, bibwriter.render_block({})).startswith("before")


def test_cli_init_add_sync(env, monkeypatch, capsys):
    vault, cfg, tmp = env
    monkeypatch.setenv("OBIB_CONFIG", str(tmp / "cfg.toml"))
    write_note(vault, "a.md")
    bib = tmp / "a" / "refs.bib"
    assert main(["init", str(vault)]) == 0
    assert main(["add", str(bib)]) == 0
    assert main(["add", str(bib)]) == 0  # idempotent
    assert main(["add", str(tmp / "x.txt")]) == 2
    c = Config.load()
    c.registry_path = tmp / "state" / "keys.json"
    c.save()
    assert main(["sync"]) == 0
    assert "vaswani2017attention" in bib.read_text()
    assert main(["remove", str(bib)]) == 0
    assert main(["list"]) == 0
    assert Registry.load(c.registry_path).keys
