"""Find and delete stale branches on the primary remote (GitHub).

A branch is deletable when its work is already in the default branch:
  merged         its tip is an ancestor of main (merge commit / fast-forward / rebase)
  squash-merged  its combined change already exists in main as one squashed commit

Unmerged branches are only reported, unless --include-unmerged DAYS is given, and then
only those older than DAYS with no open pull request (needs the GitHub CLI `gh`).
Never touched: the default branch, anything matching prune_keep, branches younger than
prune_min_age_days, and anything checked out locally.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from . import core
from .config import Config


@dataclass
class Branch:
    name: str
    sha: str
    age_days: int
    kind: str                  # merged | squash-merged | unmerged
    ahead: int = 0             # commits not in default (unmerged only)
    delete: bool = False
    reason: str = ""           # why kept
    deleted: bool | None = None


@dataclass
class PruneResult:
    path: str
    name: str
    remote: str = ""
    default: str = ""
    branches: list[Branch] = field(default_factory=list)
    skipped: str | None = None
    note: str | None = None
    error: str | None = None

    @property
    def to_delete(self) -> list[Branch]:
        return [b for b in self.branches if b.delete]


_ID = ["-c", "user.name=repowatch", "-c", "user.email=repowatch@localhost"]


def _squash_merged(path: str, sha: str, default_ref: str) -> bool:
    """True if the branch's net change is already in default (the git-delete-squashed trick)."""
    mb = core.git(path, "merge-base", default_ref, sha).stdout.strip()
    if not mb:
        return False
    tree = core.git(path, "rev-parse", f"{sha}^{{tree}}").stdout.strip()
    tmp = core.git(path, *_ID, "commit-tree", tree, "-p", mb, "-m", "repowatch-squash-probe").stdout.strip()
    if not tmp:
        return False
    out = core.git(path, "cherry", default_ref, tmp).stdout.strip()
    return out.startswith("-")


def _open_pr_heads(url: str) -> set[str] | None:
    """Head branch names of open PRs, via `gh`. None if gh is missing or fails."""
    if not shutil.which("gh") or "github.com" not in url:
        return None
    owner_repo = core.url_owner_repo(url)
    if not owner_repo:
        return None
    import subprocess

    try:
        cp = subprocess.run(
            ["gh", "pr", "list", "-R", "/".join(owner_repo), "--state", "open",
             "--json", "headRefName", "--limit", "500"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=core._NO_WINDOW,
        )
        if cp.returncode != 0:
            return None
        return {pr["headRefName"] for pr in json.loads(cp.stdout or "[]")}
    except Exception:
        return None


def plan_and_run(path: str, root: str, cfg: Config, apply: bool = False,
                 include_unmerged_days: int | None = None, min_age_days: int | None = None) -> PruneResult:
    status = core.repo_status(path, root, cfg)
    res = PruneResult(path=str(path), name=status.name)
    if status.error:
        res.skipped = status.error
        return res
    primary = next((r for r in status.remotes if r.label.split(":")[0] in cfg.primary_labels), None)
    if primary is None:
        res.skipped = "no primary remote"
        return res
    res.remote = primary.label

    errs = core.fetch_repo(path, cfg.fetch_timeout, remotes=[primary.name])
    if errs:
        res.skipped = f"{primary.label} unreachable: {errs[primary.name]}"
        return res

    # default branch
    cp = core.git(path, "symbolic-ref", "--short", f"refs/remotes/{primary.name}/HEAD")
    if cp.returncode != 0:
        core.git(path, "remote", "set-head", primary.name, "--auto", timeout=cfg.fetch_timeout)
        cp = core.git(path, "symbolic-ref", "--short", f"refs/remotes/{primary.name}/HEAD")
    default_ref = cp.stdout.strip()
    if not default_ref:
        for cand in ("main", "master"):
            if core.git(path, "rev-parse", "--verify", "-q", f"{primary.name}/{cand}").returncode == 0:
                default_ref = f"{primary.name}/{cand}"
                break
    if not default_ref:
        res.skipped = "can't tell the default branch"
        return res
    default = default_ref.split("/", 1)[1]
    res.default = default

    min_age = cfg.prune_min_age_days if min_age_days is None else min_age_days
    keep = set(cfg.prune_keep) | {default}
    local_heads = set(core.git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split())

    cp = core.git(path, "for-each-ref", "--format=%(refname:strip=3)%09%(objectname)%09%(committerdate:unix)",
                  f"refs/remotes/{primary.name}")
    now = time.time()
    open_prs: set[str] | None = None
    if include_unmerged_days is not None:
        open_prs = _open_pr_heads(primary.url)
        if open_prs is None:
            res.note = "unmerged branches not deleted: open-PR check needs the GitHub CLI (gh) signed in"

    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] in ("HEAD", default):
            continue
        name, sha, ts = parts
        age = int((now - int(ts)) / 86400) if ts.isdigit() else 0
        if core.git(path, "merge-base", "--is-ancestor", sha, default_ref).returncode == 0:
            kind = "merged"
        elif _squash_merged(path, sha, default_ref):
            kind = "squash-merged"
        else:
            kind = "unmerged"
        b = Branch(name=name, sha=sha, age_days=age, kind=kind)
        if kind == "unmerged":
            b.ahead = int(core.git(path, "rev-list", "--count", f"{default_ref}..{sha}").stdout.strip() or 0)

        if any(fnmatchcase(name, pat) for pat in keep):
            b.reason = "kept by prune_keep"
        elif name in local_heads and name == status.branch:
            b.reason = "checked out locally"
        elif age < min_age:
            b.reason = f"younger than {min_age}d"
        elif kind != "unmerged":
            b.delete = True
        elif include_unmerged_days is None:
            b.reason = "unmerged"
        elif open_prs is None:
            b.reason = "unmerged (no PR check)"
        elif name in open_prs:
            b.reason = "open PR"
        elif age < include_unmerged_days:
            b.reason = f"unmerged, younger than {include_unmerged_days}d"
        else:
            b.delete = True
        res.branches.append(b)

    order = {"merged": 0, "squash-merged": 1, "unmerged": 2}
    res.branches.sort(key=lambda b: (not b.delete, order[b.kind], b.name))

    if apply and res.to_delete:
        names = [b.name for b in res.to_delete]
        ssh = core._ssh_override(path)
        for i in range(0, len(names), 50):  # batches keep the command line short
            core.git(path, *ssh, "push", primary.name, "--delete", *names[i:i + 50],
                     timeout=cfg.fetch_timeout * 4)
        # Verify against the remote rather than parsing push output.
        cp = core.git(path, *ssh, "ls-remote", "--heads", primary.name, timeout=cfg.fetch_timeout)
        remaining = {l.split("refs/heads/", 1)[1] for l in cp.stdout.splitlines() if "refs/heads/" in l}
        if cp.returncode != 0:
            res.error = "couldn't verify deletions: " + (cp.stderr.strip().splitlines() or ["ls-remote failed"])[-1]
        for b in res.to_delete:
            b.deleted = None if cp.returncode != 0 else (b.name not in remaining)
        core.git(path, "fetch", "--prune", "--quiet", primary.name, timeout=cfg.fetch_timeout)
    return res


def prune_all(root: str, cfg: Config, apply: bool = False, names: list[str] | None = None,
              include_unmerged_days: int | None = None, min_age_days: int | None = None,
              workers: int = 4) -> list[PruneResult]:
    from concurrent.futures import ThreadPoolExecutor

    root = str(Path(root).expanduser().resolve())
    repos = [str(p) for p in core.find_repos(root, cfg.depth)]
    if names:
        wanted = {n.lower() for n in names}
        repos = [p for p in repos if Path(p).name.lower() in wanted
                 or Path(p).relative_to(root).as_posix().lower() in wanted]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: plan_and_run(p, root, cfg, apply, include_unmerged_days, min_age_days), repos))
