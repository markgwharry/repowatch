# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-10-20

First public release.

### Changed
- `sync` now defaults to `main`/`master` only and skips `dependabot/*`. Previously every branch was synced. Set `sync_branches = ["*"]` for the old behaviour.
- The git environment is read on each call rather than at import, so environment changes (e.g. `GIT_CONFIG_GLOBAL`) take effect.

### Added
- A pytest suite that builds GitHub- and Gitea-style remotes for every state, with CI on Windows, macOS and Linux.
- MIT licence, contributing guide, issue and PR templates, and a PyPI release workflow.

## [0.3.0]

### Added
- `prune`: deletes merged and squash-merged branches on the primary remote. `--include-unmerged DAYS` also removes abandoned branches with no open PR (checked via `gh`).
- `sync_branches` / `sync_exclude` glob filters.

### Fixed
- `--config` placed before the subcommand no longer falls through to the TUI.

## [0.2.1]

### Fixed
- A missing root folder now gives a clear error instead of an empty matrix.

## [0.2.0]

### Added
- `sync`: fast-forward-only levelling between primary and secondary remotes and the local checkout, in both directions.
- TUI `s` / `S` keys with a plan-then-apply dialog.
- `schedule`: Windows Task Scheduler integration (or a crontab line on other platforms), with a rotating log.

## [0.1.0]

### Added
- Matrix of every repo under a folder against every remote, labelled by host.
- Textual TUI with fetch, add-remote, open-folder and attention filter; `status` with `--json` and `--attention`.

[Unreleased]: https://github.com/markgwharry/repowatch/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/markgwharry/repowatch/releases/tag/v0.4.0
