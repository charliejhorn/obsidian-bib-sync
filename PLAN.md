# obsidian-bib-sync — Plan

> First implementation step: copy this file to `PLAN.md` in the repo root and add a pointer in `CLAUDE.md`
> ("Review PLAN.md before each work session; update it when a decision changes"). Keep the Decisions and Status sections current.

## Status
- [x] Plan approved
- [x] M1 Scaffold (pyproject, uv, ruff, pytest, CI, CLAUDE.md)
- [x] M2 notes (frontmatter parse, scan, round-trip write; "First Last" splits on last word, so use "Last, First" for particles like "van der Berg")
- [x] M3 citekey
- [x] M4 bibwriter/sync/CLI (init, add, remove, list, sync; aliases are full duplicate entries; unusable notes keep last good entry; missing papers dir aborts sync)
- [x] M5 fetch (Crossref + arXiv, disk cache, 1h failure backoff, fills only empty fields)
- [x] M6 daemon/IPC (obib run, status; sync prefers the daemon; unix socket at ~/.local/state/obsidian-bib-sync/obib.sock)
- [ ] M7 TUI/rewrite
- [ ] M8 Homebrew/doctor

## Context
Papers are researched in Obsidian, one note per paper. The same bibliography must be available in many LaTeX
projects across the machine. This tool watches the papers directory and keeps a set of `.bib` files identical,
each reflecting every paper note. The main risk is changing an entry key, which silently breaks `\cite{}` in
the `.tex` files. Keys are therefore stable and tracked.

## Decisions (approved)
| Area | Decision |
|---|---|
| Language | Python (3.11+), packaged with `pyproject.toml`, installable via pipx and a Homebrew tap |
| Metadata | YAML frontmatter in each note, BibTeX-like fields |
| Auto-fetch | If `doi` or `arxiv` is set and core fields are empty, fetch and fill frontmatter |
| Cite key | `authorYearWord` (e.g. `vaswani2017attention`), generated once, stored in the note as `citekey`, never auto-changed |
| Runtime | Headless daemon run by `brew services`; separate TUI client |
| Bib ownership | Tool owns only a managed block inside each bib; the rest of the file is preserved |
| Deleted notes | Entry removed after a grace period; shown as a TUI warning because removal breaks `\cite` |
| Targets | Explicit list, managed in the TUI and via `obib add <path.bib>` |
| Key safety | Lock file registry of all issued keys, plus a TUI "rename" action that rewrites `\cite` in registered projects |

CLI command: `obib` (approved). Project, package (`obsidian_bib_sync`), Homebrew formula and config dir keep the `obsidian-bib-sync` name.

## Note syntax
Frontmatter (Obsidian Properties compatible, lowercase keys, lists as YAML lists):

```yaml
---
type: article            # article | inproceedings | book | misc | techreport | phdthesis ... (BibTeX entry type)
citekey: vaswani2017attention   # written by the tool on first sync; never edit casually
title: "Attention Is All You Need"
authors:                 # list, "Last, First" preferred; "First Last" is also parsed
  - Vaswani, Ashish
  - Shazeer, Noam
year: 2017
month: 6                 # optional
journal: NeurIPS         # or booktitle for inproceedings
volume:
number:
pages:
publisher:
doi: 10.48550/arXiv.1706.03762
arxiv: 1706.03762
url:
abstract:                # optional; excluded from bib by default
tags: [paper, transformers]   # ignored by the bib output
---
```
- Only `title`, `authors`, `year` (plus `type`) are required, or `doi`/`arxiv` alone (then fetched).
- Notes without `type` or without any of `title`/`doi`/`arxiv` are skipped and reported as "not a paper".
- Note body is ignored. Unknown frontmatter fields are ignored by default.
- Reuse Obsidian conventions: `tags`, `aliases` untouched; the tool only writes `citekey` and fetched fields.
  Writing back to frontmatter must preserve the rest of the file and key order (use `ruamel.yaml` round-trip).

## Cite key and \cite safety
1. `citekey` empty -> generate `authorYearWord`: ASCII-fold first author surname, lowercase, year, first non-stopword
   title word. Collision -> suffix `a`, `b`, ...
2. Write it into the note's frontmatter and into the **lock file** (`~/.local/state/obsidian-bib-sync/keys.json`:
   `key -> {note path/stable id, doi, first_issued}`).
3. Notes are identified by a stable id (`citekey` itself, falling back to doi, then file path), so renaming or moving a
   note file does not change the key.
4. If a note's `citekey` is edited by hand, the sync loop detects it against the lock file, **keeps the old key in
   the bib as well as the new one** (alias entry via `crossref`/duplicate), and raises a TUI warning, so no `\cite`
   breaks. The TUI offers "Accept rename" which rewrites `\cite*{old}` -> `\cite*{new}` across the registered
   LaTeX project dirs (with preview diff and backup) and then drops the alias.
5. A key is never reused for a different paper, even after deletion.
6. Two notes claiming the same key is a conflict: neither is written until resolved; shown in the TUI.

## Architecture
```
obsidian-bib-sync/
  pyproject.toml
  PLAN.md  CLAUDE.md  README.md
  src/obsidian_bib_sync/
    config.py       # TOML at ~/.config/obsidian-bib-sync/config.toml (vault dir, papers subdir, interval, grace days, targets)
    notes.py        # scan dir, parse frontmatter (python-frontmatter/ruamel), validate -> Paper model
    fetch.py        # DOI (Crossref content negotiation) + arXiv API lookup, cached, rate-limited
    citekey.py      # generation + lock file registry + rename detection
    bibwriter.py    # render entries; managed-block read/write (bibtexparser for parsing the rest), atomic write
    sync.py         # one sync pass: scan -> fetch -> keys -> render -> write all targets; hash-based change detection
    daemon.py       # loop: watchdog events + periodic full scan fallback; serves state over unix socket
    ipc.py          # JSON-over-unix-socket protocol (status, sync-now, list-warnings, accept-rename...)
    tui/            # Textual app: Status, Papers, Targets, Warnings/Renames, Log screens
    rewrite.py      # \cite-family rewriter for rename action (cite, citep, citet, autocite, parencite, textcite, ...)
    cli.py          # entry point: run, tui, sync, add, remove, list, doctor
  tests/
  packaging/homebrew/   # formula template for a tap; service block -> `brew services start obsidian-bib-sync`
```
- **Change detection:** poll interval (default 30s) plus watchdog FS events; compare per-note content hash; a full
  scan runs on start. Only write targets when rendered output hash changed (avoids churning LaTeX builds / cloud sync).
- **Bib output:** managed block between
  `% >>> obsidian-bib-sync (do not edit between markers) >>>` and `% <<< obsidian-bib-sync <<<`.
  Entries sorted by key for deterministic output. All targets get byte-identical blocks. Writes are atomic
  (temp file + rename). A target that does not exist is created. Hand-edited content inside the block is
  overwritten, but a backup is kept (`.bak`, last one only).
- **Brew service:** daemon is `obib run`; formula `service do run [opt_bin/"obib", "run"]; keep_alive true; log_path ...`.
  TUI (`obib tui`) is a client; it works (read-only, config edits only) even if the daemon is down and says so.
- **Add command:** `obib add path/to/refs.bib` registers the target (also `remove`, `list`). It writes
  config atomically; the daemon reloads config on change/SIGHUP. It also records the project dir (the bib's directory)
  for the rename rewriter.
- **Deleted notes:** entry stays through `grace_days` (default 14), flagged "orphan" in the TUI; the user can
  "remove now" or "restore". The key stays reserved in the lock file.
- **Failure handling:** fetch errors never block syncing other papers; offline = skip fetch, retry later.
  Invalid note -> warning with file and reason, previous entry retained.

## Milestones
1. Scaffold (console script `obib`): pyproject, package layout, CI (ruff, pytest), PLAN.md/CLAUDE.md in repo.
2. `notes.py` + Paper model + frontmatter spec tests (fixtures in `tests/fixtures/vault/`).
3. `citekey.py` + lock file + collision and rename-detection tests.
4. `bibwriter.py` managed block + atomic multi-target `sync.py` + `sync`/`add` CLI. **Usable CLI milestone.**
5. `fetch.py` DOI/arXiv auto-fill with cached HTTP fixtures (no live network in tests).
6. `daemon.py` + IPC + watchdog/poll; launchd run via brew service.
7. Textual TUI (status, targets, warnings, rename flow) and `rewrite.py`.
8. Homebrew tap formula, README, `doctor` command.
Review PLAN.md at the start and end of each milestone; update Decisions/Status if anything changes.

## Verification
- `pytest`: frontmatter parsing, key generation/collisions, lock/rename detection, managed-block merge (existing bib content preserved),
  byte-identical outputs across targets, cite rewrite on sample `.tex`, fetch parsers against recorded responses.
- End-to-end with a temp vault and two temp "LaTeX project" dirs: `add` both, add/edit/delete/rename notes, confirm
  both bibs identical and `\cite` keys unchanged; compile a sample `.tex` with `latexmk`/`bibtex` if available.
- Run daemon via `obib run`, edit a note, confirm bibs update within one interval; then `brew services` locally via the formula.
- Drive the TUI with Textual's `Pilot` tests and manually.

## Remaining open questions (low-stakes, will default if no answer)

- Default papers dir and interval (default: set in config at first run; interval 30s).
- Whether `abstract` should ever be emitted to bib (default: no).
