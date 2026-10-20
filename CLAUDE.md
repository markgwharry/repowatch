# repowatch: notes for Claude Code

Python 3.11+ CLI/TUI (Textual) that compares every git repo under a folder across all its remotes,
syncs a secondary remote with a primary one (fast-forward only), and prunes merged branches.

## Layout

- `repowatch/core.py`: discovery, per-remote status, fetch, URL→label mapping. No UI code.
- `repowatch/sync.py`: plan/apply sync. `prune.py`: classify/delete branches on the primary remote.
- `repowatch/cli.py`: argparse entry point (`tui` is the default subcommand). `tui.py`: Textual app.
- `repowatch/render.py`: Rich cells and text shared by the CLI and TUI. `config.py`: TOML config.
- `tests/conftest.py`: builds the fake world (bare remotes rewritten via `insteadOf`); every state
  repowatch distinguishes has a repo there.

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest                      # ~20 s; all tests offline
```

## Rules

- Anything that pushes, deletes or fast-forwards must be a dry run unless `--apply`, and needs a
  test for what it refuses to do.
- git calls go through `core.git()`, which disables prompts and hides console windows on Windows.
  Don't call `subprocess` for git elsewhere.
- Keep Windows working: no shell-isms, `Path` everywhere, and test with spaces in paths.
- Update `CHANGELOG.md` under `[Unreleased]` with user-facing changes.
