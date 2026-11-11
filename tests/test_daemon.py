import tempfile
import threading
import time
from pathlib import Path

import pytest

from obsidian_bib_sync import ipc
from obsidian_bib_sync.config import Config
from obsidian_bib_sync.daemon import Daemon

NOTE = '---\ntype: article\ntitle: "{t}"\nauthors: [Smith, Jo]\nyear: 2020\n---\n'


@pytest.fixture
def running(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    bib = tmp_path / "refs.bib"
    cfg = Config(papers_dir=vault, targets=[bib], interval=1, registry_path=tmp_path / "k.json")
    sockdir = Path(tempfile.mkdtemp(prefix="obib"))
    sock = sockdir / "s.sock"
    d = Daemon(cfg, socket=sock, cfg_file=tmp_path / "cfg.toml")
    t = threading.Thread(target=d.run, kwargs={"install_signals": False}, daemon=True)
    t.start()
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.05)
    yield d, vault, bib, sock
    d.stop()
    t.join(timeout=10)
    assert not t.is_alive()
    assert not sock.exists()


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_detects_new_note_and_serves_status(running):
    d, vault, bib, sock = running
    (vault / "a.md").write_text(NOTE.format(t="Hello World"))
    assert wait_for(lambda: bib.exists() and "smith2020hello" in bib.read_text())
    st = ipc.request({"cmd": "status"}, sock)
    assert st["ok"] and st["status"]["entries"] == 1 and st["targets"] == [str(bib)]
    (vault / "b.md").write_text(NOTE.format(t="Second Paper"))
    reply = ipc.request({"cmd": "sync"}, sock)
    assert reply["status"]["entries"] == 2


def test_unknown_command_and_second_daemon(running):
    d, vault, bib, sock = running
    assert ipc.request({"cmd": "nope"}, sock)["ok"] is False
    with pytest.raises(RuntimeError):
        ipc.Server(sock, lambda c: {})


def test_client_without_daemon():
    with pytest.raises(ipc.DaemonNotRunning):
        ipc.request({"cmd": "ping"}, Path(tempfile.mkdtemp(prefix="obib")) / "none.sock")


def test_config_reload_picks_up_new_target(running, tmp_path):
    d, vault, bib, sock = running
    (vault / "a.md").write_text(NOTE.format(t="Hello World"))
    new = tmp_path / "second.bib"
    cfg = Config.load(d.cfg_file)  # missing file -> defaults
    cfg.papers_dir, cfg.targets, cfg.interval = vault, [bib, new], 1
    cfg.registry_path = tmp_path / "k.json"
    cfg.save(d.cfg_file)
    assert wait_for(lambda: new.exists() and "smith2020hello" in new.read_text())
