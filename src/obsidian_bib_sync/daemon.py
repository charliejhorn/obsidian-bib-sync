"""Background service: periodic + event-driven sync, with a unix-socket control interface."""

from __future__ import annotations

import logging
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import ipc
from .citekey import DEFAULT_REGISTRY_PATH, Registry
from .config import Config, config_path
from .fetch import Fetcher
from .sync import SyncReport, run_sync

log = logging.getLogger("obib")
DEBOUNCE = 1.0  # seconds to let a burst of file events settle


class _Wake(FileSystemEventHandler):
    def __init__(self, wake: threading.Event):
        self._wake = wake

    def on_any_event(self, event) -> None:
        paths = [str(getattr(event, "src_path", "")), str(getattr(event, "dest_path", ""))]
        if any(p.endswith(".md") or event.is_directory for p in paths):
            self._wake.set()


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
        self._observer: Observer | None = None
        self._watching: Path | None = None
        self.registry = self._load_registry()
        self.status: dict = {"last_sync": None, "ok": None, "entries": 0}

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
        }

    def sync_now(self) -> dict:
        with self._lock:
            report = run_sync(self.config, self.registry, fetcher=self.fetcher)
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
            self._watch()
        log.info("configuration reloaded")

    # -- file watching -----------------------------------------------------
    def _watch(self) -> None:
        if self._watching == self.config.papers_dir:
            return
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._watching = None
        root = self.config.papers_dir
        if root and root.is_dir():
            obs = Observer()
            obs.schedule(_Wake(self._wake), str(root), recursive=True)
            obs.start()
            self._observer, self._watching = obs, root

    # -- ipc ---------------------------------------------------------------
    def dispatch(self, cmd: dict) -> dict:
        name = cmd.get("cmd")
        if name == "ping":
            return {"ok": True}
        if name == "status":
            with self._lock:
                return {
                    "ok": True,
                    "status": self.status,
                    "papers_dir": str(self.config.papers_dir) if self.config.papers_dir else None,
                    "targets": [str(t) for t in self.config.targets],
                    "interval": self.config.interval,
                }
        if name == "sync":
            return {"ok": True, "status": self.sync_now()}
        if name == "reload":
            self.reload_config()
            return {"ok": True, "status": self.sync_now()}
        return {"ok": False, "error": f"unknown command: {name!r}"}

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
        try:
            self._watch()
            self.sync_now()
            log.info("watching %s (every %ss)", self.config.papers_dir, self.config.interval)
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
                else:
                    self._watch()  # e.g. the papers dir appeared since last time
                self.sync_now()
        finally:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)
            server.close()

    def _force_reload(self) -> None:
        self._reload = True
