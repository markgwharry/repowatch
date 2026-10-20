"""Test fixtures: a fake `code` folder whose repos have GitHub- and Gitea-style remotes.

Remotes use real-looking URLs (https://github.com/..., https://gitea.example/...) that a
private gitconfig rewrites to local bare repos with ``url.<base>.insteadOf``. So the
host-to-label logic sees realistic URLs while nothing touches the network. Pure Python, so
it works the same on Windows, macOS and Linux.

The full world is built once per session, then copied for each test that mutates it.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

GH = "https://github.com/acme/"
GT = "https://gitea.example/acme/"

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


def rmtree(path: Path) -> None:
    """shutil.rmtree that copes with git's read-only object files on Windows."""

    def _retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


def _gitconfig(root: Path) -> str:
    gh = (root / "remotes" / "github" / "acme").as_uri() + "/"
    gt = (root / "remotes" / "gitea" / "acme").as_uri() + "/"
    return (
        f'[url "{gh}"]\n\tinsteadOf = {GH}\n'
        f'[url "{gt}"]\n\tinsteadOf = {GT}\n'
        "[init]\n\tdefaultBranch = main\n"
        "[core]\n\tautocrlf = false\n"
        "[advice]\n\tdetachedHead = false\n"
    )


def _point_git_at(root: Path, monkeypatch: pytest.MonkeyPatch | None = None) -> None:
    cfg = root / "gitconfig"
    cfg.write_text(_gitconfig(root), encoding="utf-8")
    env = {"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1", **_IDENTITY}
    for k, v in env.items():
        if monkeypatch:
            monkeypatch.setenv(k, v)
        else:
            os.environ[k] = v


@dataclass
class World:
    root: Path

    @property
    def code(self) -> Path:
        return self.root / "code"

    def git(self, cwd: Path | str, *args: str) -> str:
        cp = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if cp.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{cp.stderr}")
        return cp.stdout.strip()

    def bare(self, host: str, name: str) -> None:
        d = self.root / "remotes" / host / "acme" / f"{name}.git"
        d.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.root, "init", "-q", "--bare", "-b", "main", str(d))

    def repo(self, name: str, rel: str | None = None) -> Path:
        """GitHub + Gitea bares, a clone with both remotes, everything in step."""
        rel = rel or name
        self.bare("github", name)
        self.bare("gitea", name)
        dest = self.code / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.root, "clone", "-q", f"{GH}{name}.git", str(dest))
        (dest / "README.md").write_text(f"# {name}\n")
        self.git(dest, "add", ".")
        self.git(dest, "commit", "-qm", "init")
        self.git(dest, "push", "-q", "origin", "main")
        self.git(dest, "remote", "add", "gitea", f"{GT}{name}.git")
        self.git(dest, "push", "-q", "gitea", "main")
        self.git(dest, "fetch", "-q", "--all")
        return dest

    def commit(self, repo: Path, msg: str, fname: str = "f.txt") -> str:
        with open(repo / fname, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", msg)
        return self.git(repo, "rev-parse", "HEAD")

    def commit_elsewhere(self, url: str, msg: str) -> None:
        """Simulate someone pushing to a remote from another machine."""
        tmp = self.root / "elsewhere"
        if tmp.exists():
            rmtree(tmp)
        self.git(self.root, "clone", "-q", url, str(tmp))
        self.commit(tmp, msg, fname=f"{msg}.txt")
        self.git(tmp, "push", "-q", "origin", "HEAD")
        rmtree(tmp)


def build_world(root: Path) -> World:
    """Every state repowatch distinguishes, one repo each."""
    w = World(root)
    w.code.mkdir(parents=True)
    _point_git_at(root)

    w.repo("core-lib")                                     # in step everywhere

    cli_tool = w.repo("cli-tool")                           # local ahead of both
    w.commit(cli_tool, "a")
    w.commit(cli_tool, "b")

    ticker = w.repo("ticker")                             # github ahead of gitea (mirror lag)
    w.commit(ticker, "x")
    w.git(ticker, "push", "-q", "origin", "main")
    w.git(ticker, "fetch", "-q", "--all")

    dart = w.repo("tracker", "work/tracker")    # local behind github
    w.commit_elsewhere(f"{GH}tracker.git", "from-mac")
    w.git(dart, "fetch", "-q", "origin")

    analysis = w.repo("analysis", "work/analysis")        # diverged + dirty + untracked + stash
    w.commit_elsewhere(f"{GH}analysis.git", "remote-side")
    w.commit(analysis, "local-side")
    w.git(analysis, "fetch", "-q", "origin")
    (analysis / "s.txt").write_text("s\n")
    w.git(analysis, "add", "s.txt")
    w.git(analysis, "stash", "-q")
    with open(analysis / "README.md", "a") as fh:
        fh.write("modified\n")
    (analysis / "new.txt").write_text("untracked\n")

    dashboard = w.repo("dashboard", "work/dashboard")    # feature branch only on github
    w.git(dashboard, "checkout", "-qb", "feature/kpi")
    w.commit(dashboard, "kpi")
    w.git(dashboard, "push", "-q", "-u", "origin", "feature/kpi")

    firmware = w.repo("firmware")                             # gitea unreachable
    w.git(firmware, "remote", "set-url", "gitea", f"{GT}does-not-exist.git")

    display = w.repo("display")                               # detached HEAD
    w.commit(display, "q")
    w.git(display, "push", "-q", "origin", "main")
    w.git(display, "checkout", "-q", "HEAD~1")

    fts = w.repo("sim", "work/sim")                       # gitea ahead of github
    w.commit_elsewhere(f"{GT}sim.git", "on-gitea")
    w.git(fts, "fetch", "-q", "--all")

    risk = w.repo("risk", "work/risk")                    # github and gitea diverged
    w.commit_elsewhere(f"{GH}risk.git", "on-github")
    w.commit_elsewhere(f"{GT}risk.git", "on-gitea")
    w.git(risk, "fetch", "-q", "--all")

    (w.code / "scratch").mkdir()                          # not a repo
    (cli_tool / "node_modules" / "x" / ".git").mkdir(parents=True)  # must be skipped
    return w


@pytest.fixture(scope="session")
def _world_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", *_IDENTITY)}
    root = tmp_path_factory.mktemp("world")
    try:
        build_world(root)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return root


@pytest.fixture
def world(_world_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A fresh, private copy of the world, safe to mutate."""
    root = tmp_path / "w"
    shutil.copytree(_world_template, root, symlinks=True)
    _point_git_at(root, monkeypatch)
    return World(root)


@pytest.fixture
def cfg():
    from repowatch.config import Config

    c = Config(refresh_seconds=0, fetch_timeout=20)
    c.hosts["gitea.example"] = "gitea"
    return c
