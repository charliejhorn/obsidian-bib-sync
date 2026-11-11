"""One sync pass: scan notes -> assign keys -> render -> write every target."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import bibwriter
from .citekey import DEFAULT_REGISTRY_PATH, Registry, Warning_, resolve
from .config import Config
from .notes import Paper, scan, update_frontmatter


@dataclass
class SyncReport:
    entries: int = 0
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    warnings: list[Warning_] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)  # keys whose note is gone, within grace
    block: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def _now() -> datetime:
    return datetime.now(UTC)


def run_sync(
    config: Config, registry: Registry | None = None, now: datetime | None = None
) -> SyncReport:
    report = SyncReport()
    now = now or _now()
    if config.papers_dir is None:
        report.errors.append("papers_dir is not configured (run `obib init <dir>`)")
        return report
    if not config.papers_dir.is_dir():
        # Never treat an unavailable directory as "all notes deleted".
        report.errors.append(f"papers directory not found: {config.papers_dir}")
        return report
    if registry is None:
        registry = Registry.load(config.registry_path or DEFAULT_REGISTRY_PATH)
        registry.path = config.registry_path or DEFAULT_REGISTRY_PATH

    result = scan(config.papers_dir)
    for inv in result.invalid:
        report.warnings.append(Warning_("invalid", f"{inv.path}: {inv.reason}", [inv.path]))
    res = resolve(result.papers, registry)
    report.warnings += res.warnings

    # Persist generated keys into the notes (failure here must not break the bib output).
    for path, key in res.writeback.items():
        try:
            update_frontmatter(path, {"citekey": key})
        except (OSError, ValueError) as e:
            report.warnings.append(
                Warning_("writeback", f"cannot write citekey to {path}: {e}", [path])
            )

    by_path: dict[Path, Paper] = {p.path: p for p in result.papers}
    entries: dict[str, str] = {}
    live_paths = set(by_path)
    for path, key in res.keys.items():
        entry = bibwriter.render_entry(by_path[path], key)
        entries[key] = entry
        registry.keys[key]["entry"] = entry
        registry.keys[key]["orphaned_since"] = None

    # Notes that exist but are unusable right now keep their previous entry.
    retained = live_paths | {p.path for p in result.invalid} | {s.path for s in result.skipped}
    retained |= set(res.conflicts) | set(res.pending)
    emitted_paths = {str(p) for p in res.keys}
    for key, rec in registry.keys.items():
        if key in entries or rec.get("retired") or not rec.get("entry"):
            continue
        if rec.get("renamed_to"):
            continue  # alias handled below
        if Path(rec["path"]) in retained and rec["path"] not in emitted_paths:
            entries[key] = rec["entry"]  # temporarily unusable note: keep last good entry
            continue
        since = rec.get("orphaned_since")
        if since is None:
            since = rec["orphaned_since"] = now.isoformat(timespec="seconds")
        if now - datetime.fromisoformat(since) < timedelta(days=config.grace_days):
            entries[key] = rec["entry"]
            report.orphans.append(key)

    for old, new in res.aliases.items():
        if new in entries:
            entries[old] = bibwriter.rekey(entries[new], old)

    report.entries = len(entries)
    report.block = bibwriter.render_block(entries)
    registry.save()

    for target in config.targets:
        if not target.parent.is_dir():
            report.errors.append(f"{target}: directory does not exist")
            continue
        try:
            changed, outside = bibwriter.write_target(target, report.block)
        except OSError as e:
            report.errors.append(f"{target}: {e}")
            continue
        (report.written if changed else report.unchanged).append(target)
        for dup in sorted(outside & set(entries)):
            report.warnings.append(
                Warning_(
                    "duplicate",
                    f"{target}: key '{dup}' is also defined outside the managed block",
                    [target],
                )
            )
    return report
