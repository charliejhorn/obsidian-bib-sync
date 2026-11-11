"""Data access for the TUI: ask the daemon when it is up, otherwise read state files directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import ipc
from ..citekey import DEFAULT_REGISTRY_PATH, Registry
from ..config import Config


@dataclass
class View:
    running: bool
    status: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)
    papers_dir: str | None = None
    targets: list[str] = field(default_factory=list)
    interval: int = 30


def load_view() -> View:
    cfg = Config.load()
    try:
        st = ipc.request({"cmd": "status"}, timeout=5)
        keys = ipc.request({"cmd": "keys"}, timeout=5)["keys"]
        return View(True, st["status"], keys, st["papers_dir"], st["targets"], st["interval"])
    except (ipc.DaemonNotRunning, OSError, ValueError):
        reg = Registry.load(cfg.registry_path or DEFAULT_REGISTRY_PATH)
        return View(
            False,
            {},
            reg.keys,
            str(cfg.papers_dir) if cfg.papers_dir else None,
            [str(t) for t in cfg.targets],
            cfg.interval,
        )


def key_state(key: str, rec: dict) -> str:
    if rec.get("renamed_to"):
        return f"alias -> {rec['renamed_to']}"
    if rec.get("retired"):
        return "retired"
    if rec.get("orphaned_since"):
        return "orphan (kept)" if rec.get("pinned") else "orphan"
    return "live"


def add_target(path: str) -> str:
    """Register a target; returns an error message or ''."""
    cfg = Config.load()
    p = Path(path).expanduser()
    if p.suffix != ".bib":
        return "path must end in .bib"
    if not p.parent.is_dir():
        return f"directory {p.parent} does not exist"
    if cfg.add_target(p):
        cfg.save()
        _reload()
    return ""


def remove_target(path: str) -> bool:
    cfg = Config.load()
    if cfg.remove_target(Path(path)):
        cfg.save()
        _reload()
        return True
    return False


def _reload() -> None:
    try:
        ipc.request({"cmd": "reload"}, timeout=60)
    except (ipc.DaemonNotRunning, OSError, ValueError):
        pass  # the daemon picks the config change up on its own


def call(cmd: dict) -> dict:
    try:
        return ipc.request(cmd, timeout=120)
    except ipc.DaemonNotRunning:
        return {"ok": False, "error": "daemon is not running"}
