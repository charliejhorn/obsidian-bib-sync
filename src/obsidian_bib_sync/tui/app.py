"""Textual TUI: status, papers, targets, and the rename/orphan review screen."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from . import client


class RenameScreen(ModalScreen[str]):
    """Shows the \\cite rewrite preview for one renamed key; result is apply|drop|cancel."""

    BINDINGS = [Binding("escape", "dismiss('cancel')", "Cancel")]

    def __init__(self, old: str, new: str, files: list[dict]):
        super().__init__()
        self.old, self.new, self.files = old, new, files

    def compose(self) -> ComposeResult:
        total = sum(f["count"] for f in self.files)
        with Vertical(id="rename-dialog"):
            yield Label(f"Rename cite key  {self.old}  ->  {self.new}")
            yield Label(f"{total} citation(s) in {len(self.files)} .tex file(s)")
            with VerticalScroll(id="rename-diff"):
                diffs = "\n".join(f["diff"] for f in self.files) or "(no .tex files cite it)"
                yield Static(diffs, markup=False)
            with Horizontal():
                yield Button("Rewrite .tex files", id="apply", variant="success")
                yield Button("Just drop alias", id="drop", variant="warning")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")


class ObibApp(App):
    TITLE = "obib"
    CSS = """
    #rename-dialog {
        width: 90%; height: 85%; padding: 1;
        border: thick $primary; background: $surface;
    }
    RenameScreen { align: center middle; }
    #rename-diff { height: 1fr; border: solid $panel; }
    #status-text { padding: 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "sync", "Sync now"),
        Binding("d", "remove_target", "Remove target"),
        Binding("r", "review", "Review rename"),
        Binding("x", "orphan_remove", "Drop orphan"),
        Binding("k", "orphan_keep", "Keep orphan"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="status"):
            with TabPane("Status", id="status"):
                yield Static("", id="status-text")
            with TabPane("Papers", id="papers"):
                yield DataTable(id="papers-table", cursor_type="row")
            with TabPane("Targets", id="targets"):
                yield DataTable(id="targets-table", cursor_type="row")
                yield Input(placeholder="Path to a .bib file to add, then Enter", id="add-target")
            with TabPane("Renames & orphans", id="reviews"):
                yield DataTable(id="reviews-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#papers-table", DataTable).add_columns("Key", "State", "Note")
        self.query_one("#targets-table", DataTable).add_columns("Target .bib")
        self.query_one("#reviews-table", DataTable).add_columns("Key", "Kind", "Detail")
        self.view = client.load_view()
        self.refresh_view()
        self.set_interval(2, self.refresh_view)

    # -- rendering -------------------------------------------------------------
    def refresh_view(self) -> None:
        self.view = v = client.load_view()
        st = v.status
        if v.running:
            lines = [
                "Daemon: running",
                f"Papers: {v.papers_dir}",
                f"Last sync: {st.get('last_sync')} ({'ok' if st.get('ok') else 'errors'})",
                f"Entries: {st.get('entries')}",
            ]
        else:
            lines = [
                "Daemon: NOT running  (brew services start obsidian-bib-sync, or `obib run`)",
                f"Papers: {v.papers_dir}",
                "Showing saved state; actions that need the daemon are disabled.",
            ]
        for e in st.get("errors", []):
            lines.append(f"ERROR: {e}")
        for w in st.get("warnings", []):
            lines.append(f"warning [{w['kind']}]: {w['message']}")
        self.query_one("#status-text", Static).update("\n".join(lines))

        self._fill(
            "#papers-table",
            [(k, client.key_state(k, r), r.get("path", "")) for k, r in sorted(v.keys.items())],
        )
        self._fill("#targets-table", [(t,) for t in v.targets])
        reviews = []
        for k, r in sorted(v.keys.items()):
            if r.get("renamed_to"):
                reviews.append((k, "rename", f"now '{r['renamed_to']}'; old key kept as alias"))
            elif r.get("orphaned_since") and not r.get("retired"):
                kind = "orphan (kept)" if r.get("pinned") else "orphan"
                reviews.append((k, kind, f"note gone since {r['orphaned_since'][:10]}"))
        self._fill("#reviews-table", reviews)

    def _fill(self, selector: str, rows: list[tuple]) -> None:
        table = self.query_one(selector, DataTable)
        if [tuple(table.get_row_at(i)) for i in range(table.row_count)] == rows:
            return
        cursor = table.cursor_row
        table.clear()
        for row in rows:
            table.add_row(*row)
        if rows:
            table.move_cursor(row=min(cursor, len(rows) - 1))

    def _selected(self, selector: str) -> tuple | None:
        table = self.query_one(selector, DataTable)
        if table.row_count == 0:
            return None
        return tuple(table.get_row_at(table.cursor_row))

    # -- actions ---------------------------------------------------------------
    def action_sync(self) -> None:
        reply = client.call({"cmd": "sync"})
        self.notify("Synced" if reply.get("ok") else reply.get("error", "failed"))
        self.refresh_view()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "add-target":
            return
        err = client.add_target(event.value.strip())
        self.notify(err or "Target added", severity="error" if err else "information")
        if not err:
            event.input.value = ""
        self.refresh_view()

    def action_remove_target(self) -> None:
        row = self._selected("#targets-table")
        if (
            row
            and self.query_one(TabbedContent).active == "targets"
            and client.remove_target(row[0])
        ):
            self.notify(f"Removed {row[0]} (the file is left untouched)")
            self.refresh_view()

    def action_review(self) -> None:
        row = self._selected("#reviews-table")
        if not row or self.query_one(TabbedContent).active != "reviews" or row[1] != "rename":
            return
        pre = client.call({"cmd": "rename_preview", "old": row[0]})
        if not pre.get("ok"):
            self.notify(pre.get("error", "failed"), severity="error")
            return

        def done(choice: str | None) -> None:
            if choice not in ("apply", "drop"):
                return
            cmd = "rename_apply" if choice == "apply" else "rename_drop"
            reply = client.call({"cmd": cmd, "old": row[0]})
            self.notify("Done" if reply.get("ok") else reply.get("error", "failed"))
            self.refresh_view()

        self.push_screen(RenameScreen(pre["old"], pre["new"], pre["files"]), done)

    def _orphan(self, cmd: str) -> None:
        row = self._selected("#reviews-table")
        if (
            row
            and self.query_one(TabbedContent).active == "reviews"
            and row[1].startswith("orphan")
        ):
            reply = client.call({"cmd": cmd, "key": row[0]})
            if not reply.get("ok"):
                self.notify(reply.get("error", "failed"), severity="error")
            self.refresh_view()

    def action_orphan_remove(self) -> None:
        self._orphan("orphan_remove")

    def action_orphan_keep(self) -> None:
        self._orphan("orphan_keep")


def main() -> None:
    ObibApp().run()
