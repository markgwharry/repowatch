"""prune: classification and safety. Builds its own small repo with dated branches."""

import os
import subprocess
import time

import pytest

from repowatch import prune
from tests.conftest import GH

OLD = "2020-01-01T12:00:00"


@pytest.fixture
def app_repo(world):
    """One repo whose GitHub remote has every branch kind prune distinguishes."""
    w = world
    w.bare("github", "app")
    dest = w.code / "app"
    w.git(w.root, "clone", "-q", f"{GH}app.git", str(dest))

    def commit(msg, fname=None, date=OLD):
        if fname:
            with open(dest / fname, "a") as fh:
                fh.write(msg + "\n")
            w.git(dest, "add", ".")
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.run(["git", "commit", "-qm", msg], cwd=dest, env=env, check=True)

    def branch(name):
        w.git(dest, "switch", "-qc", name)

    def main():
        w.git(dest, "switch", "-q", "main")

    commit("base", "a")
    w.git(dest, "push", "-q", "origin", "main")

    branch("merged-ff"); commit("ff", "f1"); w.git(dest, "push", "-q", "origin", "merged-ff")
    main(); w.git(dest, "merge", "-q", "--ff-only", "merged-ff")

    branch("merged-merge"); commit("mm", "f2"); w.git(dest, "push", "-q", "origin", "merged-merge")
    main(); w.git(dest, "merge", "-q", "--no-ff", "-m", "merge", "merged-merge")

    branch("squashed"); commit("s1", "f3"); commit("s2", "f4"); w.git(dest, "push", "-q", "origin", "squashed")
    main(); w.git(dest, "merge", "-q", "--squash", "squashed"); commit("squash: squashed")

    branch("unmerged-old"); commit("u1", "f5"); w.git(dest, "push", "-q", "origin", "unmerged-old")
    main()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    branch("unmerged-new"); commit("u2", "f6", date=now); w.git(dest, "push", "-q", "origin", "unmerged-new")
    main()
    branch("release/1.0"); w.git(dest, "push", "-q", "origin", "release/1.0")
    main()
    w.git(dest, "push", "-q", "origin", "main")
    return dest


def _plan(world, cfg, app_repo, **kw):
    [r] = prune.prune_all(str(world.code), cfg, names=["app"], **kw)
    return r, {b.name: b for b in r.branches}


def test_classification(world, cfg, app_repo):
    r, b = _plan(world, cfg, app_repo)
    assert r.default == "main"
    assert b["merged-ff"].kind == "merged" and b["merged-ff"].delete
    assert b["merged-merge"].kind == "merged" and b["merged-merge"].delete
    assert b["squashed"].kind == "squash-merged" and b["squashed"].delete
    assert b["unmerged-old"].kind == "unmerged" and not b["unmerged-old"].delete
    assert b["unmerged-new"].reason.startswith("younger than")
    assert b["release/1.0"].reason == "kept by prune_keep"


def test_unmerged_needs_pr_check(world, cfg, app_repo, monkeypatch):
    monkeypatch.setattr(prune, "_open_pr_heads", lambda url: None)    # gh unavailable
    _, b = _plan(world, cfg, app_repo, include_unmerged_days=30)
    assert not b["unmerged-old"].delete and "no PR check" in b["unmerged-old"].reason

    monkeypatch.setattr(prune, "_open_pr_heads", lambda url: {"unmerged-old"})
    _, b = _plan(world, cfg, app_repo, include_unmerged_days=30)
    assert not b["unmerged-old"].delete and b["unmerged-old"].reason == "open PR"

    monkeypatch.setattr(prune, "_open_pr_heads", lambda url: set())
    _, b = _plan(world, cfg, app_repo, include_unmerged_days=30)
    assert b["unmerged-old"].delete


def test_apply_deletes_only_planned(world, cfg, app_repo):
    r, _ = _plan(world, cfg, app_repo, apply=True)
    assert all(b.deleted for b in r.to_delete)
    left = world.git(world.root / "remotes" / "github" / "acme" / "app.git", "branch", "--format=%(refname:short)")
    assert set(left.split()) == {"main", "unmerged-old", "unmerged-new", "release/1.0"}


def test_dry_run_deletes_nothing(world, cfg, app_repo):
    _plan(world, cfg, app_repo)
    left = world.git(world.root / "remotes" / "github" / "acme" / "app.git", "branch")
    assert "merged-ff" in left
