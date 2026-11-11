# obsidian-bib-sync

Keep one `.bib` file per LaTeX project identical to the papers you collect in Obsidian.
Each paper is one note with YAML frontmatter; a background service watches the notes and
rewrites a managed block in every registered `.bib`. CLI command: `obib`.

## Quick start

```sh
uv tool install --editable .      # `obib` on your PATH; source edits take effect immediately
obib init ~/Obsidian/Papers       # folder with one note per paper
obib add ~/projects/thesis/refs.bib
obib add ~/projects/talk/refs.bib
obib service install              # background service, starts at login (or `obib run` in a terminal)
obib tui                          # status, renames, targets
obib doctor                       # check the setup
```

`obib sync` runs one pass now (it asks the daemon if one is running).

## Running in the background (macOS)

`obib service install` writes a launchd user agent (`~/Library/LaunchAgents/com.obsidian-bib-sync.daemon.plist`)
that runs `obib run` at login and restarts it if it exits. Related commands:

```sh
obib service status      # loaded / not loaded
obib service restart     # e.g. after editing the source of an editable install
obib service logs        # follow ~/Library/Logs/obsidian-bib-sync.log
obib service uninstall   # stop and remove the agent
obib status              # what the daemon last did, plus any warnings
```

**Grant the service access to your notes folder.** macOS blocks background processes from reading
`~/Documents`, `~/Desktop`, iCloud Drive and similar folders until you allow it. Until then the
daemon sits in its first scan and `obib doctor` / `obib status` report "sync in progress for Ns".
Fix it once:

1. Find the Python the service uses: `readlink -f "$(head -1 "$(which obib)" | sed 's/^#!//')"`
   (or look at the process path with `ps aux | grep "obib run"`).
2. System Settings > Privacy & Security > Full Disk Access > `+`, press Cmd+Shift+G, and add the
   `Python.app` that Python lives in (for Homebrew Python:
   `/opt/homebrew/opt/python@3.15/Frameworks/Python.framework/Versions/3.15/Resources/Python.app`).
3. `obib service restart`.

Keeping your notes outside those protected folders avoids the problem entirely.

## Note syntax

```yaml
---
type: article            # BibTeX entry type: article, inproceedings, book, misc, techreport, ...
title: "Attention Is All You Need"   # quote titles containing ':'
authors:
  - Vaswani, Ashish      # "Last, First" preferred; "First Last" also works
  - Shazeer, Noam
year: 2017
journal: NeurIPS         # booktitle for inproceedings
doi: 10.48550/arXiv.1706.03762
arxiv: 1706.03762
citekey: vaswani2017attention   # written by obib on first sync
tags: [paper]            # ignored
---
```

- Give only `type` plus a `doi` and/or `arxiv` id: the missing fields are fetched (Crossref/arXiv)
  and written into the note. Fields you already filled are never overwritten.
- Otherwise `title`, `authors` and `year` are required. Notes without `type` are ignored;
  notes with bad YAML are reported and keep their last good entry.
- Other optional fields: `month`, `volume`, `number`, `pages`, `publisher`, `url`.
- Use `Last, First` for names with particles (`van der Berg, Jan`).
- YAML gotcha: in a one-line list, `authors: [Doe, Jane]` is **two** authors. Quote each name
  (`authors: ["Doe, Jane"]`) or use the multi-line `- Doe, Jane` form shown above.

## Cite keys and `\cite{}`

Keys look like `vaswani2017attention` and are generated **once**, then stored in the note's
`citekey` field. Changing the title, year or file name never changes the key, so existing
`\cite` commands keep working. Keys are recorded in `~/.local/state/obsidian-bib-sync/keys.json`
and never reused, even after a note is deleted.

- **Edit `citekey` by hand?** The old key stays in the bib as an alias so nothing breaks. In
  `obib tui` > *Renames & orphans* press `r` to preview the `\cite` rewrite across the `.tex`
  files next to your registered bibs, then apply (originals kept as `*.tex.obib.bak`).
- **Delete a note?** Its entry stays for `grace_days` (14) and is flagged as an orphan; drop (`x`)
  or keep (`k`) it in the TUI.
- Hand-written entries in a `.bib` are preserved: only the block between the
  `obsidian-bib-sync` marker comments is managed (a `.bak` of the previous file is kept).

## Configuration

`~/.config/obsidian-bib-sync/config.toml` (managed by the commands above):

```toml
papers_dir = "/Users/me/Obsidian/Papers"
interval = 30        # seconds between full scans (file changes trigger a sync immediately)
grace_days = 14
targets = ["/Users/me/projects/thesis/refs.bib"]
```

## Development

```sh
uv sync && uv run pytest && uv run ruff check .
```

See `PLAN.md` for the design decisions and status.
