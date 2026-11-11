import argparse

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obib", description="Sync Obsidian papers to .bib files")
    parser.add_argument("--version", action="version", version=f"obib {__version__}")
    parser.parse_args(argv)
    return 0
