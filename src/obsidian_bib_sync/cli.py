import argparse
import sys
from pathlib import Path

from . import __version__
from .config import Config, config_path
from .sync import run_sync


def _cmd_init(args) -> int:
    cfg = Config.load()
    cfg.papers_dir = args.papers_dir.expanduser().resolve()
    cfg.save()
    print(f"papers directory: {cfg.papers_dir}")
    return 0


def _cmd_add(args) -> int:
    cfg = Config.load()
    target = args.bib.expanduser().resolve()
    if target.suffix != ".bib":
        print(f"error: {target} does not end in .bib", file=sys.stderr)
        return 2
    if not target.parent.is_dir():
        print(f"error: directory {target.parent} does not exist", file=sys.stderr)
        return 2
    if not cfg.add_target(target):
        print(f"already registered: {target}")
        return 0
    cfg.save()
    print(f"added {target}")
    return 0


def _cmd_remove(args) -> int:
    cfg = Config.load()
    if not cfg.remove_target(args.bib):
        print(f"not registered: {args.bib}", file=sys.stderr)
        return 1
    cfg.save()
    print(f"removed {args.bib.expanduser().resolve()} (its file is left untouched)")
    return 0


def _cmd_list(args) -> int:
    cfg = Config.load()
    print(f"config:  {config_path()}")
    print(f"papers:  {cfg.papers_dir or '(not set)'}")
    print("targets:" + ("" if cfg.targets else " (none)"))
    for t in cfg.targets:
        print(f"  {t}")
    return 0


def _cmd_sync(args) -> int:
    report = run_sync(Config.load())
    for w in report.warnings:
        print(f"warning [{w.kind}]: {w.message}", file=sys.stderr)
    for e in report.errors:
        print(f"error: {e}", file=sys.stderr)
    print(
        f"{report.entries} entries; {len(report.written)} target(s) updated, "
        f"{len(report.unchanged)} unchanged"
    )
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obib", description="Sync Obsidian papers to .bib files")
    parser.add_argument("--version", action="version", version=f"obib {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="set the Obsidian papers directory")
    p.add_argument("papers_dir", type=Path)
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("add", help="register a .bib file to keep in sync")
    p.add_argument("bib", type=Path)
    p.set_defaults(func=_cmd_add)

    p = sub.add_parser("remove", help="stop syncing a .bib file")
    p.add_argument("bib", type=Path)
    p.set_defaults(func=_cmd_remove)

    p = sub.add_parser("list", help="show configuration and targets")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("sync", help="run one sync pass now")
    p.set_defaults(func=_cmd_sync)

    args = parser.parse_args(argv)
    return args.func(args)
