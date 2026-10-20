# Contributing

Thanks for helping. Bug reports, ideas and PRs are all welcome.

## Set up

```bash
git clone https://github.com/markgwharry/repowatch && cd repowatch
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before you open a PR

```bash
ruff check .
pytest
```

CI runs both on Windows, macOS and Linux, Python 3.11–3.13.

## How the tests work

`tests/conftest.py` builds a throwaway "code" folder once per session. Its repos have
remotes with real-looking URLs (`https://github.com/acme/…`, `https://gitea.example/acme/…`),
which a private gitconfig rewrites to local bare repos via `url.<base>.insteadOf`. So the
host-labelling logic sees realistic URLs, and nothing touches the network. Each test gets
its own copy, so tests can push, sync and prune freely.

When you add behaviour, add the repo state to `build_world` if it's new, then assert on it.
In particular, anything that pushes or deletes needs a test showing what it **refuses** to do.

## Principles

- **Never surprise the user with a destructive action.** Network-changing commands are dry runs
  unless `--apply` is given; sync is fast-forward only; prune never deletes unmerged work
  without an explicit flag and an open-PR check.
- **Never hang.** git runs without prompts. A remote that needs input is reported, not waited on.
- **Cross-platform.** Windows is a first-class target. Avoid shell-isms, and test paths with spaces.
- `core.py` has no UI code, and `cli.py` / `tui.py` don't run git directly.

## Releasing (maintainers)

1. Bump `__version__` in `repowatch/__init__.py` and move `[Unreleased]` items in `CHANGELOG.md`
   under the new version.
2. Commit, then tag `vX.Y.Z` and push the tag. The `release` workflow builds the package,
   publishes to PyPI via trusted publishing, and creates a GitHub release.
