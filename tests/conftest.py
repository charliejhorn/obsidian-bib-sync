import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_daemon_and_config(monkeypatch, tmp_path):
    """Never talk to a real running daemon or read the real config during tests."""
    sock = Path(tempfile.mkdtemp(prefix="obib")) / "none.sock"
    monkeypatch.setenv("OBIB_SOCKET", str(sock))
    monkeypatch.setenv("OBIB_CONFIG", str(tmp_path / "config.toml"))
