"""Background service: periodic + event-driven sync, with a unix-socket control interface."""

from __future__ import annotations

import logging
import os
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path

from . import ipc, rewrite
from .citekey import DEFAULT_REGISTRY_PATH, Registry, accept_rename
from .config import Config, config_path
from .fetch import Fetcher
from .sync import SyncReport, run_sync

log = logging.getLogger("obib")
DEBOUNCE = 1.0  # seconds to let a burst of file changes settle
POLL = 2.0  # seconds between cheap change checks of the papers directory


def signature(root: Path) -> int:
    """Cheap fingerprint of the notes (names, mtimes, sizes); changes when any note changes.

    Deliberately pure Python: native FSEvents bindings hold the GIL while opening the watched
    folder, which freezes the whole daemon when macOS privacy (TCC) blocks the folder.
    """
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".md") and not fn.startswith("."):
                try:
                    st = os.stat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                entries.append((dirpath, fn, st.st_mtime_ns, st.st_size))
    return hash(frozenset(entries))


class Daemon:
    def __init__(
        self,
        config: Config,
        socket: Path | None = None,
        fetcher: Fetcher | None = None,
        cfg_file: Path | None = None,
    ):
        self.config = config
        self.fetcher = fetcher
        self.cfg_file = cfg_file or config_path()
        self._cfg_mtime = self._mtime()
        self._socket = socket or ipc.socket_path()
        self._lock = threading.RLock()  # serialises sync passes and registry access
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._reload = False
        self.registry = self._load_registry()
        self.status: dict = {"last_sync": None, "ok": None, "entries": 0}
        self._syncing_since: str | None = None

    # -- state -------------------------------------------------------------
    def _load_registry(self) -> Registry:
        path = self.config.registry_path or DEFAULT_REGISTRY_PATH
        reg = Registry.load(path)
        reg.path = path
        return reg

    def _mtime(self) -> float:
        try:
            return self.cfg_file.stat().st_mtime
        except OSError:
            return 0.0

    def _record(self, report: SyncReport) -> None:
        self.status = {
            "last_sync": datetime.now(UTC).isoformat(timespec="seconds"),
            "ok": report.ok,
            "entries": report.entries,
            "written": [str(p) for p in report.written],
            "warnings": [
                {"kind": w.kind, "message": w.message, "paths": [str(p) for p in w.paths]}
                for w in report.warnings
            ],
            "errors": report.errors,
            "orphans": report.orphans,
            "aliases": {
                k: r["renamed_to"] for k, r in self.registry.keys.items() if r.get("renamed_to")
            },
        }

    def sync_now(self) -> dict:
        with self._lock:
            self._syncing_since = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                report = run_sync(self.config, self.registry, fetcher=self.fetcher)
            finally:
                self._syncing_since = None
            self._record(report)
        for w in report.warnings:
            log.warning("[%s] %s", w.kind, w.message)
        for e in report.errors:
            log.error("%s", e)
        if report.written:
            log.info("updated %s", ", ".join(p.name for p in report.written))
        return self.status

    def reload_config(self) -> None:
        with self._lock:
            self.config = Config.load(self.cfg_file)
            self._cfg_mtime = self._mtime()
            self.registry = self._load_registry()
        log.info("configuration reloaded")

    # -- change detection -----------------------------------------------------
    def _poll_changes(self) -> None:
        last = None
        while not self._stop.is_set():
            root = self.config.papers_dir
            if root is not None and root.is_dir():
                try:
                    sig = signature(root)
                except OSError:
                    sig = last
                if last is not None and sig != last:
                    self._wake.set()
                last = sig
            self._stop.wait(POLL)

    # -- ipc ---------------------------------------------------------------
    def dispatch(self, cmd: dict) -> dict:
        name = cmd.get("cmd")
        if name == "ping":
            return {"ok": True}
        if name == "status":
            # No lock: this must answer even while a sync is stuck (e.g. blocked by macOS privacy).
            return {
                "ok": True,
                "status": self.status,
                "syncing_since": self._syncing_since,
                "papers_dir": str(self.config.papers_dir) if self.config.papers_dir else None,
                "targets": [str(t) for t in self.config.targets],
                "interval": self.config.interval,
            }
        if name == "sync":
            return {"ok": True, "status": self.sync_now()}
        if name == "reload":
            self.reload_config()
            return {"ok": True, "status": self.sync_now()}
        if name == "keys":
            with self._lock:
                return {"ok": True, "keys": self.registry.keys}
        if name in ("rename_preview", "rename_apply", "rename_drop"):
            return self._rename(name, cmd.get("old", ""))
        if name in ("orphan_remove", "orphan_keep"):
            return self._orphan(name, cmd.get("key", ""))
        return {"ok": False, "error": f"unknown command: {name!r}"}

    def _rename(self, name: str, old: str) -> dict:
        with self._lock:
            rec = self.registry.keys.get(old)
            if not rec or not rec.get("renamed_to"):
                return {"ok": False, "error": f"no pending rename for key {old!r}"}
            new = rec["renamed_to"]
            roots = sorted({t.parent for t in self.config.targets})
            changes = rewrite.preview(roots, old, new)
            files = [{"path": str(c.path), "count": c.count, "diff": c.diff()} for c in changes]
            if name == "rename_preview":
                return {"ok": True, "old": old, "new": new, "files": files}
            if name == "rename_apply":
                rewrite.apply(changes)
            accept_rename(self.registry, old)
            self.registry.save()
            status = self.sync_now()
            return {"ok": True, "old": old, "new": new, "files": files, "status": status}

    def _orphan(self, name: str, key: str) -> dict:
        with self._lock:
            rec = self.registry.keys.get(key)
            if not rec or rec.get("orphaned_since") is None:
                return {"ok": False, "error": f"{key!r} is not an orphaned entry"}
            if name == "orphan_keep":
                rec["pinned"] = True
            else:
                rec.pop("pinned", None)
                rec["orphaned_since"] = "1970-01-01T00:00:00+00:00"  # past grace: dropped now
            self.registry.save()
            return {"ok": True, "status": self.sync_now()}

    # -- main loop ---------------------------------------------------------
    def stop(self, *_) -> None:
        self._stop.set()
        self._wake.set()

    def run(self, install_signals: bool = True) -> None:
        server = ipc.Server(self._socket, self.dispatch)
        server.start()
        if install_signals:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
            signal.signal(signal.SIGHUP, lambda *_: self._wake.set() or self._force_reload())
        threading.Thread(target=self._poll_changes, daemon=True, name="obib-poll").start()
        try:
            log.info(
                "starting; papers dir %s (every %ss)", self.config.papers_dir, self.config.interval
            )
            self.sync_now()
            log.info("first sync finished")
            while not self._stop.is_set():
                triggered = self._wake.wait(timeout=self.config.interval)
                if self._stop.is_set():
                    break
                if triggered:
                    self._stop.wait(DEBOUNCE)
                    self._wake.clear()
                if self._mtime() != self._cfg_mtime or self._reload:
                    self._reload = False
                    self.reload_config()
                self.sync_now()
        finally:
            server.close()

    def _force_reload(self) -> None:
        self._reload = True
