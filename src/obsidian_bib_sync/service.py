"""Run the daemon as a launchd user agent (starts at login, restarts if it exits)."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

LABEL = "com.obsidian-bib-sync.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "obsidian-bib-sync.log"


def build_plist(executable: str, log_path: Path = LOG_PATH) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [executable, "run"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def find_executable() -> str:
    """Absolute path of `obib` to put in the plist (the installed script, not `python -m`)."""
    exe = shutil.which("obib") or sys.argv[0]
    path = Path(exe).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path)


class Service:
    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        plist_path: Path = PLIST_PATH,
        log_path: Path = LOG_PATH,
        uid: int | None = None,
    ):
        self._run = run
        self.plist_path = plist_path
        self.log_path = log_path
        self.domain = f"gui/{os.getuid() if uid is None else uid}"

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["launchctl", *args], capture_output=True, text=True)

    def install(self, executable: str | None = None) -> str:
        exe = executable or find_executable()
        if not Path(exe).exists():
            raise RuntimeError(f"cannot find the obib executable at {exe}")
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.plist_path, "wb") as f:
            plistlib.dump(build_plist(exe, self.log_path), f)
        self._launchctl("bootout", f"{self.domain}/{LABEL}")  # reload cleanly if already loaded
        res = self._launchctl("bootstrap", self.domain, str(self.plist_path))
        if res.returncode != 0:
            raise RuntimeError(
                f"launchctl bootstrap failed: {res.stderr.strip() or res.stdout.strip()}"
            )
        return exe

    def uninstall(self) -> bool:
        self._launchctl("bootout", f"{self.domain}/{LABEL}")
        existed = self.plist_path.exists()
        self.plist_path.unlink(missing_ok=True)
        return existed

    def restart(self) -> None:
        res = self._launchctl("kickstart", "-k", f"{self.domain}/{LABEL}")
        if res.returncode != 0:
            raise RuntimeError(
                f"service is not loaded ({res.stderr.strip()}); run `obib service install`"
            )

    def is_loaded(self) -> bool:
        return self._launchctl("print", f"{self.domain}/{LABEL}").returncode == 0
