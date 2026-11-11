import pytest

from obsidian_bib_sync import ipc, rewrite
from obsidian_bib_sync.rewrite import rewrite_text

from .test_daemon import NOTE, running, wait_for  # noqa: F401


def test_rewrite_variants():
    src = (
        r"See \cite{old} and \citep[p.~3]{x, old ,y} and \textcite[a][b]{old}." + "\n"
        r"\nocite{old,older} \autocite*{old}" + "\n"
        r"% \cite{old} commented" + "\n"
        r"Text \cite{older} \cite{old} % \cite{old}" + "\n"
        r"\ref{old} \label{old}" + "\n"
    )
    out, n = rewrite_text(src, "old", "new")
    assert n == 6
    assert r"\cite{new}" in out and r"\citep[p.~3]{x, new ,y}" in out
    assert r"\textcite[a][b]{new}" in out and r"\nocite{new,older}" in out
    assert r"\autocite*{new}" in out
    assert "% \\cite{old} commented" in out and r"\cite{older}" in out
    assert r"\ref{old} \label{old}" in out
    assert out.splitlines()[3] == r"Text \cite{older} \cite{new} % \cite{old}"


def test_apply_with_backup_and_staleness(tmp_path):
    f = tmp_path / "paper.tex"
    f.write_text(r"\cite{old}")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.tex").write_text(r"\cite{old}")
    changes = rewrite.preview([tmp_path], "old", "new")
    assert [c.path for c in changes] == [f] and "-\\cite{old}" in changes[0].diff()
    f.write_text(r"\cite{old} edited meanwhile")
    with pytest.raises(RuntimeError):
        rewrite.apply(changes)
    changes = rewrite.preview([tmp_path], "old", "new")
    rewrite.apply(changes)
    assert f.read_text() == r"\cite{new} edited meanwhile"
    assert (tmp_path / "paper.tex.obib.bak").read_text() == r"\cite{old} edited meanwhile"


def test_rename_flow_through_daemon(running, tmp_path):  # noqa: F811
    d, vault, bib, sock = running
    note = vault / "a.md"
    note.write_text(NOTE.format(t="Hello World") + "")
    assert wait_for(lambda: bib.exists() and "smith2020hello" in bib.read_text())
    tex = bib.parent / "main.tex"
    tex.write_text(r"\cite{smith2020hello}")
    note.write_text(note.read_text().replace("citekey: smith2020hello", "citekey: hello"))
    st = ipc.request({"cmd": "sync"}, sock)["status"]
    assert st["aliases"] == {"smith2020hello": "hello"}
    assert "@article{smith2020hello," in bib.read_text()  # old key still resolves
    pre = ipc.request({"cmd": "rename_preview", "old": "smith2020hello"}, sock)
    assert pre["new"] == "hello" and pre["files"][0]["count"] == 1
    assert tex.read_text() == r"\cite{smith2020hello}"  # preview changes nothing
    done = ipc.request({"cmd": "rename_apply", "old": "smith2020hello"}, sock)
    assert done["ok"] and tex.read_text() == r"\cite{hello}"
    assert "@article{smith2020hello," not in bib.read_text()
    assert done["status"]["aliases"] == {}


def test_orphan_remove(running):  # noqa: F811
    d, vault, bib, sock = running
    (vault / "a.md").write_text(NOTE.format(t="Hello World"))
    assert wait_for(lambda: bib.exists() and "smith2020hello" in bib.read_text())
    (vault / "a.md").unlink()
    st = ipc.request({"cmd": "sync"}, sock)["status"]
    assert st["orphans"] == ["smith2020hello"]
    ipc.request({"cmd": "orphan_remove", "key": "smith2020hello"}, sock)
    assert "smith2020hello" not in bib.read_text()
