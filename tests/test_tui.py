import tempfile
from pathlib import Path

from textual.widgets import DataTable

from obsidian_bib_sync import ipc
from obsidian_bib_sync.config import Config
from obsidian_bib_sync.tui.app import ObibApp

from .test_daemon import NOTE, running, wait_for  # noqa: F401


async def test_tui_without_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("OBIB_CONFIG", str(tmp_path / "cfg.toml"))
    monkeypatch.setenv("OBIB_SOCKET", str(Path(tempfile.mkdtemp(prefix="obib")) / "none.sock"))
    Config(papers_dir=tmp_path, registry_path=tmp_path / "k.json").save()
    app = ObibApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "NOT running" in str(app.query_one("#status-text").render())


async def test_tui_with_daemon_lists_papers_and_reviews_rename(running, tmp_path, monkeypatch):  # noqa: F811
    d, vault, bib, sock = running
    monkeypatch.setenv("OBIB_SOCKET", str(sock))
    monkeypatch.setenv("OBIB_CONFIG", str(tmp_path / "cfg.toml"))
    note = vault / "a.md"
    note.write_text(NOTE.format(t="Hello World"))
    assert wait_for(lambda: bib.exists() and "smith2020hello" in bib.read_text())
    (bib.parent / "main.tex").write_text(r"\cite{smith2020hello}")
    note.write_text(note.read_text().replace("citekey: smith2020hello", "citekey: hello"))
    ipc.request({"cmd": "sync"}, sock)

    app = ObibApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "running" in str(app.query_one("#status-text").render())
        papers = app.query_one("#papers-table", DataTable)
        assert {papers.get_row_at(i)[0] for i in range(papers.row_count)} == {
            "hello",
            "smith2020hello",
        }
        await pilot.press("tab")  # focus moves; switch to the review tab directly
        app.query_one("TabbedContent").active = "reviews"
        await pilot.pause()
        reviews = app.query_one("#reviews-table", DataTable)
        assert tuple(reviews.get_row_at(0)[:2]) == ("smith2020hello", "rename")
        reviews.focus()
        await pilot.press("r")
        await pilot.pause()
        assert type(app.screen).__name__ == "RenameScreen"
        await pilot.click("#apply")
        await pilot.pause()
        assert (bib.parent / "main.tex").read_text() == r"\cite{hello}"
