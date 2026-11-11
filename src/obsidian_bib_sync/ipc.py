"""Newline-delimited JSON over a unix socket between the daemon and its clients (CLI, TUI)."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path

DEFAULT_SOCKET_PATH = Path.home() / ".local" / "state" / "obsidian-bib-sync" / "obib.sock"


def socket_path() -> Path:
    return Path(os.environ.get("OBIB_SOCKET", DEFAULT_SOCKET_PATH))


class DaemonNotRunning(Exception):
    pass


def _check_length(path: Path) -> None:
    if len(str(path).encode()) > 100:  # sun_path limit is ~104 bytes on macOS
        raise ValueError(f"socket path is too long for a unix socket: {path} (set OBIB_SOCKET)")


def request(cmd: dict, path: Path | None = None, timeout: float = 120) -> dict:
    path = path or socket_path()
    _check_length(path)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(json.dumps(cmd).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonNotRunning(str(path)) from e
    return json.loads(data)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            cmd = json.loads(self.rfile.readline())
            reply = self.server.dispatch(cmd)  # type: ignore[attr-defined]
        except Exception as e:  # a bad client must never take the daemon down
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, dispatch: Callable[[dict], dict]):
        _check_length(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                request({"cmd": "ping"}, path, timeout=2)
            except (DaemonNotRunning, OSError, ValueError):
                path.unlink(missing_ok=True)  # stale socket from a crashed daemon
            else:
                raise RuntimeError(f"another obib daemon is already running ({path})")
        self.dispatch = dispatch
        self.path = path
        super().__init__(str(path), _Handler)
        os.chmod(path, 0o600)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, daemon=True, name="obib-ipc")
        t.start()
        return t

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.path.unlink(missing_ok=True)
