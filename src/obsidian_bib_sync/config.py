"""User configuration (TOML) and default locations."""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "obsidian-bib-sync" / "config.toml"


def config_path() -> Path:
    return Path(os.environ.get("OBIB_CONFIG", DEFAULT_CONFIG_PATH))


@dataclass
class Config:
    papers_dir: Path | None = None
    interval: int = 30  # seconds between full scans
    grace_days: int = 14  # how long an entry survives after its note disappears
    targets: list[Path] = field(default_factory=list)
    registry_path: Path | None = None  # default: next to the other state, see state_dir()

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text())
        return cls(
            papers_dir=Path(data["papers_dir"]).expanduser() if "papers_dir" in data else None,
            interval=int(data.get("interval", 30)),
            grace_days=int(data.get("grace_days", 14)),
            targets=[Path(t).expanduser() for t in data.get("targets", [])],
            registry_path=(
                Path(data["registry_path"]).expanduser() if "registry_path" in data else None
            ),
        )

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        data: dict = {"interval": self.interval, "grace_days": self.grace_days}
        if self.papers_dir:
            data["papers_dir"] = str(self.papers_dir)
        if self.registry_path:
            data["registry_path"] = str(self.registry_path)
        data["targets"] = [str(t) for t in self.targets]
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise

    def add_target(self, target: Path) -> bool:
        target = target.expanduser().resolve()
        if target in self.targets:
            return False
        self.targets.append(target)
        return True

    def remove_target(self, target: Path) -> bool:
        target = target.expanduser().resolve()
        if target not in self.targets:
            return False
        self.targets.remove(target)
        return True
