# obsidian-bib-sync

Keep one `.bib` file per LaTeX project identical to the papers you collect in Obsidian.
Each paper is one note with YAML frontmatter; a background service watches the notes and
rewrites a managed block in every registered `.bib`. CLI command: `obib`.

## Quick start

```sh
pipx install .                    # or install via Homebrew, see "Homebrew install" below
obib init ~/Obsidian/Papers       # folder with one note per paper
obib add ~/projects/thesis/refs.bib
obib add ~/projects/talk/refs.bib
brew services start obsidian-bib-sync   # Homebrew installs only; otherwise run `obib run` in a terminal
obib tui                          # status, renames, targets
obib doctor                       # check the setup
```

`obib sync` runs one pass now (it asks the daemon if one is running).

## Homebrew install (runs at login via `brew services`)

`brew services` only manages Homebrew formulae, so the formula has to live in a tap: a GitHub repo
named `homebrew-<name>` that you own. With `github.com/<you>/homebrew-tap`, the tap is `<you>/tap`.
`packaging/homebrew/obsidian-bib-sync.rb` is the formula template.

1. Publish the project and tag a release:
   ```sh
   git tag v0.1.0 && git push origin main --tags
   curl -L https://github.com/<you>/obsidian-bib-sync/archive/refs/tags/v0.1.0.tar.gz | shasum -a 256
   ```
2. Create the tap and add the formula:
   ```sh
   brew tap-new <you>/tap
   cp packaging/homebrew/obsidian-bib-sync.rb "$(brew --repository <you>/tap)/Formula/"
   ```
   In the copied file, replace `OWNER` with your GitHub username and `REPLACE_WITH_TARBALL_SHA256`
   with the hash from step 1.
3. Generate the Python dependency blocks:
   ```sh
   brew update-python-resources <you>/tap/obsidian-bib-sync
   ```
4. Push the tap: in the directory printed by `brew --repository <you>/tap`, create
   `github.com/<you>/homebrew-tap` and push to it.
5. Install and start the service:
   ```sh
   brew install <you>/tap/obsidian-bib-sync
   obib init ~/Obsidian/Papers && obib add ~/projects/thesis/refs.bib
   brew services start obsidian-bib-sync
   ```

The service runs `obib run` at login and restarts it if it exits. Its log is at
`$(brew --prefix)/var/log/obsidian-bib-sync.log`. Use `brew services restart obsidian-bib-sync`
after upgrading. Without Homebrew, `pipx install .` and `obib run` in a terminal also work, but
nothing starts it at login.

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

See `PLAN.md` for the design decisions and status; `packaging/homebrew/` has the formula template.
