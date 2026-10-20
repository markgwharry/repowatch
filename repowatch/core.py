"""Repo discovery and status. No UI code here — everything returns plain data."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .config import Config

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", "build", "dist", "target", ".idea", ".vscode", ".gradle",
}

# Never let git block on a prompt: no terminal prompts, no credential-manager pop-ups.
_GIT_ENV_OVERRIDES = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "LC_ALL": "C",
}


def git_env() -> dict[str, str]:
    """Current environment plus the no-prompt overrides (read per call, not at import)."""
    return {**os.environ, **_GIT_ENV_OVERRIDES}

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def git(path: Path | str, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=git_env(),
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


# --------------------------------------------------------------------------- data


@dataclass
class RemoteStatus:
    name: str
    label: str
    url: str
    host: str
    ref: str | None = None          # e.g. "origin/main"
    sha: str | None = None
    ahead: int | None = None        # local commits not on remote  (→ needs push)
    behind: int | None = None       # remote commits not in local  (→ needs pull)
    error: str | None = None        # last fetch error, if any
    mirror: bool = False            # read-only mirror of another remote (e.g. a Gitea pull mirror)

    @property
    def state(self) -> str:
        if self.error:
            return "unreachable"
        if self.sha is None:
            return "missing"
        if self.ahead is None:
            return "unknown"
        if self.ahead == 0 and self.behind == 0:
            return "synced"
        if self.ahead and self.behind:
            return "diverged"
        return "ahead" if self.ahead else "behind"


@dataclass
class RepoStatus:
    path: str
    name: str
    branch: str | None = None
    detached: bool = False
    head: str | None = None
    upstream: str | None = None
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicts: int = 0
    stashes: int = 0
    last_fetch: float | None = None
    remotes: list[RemoteStatus] = field(default_factory=list)
    error: str | None = None

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.unstaged or self.conflicts)

    def remote(self, label: str) -> RemoteStatus | None:
        return next((r for r in self.remotes if r.label == label), None)

    def flags(self) -> list[str]:
        if self.error:
            return ["error"]
        out: list[str] = []
        if self.conflicts:
            out.append("conflicts")
        if self.dirty:
            out.append("dirty")
        if self.untracked:
            out.append("untracked")
        if self.stashes:
            out.append("stash")
        primary = [r for r in self.remotes if not r.mirror]
        mirrors = [r for r in self.remotes if r.mirror]
        states = {r.state for r in primary}
        if "diverged" in states:
            out.append("diverged")
        if "ahead" in states:
            out.append("push")
        if "behind" in states:
            out.append("pull")
        tips = {r.sha for r in primary if r.sha}
        if len(tips) > 1:
            out.append("remotes differ")
        # A mirror is "lagging" if it doesn't match what the primary remote(s) have.
        if tips and any(m.sha not in tips and m.state != "unreachable" for m in mirrors):
            out.append("mirror lag")
        if "unreachable" in {r.state for r in self.remotes}:
            out.append("unreachable")
        if not self.remotes:
            out.append("no remotes")
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flags"] = self.flags()
        for rd, r in zip(d["remotes"], self.remotes, strict=True):
            rd["state"] = r.state
        return d


# ----------------------------------------------------------------------- discovery


def find_repos(root: Path | str, max_depth: int = 3) -> list[Path]:
    root = Path(root).expanduser().resolve()
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if (d / ".git").exists():
            found.append(d)
            return  # don't descend into repos (submodules/vendored repos ignored)
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and e.name not in SKIP_DIRS and not e.name.startswith("."):
                walk(Path(e.path), depth + 1)

    walk(root, 0)
    return found


# ---------------------------------------------------------------------- remotes


_SCP_LIKE = re.compile(r"^(?:[\w.+-]+@)?(?P<host>[\w.-]+):(?!//)(?P<path>.+)$")


def url_host(url: str) -> str:
    if "://" not in url:
        m = _SCP_LIKE.match(url)
        # Guard against Windows drive letters (C:/...) being read as hosts.
        if m and len(m.group("host")) > 1:
            return m.group("host").lower()
        return ""
    return (urlparse(url).hostname or "").lower()


def url_owner_repo(url: str) -> tuple[str, str] | None:
    if "://" in url:
        path = urlparse(url).path
    else:
        m = _SCP_LIKE.match(url)
        path = m.group("path") if m else url
    parts = [p for p in re.split(r"[\\/]", path) if p]
    if len(parts) < 2:
        return None
    repo = parts[-1].removesuffix(".git")
    return parts[-2], repo


def label_for(remote_name: str, url: str, hosts: dict[str, str]) -> str:
    host = url_host(url)
    if host:
        if host in hosts:
            return hosts[host]
        for key, lab in hosts.items():
            if key in host:
                return lab
        if "gitea" in host:
            return "gitea"
        if "github" in host:
            return "github"
    return remote_name


def list_remotes(path: Path | str) -> dict[str, str]:
    """Remote name -> URL as configured (before any url.insteadOf rewriting)."""
    out: dict[str, str] = {}
    cp = git(path, "config", "--local", "--get-regexp", r"^remote\..*\.url$")
    for line in cp.stdout.splitlines():
        key, _, url = line.partition(" ")
        name = key[len("remote."):-len(".url")]
        out.setdefault(name, url.strip())
    return out


# ----------------------------------------------------------------------- status


def _parse_status(text: str, st: RepoStatus) -> None:
    for line in text.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2]
            st.head = None if oid == "(initial)" else oid
        elif line.startswith("# branch.head "):
            head = line.split(" ", 2)[2]
            st.detached = head == "(detached)"
            st.branch = None if st.detached else head
        elif line.startswith("# branch.upstream "):
            st.upstream = line.split(" ", 2)[2]
        elif line.startswith(("1 ", "2 ")):
            xy = line[2:4]
            if xy[0] != ".":
                st.staged += 1
            if xy[1] != ".":
                st.unstaged += 1
        elif line.startswith("u "):
            st.conflicts += 1
        elif line.startswith("? "):
            st.untracked += 1


def repo_status(
    path: Path | str,
    root: Path | str,
    cfg: Config,
    fetch_errors: dict[str, str] | None = None,
) -> RepoStatus:
    path = Path(path)
    try:
        name = path.relative_to(Path(root).expanduser().resolve()).as_posix() or path.name
    except ValueError:
        name = path.name
    st = RepoStatus(path=str(path), name=name or path.name)
    fetch_errors = fetch_errors or {}

    try:
        cp = git(path, "status", "--porcelain=v2", "--branch")
        if cp.returncode != 0:
            st.error = (cp.stderr.strip().splitlines() or ["git status failed"])[-1]
            return st
        _parse_status(cp.stdout, st)

        cp = git(path, "stash", "list", "--format=%h")
        st.stashes = len([l for l in cp.stdout.splitlines() if l.strip()])

        gd = git(path, "rev-parse", "--git-common-dir").stdout.strip() or ".git"
        fh = (path / gd) / "FETCH_HEAD"  # absolute gd overrides path on join
        if fh.exists():
            st.last_fetch = fh.stat().st_mtime

        remotes = list_remotes(path)
        cp = git(path, "for-each-ref", "--format=%(refname:strip=2)%09%(objectname)", "refs/remotes")
        refs = dict(l.split("\t", 1) for l in cp.stdout.splitlines() if "\t" in l)

        used_labels: set[str] = set()
        for rname, url in remotes.items():
            label = label_for(rname, url, cfg.hosts)
            if label in used_labels:
                label = f"{label}:{rname}"
            used_labels.add(label)
            rs = RemoteStatus(name=rname, label=label, url=url, host=url_host(url),
                              error=fetch_errors.get(rname),
                              mirror=label.split(":")[0] in cfg.mirror_labels)

            if st.branch:
                candidates = [f"{rname}/{st.branch}"]
            else:  # detached: compare to the remote's default branch
                candidates = [f"{rname}/HEAD", f"{rname}/main", f"{rname}/master"]
            for cand in candidates:
                if cand in refs:
                    rs.ref, rs.sha = cand, refs[cand]
                    break

            if rs.sha and st.head:
                if rs.sha == st.head:
                    rs.ahead = rs.behind = 0
                else:
                    cp = git(path, "rev-list", "--left-right", "--count", f"HEAD...{rs.sha}")
                    if cp.returncode == 0 and cp.stdout.strip():
                        a, b = cp.stdout.split()
                        rs.ahead, rs.behind = int(a), int(b)
            st.remotes.append(rs)
    except subprocess.TimeoutExpired:
        st.error = "git timed out"
    except Exception as exc:  # keep one broken repo from killing the whole scan
        st.error = f"{type(exc).__name__}: {exc}"
    return st


# ------------------------------------------------------------------------ fetch


def _ssh_override(path: Path | str) -> list[str]:
    """Make ssh fail fast instead of prompting, unless the user has their own sshCommand."""
    cp = git(path, "config", "--get", "core.sshCommand")
    if cp.returncode == 0 and cp.stdout.strip():
        return []
    return ["-c", "core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=8"]


def fetch_repo(path: Path | str, timeout: float = 25, remotes: list[str] | None = None) -> dict[str, str]:
    """Fetch each remote separately so one unreachable remote doesn't block the others.
    Returns {remote_name: error} for the ones that failed."""
    errors: dict[str, str] = {}
    names = remotes or list(list_remotes(path))
    ssh = _ssh_override(path)
    for rname in names:
        try:
            cp = git(path, *ssh, "fetch", "--prune", "--quiet", rname, timeout=timeout)
            if cp.returncode != 0:
                lines = [l.strip() for l in cp.stderr.splitlines() if l.strip()]
                key = next((l for l in lines if l.startswith(("fatal:", "error:"))), None)
                errors[rname] = key or (lines[0] if lines else f"fetch failed ({cp.returncode})")
        except subprocess.TimeoutExpired:
            errors[rname] = f"timed out after {timeout:.0f}s"
    return errors


def add_remote(path: Path | str, name: str, url: str) -> str | None:
    cp = git(path, "remote", "add", name, url)
    if cp.returncode != 0:
        return (cp.stderr.strip().splitlines() or ["git remote add failed"])[-1]
    return None


# ------------------------------------------------------------------------- bulk


def scan(
    root: Path | str,
    cfg: Config,
    do_fetch: bool = False,
    workers: int = 8,
    fetch_errors: dict[str, dict[str, str]] | None = None,
) -> list[RepoStatus]:
    repos = find_repos(root, cfg.depth)
    fetch_errors = fetch_errors if fetch_errors is not None else {}

    def one(p: Path) -> RepoStatus:
        if do_fetch:
            fetch_errors[str(p)] = fetch_repo(p, cfg.fetch_timeout)
        return repo_status(p, root, cfg, fetch_errors.get(str(p)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, repos))


def all_labels(statuses: list[RepoStatus], order: list[str]) -> list[str]:
    seen = {r.label for s in statuses for r in s.remotes}
    first = [l for l in order if l in seen]
    return first + sorted(seen - set(first))


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{d / 60:.0f}m ago"
    if d < 86400 * 2:
        return f"{d / 3600:.0f}h ago"
    return f"{d / 86400:.0f}d ago"
