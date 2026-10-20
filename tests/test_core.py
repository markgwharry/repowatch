
import pytest

from repowatch import core
from repowatch.config import DEFAULT_HOSTS

# --------------------------------------------------------------------------- URL parsing


@pytest.mark.parametrize("url,host,owner_repo,label", [
    ("git@github.com:acme/cli-tool.git", "github.com", ("acme", "cli-tool"), "github"),
    ("https://github.com/acme/cli-tool", "github.com", ("acme", "cli-tool"), "github"),
    ("ssh://git@gitea.corp.local:2222/eng/core-lib.git", "gitea.corp.local", ("eng", "core-lib"), "gitea"),
    ("git@10.20.30.40:team/service.git", "10.20.30.40", ("team", "service"), "origin"),
    ("http://192.168.1.20:3000/acme/x.git", "192.168.1.20", ("acme", "x"), "origin"),
    ("C:/repos/x.git", "", ("repos", "x"), "origin"),
    ("/srv/git/x.git", "", ("git", "x"), "origin"),
])
def test_url_parsing(url, host, owner_repo, label):
    assert core.url_host(url) == host
    assert core.url_owner_repo(url) == owner_repo
    assert core.label_for("origin", url, DEFAULT_HOSTS) == label


def test_label_from_configured_host():
    hosts = {**DEFAULT_HOSTS, "10.20.30.40": "gitea"}
    assert core.label_for("work", "git@10.20.30.40:team/service.git", hosts) == "gitea"


# --------------------------------------------------------------------------- discovery


def test_find_repos_skips_non_repos_and_junk(world):
    found = {p.relative_to(world.code).as_posix() for p in core.find_repos(world.code, 3)}
    assert found == {
        "core-lib", "cli-tool", "ticker", "firmware", "display",
        "work/tracker", "work/analysis", "work/dashboard", "work/sim", "work/risk",
    }


def test_find_repos_respects_depth(world):
    names = {p.name for p in core.find_repos(world.code, 1)}
    assert "core-lib" in names and "tracker" not in names


# --------------------------------------------------------------------------- status


def _by_name(world, cfg, fetch=False):
    return {s.name: s for s in core.scan(world.code, cfg, do_fetch=fetch)}


def test_status_matrix(world, cfg):
    s = _by_name(world, cfg, fetch=True)

    assert s["core-lib"].flags() == []
    assert [r.state for r in s["core-lib"].remotes] == ["synced", "synced"]

    assert s["cli-tool"].remote("github").ahead == 2
    assert "push" in s["cli-tool"].flags()

    assert s["ticker"].remote("github").state == "synced"
    assert s["ticker"].remote("gitea").state == "ahead"
    assert s["ticker"].flags() == ["mirror lag"]

    assert s["work/tracker"].remote("github").behind == 1
    assert "pull" in s["work/tracker"].flags()

    analysis = s["work/analysis"]
    assert (analysis.unstaged, analysis.untracked, analysis.stashes) == (1, 1, 1)
    assert {"dirty", "untracked", "stash", "diverged"} <= set(analysis.flags())

    assert s["work/dashboard"].branch == "feature/kpi"
    assert s["work/dashboard"].remote("gitea").state == "missing"

    assert s["firmware"].remote("gitea").state == "unreachable"
    assert s["firmware"].remote("github").state == "synced"

    assert s["display"].detached
    assert s["display"].remote("github").ahead == 0 and s["display"].remote("github").behind == 1


def test_labels_use_host_not_remote_name(world, cfg):
    s = _by_name(world, cfg)
    assert {r.name: r.label for r in s["core-lib"].remotes} == {"origin": "github", "gitea": "gitea"}


def test_unfetched_status_does_not_touch_network(world, cfg):
    s = _by_name(world, cfg, fetch=False)
    assert s["firmware"].remote("gitea").state != "unreachable"   # no fetch, so no fetch error


def test_to_dict_is_json_ready(world, cfg):
    import json

    d = core.scan(world.code, cfg)[0].to_dict()
    json.dumps(d)
    assert {"flags", "remotes", "branch", "head"} <= d.keys()
    assert "state" in d["remotes"][0]


def test_add_remote(world, cfg):
    repo = world.code / "core-lib"
    assert core.add_remote(repo, "backup", "https://gitea.example/acme/core-lib.git") is None
    assert "backup" in core.list_remotes(repo)
    assert core.add_remote(repo, "backup", "x") is not None   # duplicate -> error text


def test_all_labels_order(world, cfg):
    statuses = core.scan(world.code, cfg)
    assert core.all_labels(statuses, ["github", "gitea"])[:2] == ["github", "gitea"]


def test_ago():
    import time

    assert core.ago(None) == "never"
    assert core.ago(time.time()) == "just now"
    assert core.ago(time.time() - 3 * 86400) == "3d ago"


def test_broken_repo_reports_error_not_crash(tmp_path, cfg):
    (tmp_path / "bad" / ".git").mkdir(parents=True)      # .git dir that isn't a repo
    [s] = core.scan(tmp_path, cfg)
    assert s.error and s.flags() == ["error"]
