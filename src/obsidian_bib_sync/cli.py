import argparse
import sys
from pathlib import Path

from . import __version__, ipc
from .config import Config, config_path
from .fetch import Fetcher
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


def _print_status(st: dict) -> None:
    for w in st.get("warnings", []):
        print(f"warning [{w['kind']}]: {w['message']}", file=sys.stderr)
    for e in st.get("errors", []):
        print(f"error: {e}", file=sys.stderr)
    print(f"{st['entries']} entries; {len(st.get('written', []))} target(s) updated")


def _cmd_sync(args) -> int:
    try:  # prefer the running daemon so there is only ever one writer
        reply = ipc.request({"cmd": "sync"})
        _print_status(reply["status"])
        return 0 if reply["status"]["ok"] else 1
    except ipc.DaemonNotRunning:
        pass
    report = run_sync(Config.load(), fetcher=None if args.no_fetch else Fetcher())
    for w in report.warnings:
        print(f"warning [{w.kind}]: {w.message}", file=sys.stderr)
    for e in report.errors:
        print(f"error: {e}", file=sys.stderr)
    print(
        f"{report.entries} entries; {len(report.written)} target(s) updated, "
        f"{len(report.unchanged)} unchanged"
    )
    return 0 if report.ok else 1


def _cmd_run(args) -> int:
    import logging

    from .daemon import Daemon

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load()
    if cfg.papers_dir is None:
        print("error: papers_dir is not configured (run `obib init <dir>`)", file=sys.stderr)
        return 2
    try:
        Daemon(cfg, fetcher=Fetcher()).run()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args) -> int:
    try:
        reply = ipc.request({"cmd": "status"}, timeout=10)
    except ipc.DaemonNotRunning:
        print(
            "daemon is not running (start it with `brew services start obsidian-bib-sync`"
            " or `obib run`)"
        )
        return 1
    st = reply["status"]
    print(f"papers:    {reply['papers_dir']}")
    print(f"last sync: {st['last_sync']}  ({'ok' if st['ok'] else 'errors'})")
    print(f"entries:   {st['entries']}")
    print("targets:   " + ", ".join(reply["targets"]))
    for w in st.get("warnings", []):
        print(f"warning [{w['kind']}]: {w['message']}")
    for e in st.get("errors", []):
        print(f"error: {e}")
    return 0


def _cmd_doctor(args) -> int:
    from .citekey import DEFAULT_REGISTRY_PATH
    from .notes import scan

    problems = 0

    def check(ok: bool, good: str, bad: str) -> None:
        nonlocal problems
        print(("ok    " if ok else "FAIL  ") + (good if ok else bad))
        problems += not ok

    cfg_file = config_path()
    check(
        cfg_file.exists(), f"config: {cfg_file}", f"no config at {cfg_file} (run `obib init <dir>`)"
    )
    cfg = Config.load()
    check(cfg.papers_dir is not None, f"papers dir: {cfg.papers_dir}", "papers_dir is not set")
    if cfg.papers_dir is not None:
        exists = cfg.papers_dir.is_dir()
        check(exists, "papers dir exists", f"papers dir {cfg.papers_dir} does not exist")
        if exists:
            r = scan(cfg.papers_dir)
            print(
                f"      {len(r.papers)} paper note(s), {len(r.invalid)} invalid, "
                f"{len(r.skipped)} other note(s) ignored"
            )
            for inv in r.invalid:
                print(f"      invalid: {inv.path}: {inv.reason}")
    check(bool(cfg.targets), f"{len(cfg.targets)} target(s) registered", "no targets (`obib add`)")
    for t in cfg.targets:
        check(t.parent.is_dir(), f"target {t}", f"target directory missing: {t.parent}")
    reg = cfg.registry_path or DEFAULT_REGISTRY_PATH
    print(f"      key registry: {reg} ({'exists' if reg.exists() else 'not created yet'})")
    try:
        ipc.request({"cmd": "ping"}, timeout=5)
        check(True, "daemon is running", "")
    except ipc.DaemonNotRunning:
        check(False, "", "daemon is not running (`brew services start obsidian-bib-sync`)")
    return 1 if problems else 0


def _cmd_tui(args) -> int:
    from .tui.app import main as tui_main

    tui_main()
    return 0


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
    p.add_argument("--no-fetch", action="store_true", help="skip DOI/arXiv metadata lookup")
    p.set_defaults(func=_cmd_sync)

    p = sub.add_parser("run", help="run the sync daemon in the foreground (used by brew services)")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("status", help="show the running daemon's status")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("doctor", help="check configuration and environment")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("tui", help="open the terminal UI")
    p.set_defaults(func=_cmd_tui)

    args = parser.parse_args(argv)
    return args.func(args)
