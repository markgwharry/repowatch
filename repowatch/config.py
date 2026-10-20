"""Config loading. Optional TOML file; everything has a sensible default."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket",
}

EXAMPLE_CONFIG = """\
# repowatch config

# Folder to scan when no path is given on the command line.
# root = "C:/Users/you/code"
# root = "~/code"

# How many folder levels below root to look for repos.
depth = 3

# Seconds between automatic local refreshes in the TUI (no network). 0 = off.
refresh_seconds = 30

# Seconds allowed per remote when fetching.
fetch_timeout = 25

# Column order in the matrix. Labels not listed here follow alphabetically.
column_order = ["github", "gitea"]

# Where work normally lands (pushes/PRs). `repowatch sync` treats this as the source.
primary_labels = ["github"]

# Secondary copies kept level with the primary by `repowatch sync` (fast-forward only,
# both directions). Their differences are reported as "mirror lag" rather than "push".
mirror_labels = ["gitea"]

# Which branches `repowatch sync` keeps level between primary and secondary.
# Glob patterns; a branch is synced if it matches sync_branches and not sync_exclude.
# The local checkout's current branch is fast-forwarded regardless.
sync_branches = ["main", "master"]
sync_exclude = ["dependabot/*"]

# `repowatch prune`: branches never deleted (globs; the default branch is always kept),
# and the minimum age before a merged branch is deleted.
prune_keep = ["main", "master", "develop", "release/*"]
prune_min_age_days = 7

# Map remote hosts to column labels. Exact host match first, then substring.
# Anything containing "gitea" is labelled "gitea" automatically.
[hosts]
"github.com" = "github"
# "git.example.internal" = "gitea"

# URL templates used to pre-fill the "add remote" dialog.
# {owner} and {repo} are taken from the repo's first remote URL.
[remote_templates]
# gitea = "https://git.example.internal/{owner}/{repo}.git"
"""


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "repowatch"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "repowatch"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Config:
    root: str | None = None
    depth: int = 3
    refresh_seconds: int = 30
    fetch_timeout: int = 25
    column_order: list[str] = field(default_factory=lambda: ["github", "gitea"])
    hosts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOSTS))
    remote_templates: dict[str, str] = field(default_factory=dict)
    mirror_labels: list[str] = field(default_factory=lambda: ["gitea"])
    primary_labels: list[str] = field(default_factory=lambda: ["github"])
    sync_branches: list[str] = field(default_factory=lambda: ["main", "master"])
    sync_exclude: list[str] = field(default_factory=lambda: ["dependabot/*"])
    prune_keep: list[str] = field(default_factory=lambda: ["main", "master", "develop", "release/*"])
    prune_min_age_days: int = 7


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    cfg = Config()
    if not path.exists():
        return cfg
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    cfg.root = data.get("root", cfg.root)
    cfg.depth = int(data.get("depth", cfg.depth))
    cfg.refresh_seconds = int(data.get("refresh_seconds", cfg.refresh_seconds))
    cfg.fetch_timeout = int(data.get("fetch_timeout", cfg.fetch_timeout))
    cfg.column_order = list(data.get("column_order", cfg.column_order))
    cfg.hosts.update({k.lower(): v for k, v in data.get("hosts", {}).items()})
    cfg.remote_templates.update(data.get("remote_templates", {}))
    cfg.mirror_labels = list(data.get("mirror_labels", cfg.mirror_labels))
    cfg.primary_labels = list(data.get("primary_labels", cfg.primary_labels))
    cfg.sync_branches = list(data.get("sync_branches", cfg.sync_branches))
    cfg.sync_exclude = list(data.get("sync_exclude", cfg.sync_exclude))
    cfg.prune_keep = list(data.get("prune_keep", cfg.prune_keep))
    cfg.prune_min_age_days = int(data.get("prune_min_age_days", cfg.prune_min_age_days))
    return cfg


def write_example_config(path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    return path
