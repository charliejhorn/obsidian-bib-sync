import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_bib_sync.service import LABEL, Service, build_plist


class FakeLaunchctl:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        rc = 1 if self.fail_on and self.fail_on in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="boom" if rc else "")


def make(tmp_path, **kw):
    fake = FakeLaunchctl(**kw)
    return Service(
        run=fake,
        plist_path=tmp_path / "la" / "x.plist",
        log_path=tmp_path / "log" / "o.log",
        uid=501,
    ), fake


def test_plist_contents():
    p = build_plist("/u/.local/bin/obib", Path("/l/o.log"))
    assert p["Label"] == LABEL and p["ProgramArguments"] == ["/u/.local/bin/obib", "run"]
    assert p["RunAtLoad"] and p["KeepAlive"] and p["StandardOutPath"] == "/l/o.log"


def test_install_writes_plist_and_bootstraps(tmp_path):
    s, fake = make(tmp_path)
    exe = s.install(sys.executable)
    assert plistlib.loads(s.plist_path.read_bytes())["ProgramArguments"] == [exe, "run"]
    assert ["launchctl", "bootstrap", "gui/501", str(s.plist_path)] in fake.calls
    assert fake.calls[0][:2] == ["launchctl", "bootout"]  # reloads cleanly if already loaded


def test_install_reports_bootstrap_failure_and_missing_exe(tmp_path):
    s, _ = make(tmp_path, fail_on="bootstrap")
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        s.install(sys.executable)
    with pytest.raises(RuntimeError, match="cannot find"):
        make(tmp_path)[0].install("/nonexistent/obib")


def test_uninstall_and_restart(tmp_path):
    s, fake = make(tmp_path)
    assert s.uninstall() is False
    s.install(sys.executable)
    assert s.uninstall() is True and not s.plist_path.exists()
    s.restart()
    assert ["launchctl", "kickstart", "-k", f"gui/501/{LABEL}"] in fake.calls
    with pytest.raises(RuntimeError, match="not loaded"):
        make(tmp_path, fail_on="kickstart")[0].restart()
