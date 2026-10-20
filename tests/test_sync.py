from repowatch.sync import branch_selected, sync_all


def _results(world, cfg, apply=False):
    return {r.name: r for r in sync_all(str(world.code), cfg, apply=apply)}


def _texts(res):
    return [s.text for s in res.steps]


def test_dry_run_changes_nothing(world, cfg):
    before = world.git(world.root / "remotes" / "gitea" / "acme" / "ticker.git", "rev-parse", "main")
    r = _results(world, cfg)
    assert any("gitea +1 from github" in t for t in _texts(r["ticker"]))
    after = world.git(world.root / "remotes" / "gitea" / "acme" / "ticker.git", "rev-parse", "main")
    assert before == after
    assert all(s.ok is None for res in r.values() for s in res.steps)


def test_apply_levels_everything_it_safely_can(world, cfg):
    r = _results(world, cfg, apply=True)
    assert not [s for res in r.values() for s in res.failed]

    # github -> gitea fast-forward
    assert any("gitea +1 from github" in t for t in _texts(r["ticker"]))
    # gitea -> github fast-forward (work done on the work network flows up)
    assert any("github +1 from gitea" in t for t in _texts(r["work/sim"]))
    # local fast-forwarded when clean and behind
    assert any("local main: fast-forward" in t for t in _texts(r["work/tracker"]))

    # second run: nothing left to do except the things that need a human
    again = _results(world, cfg, apply=True)
    assert sum(len(res.actions) for res in again.values()) == 0


def test_refuses_what_it_should(world, cfg):
    r = _results(world, cfg)
    risk = _texts(r["work/risk"])
    assert any("diverged" in t and "resolve by hand" in t for t in risk)
    assert not [s for s in r["work/risk"].actions if s.kind == "push"]

    assert any("2 commit(s) not on github" in t for t in _texts(r["cli-tool"]))     # never pushes your work
    assert any("detached HEAD" in t for t in _texts(r["display"]))
    assert any("unreachable" in t for t in _texts(r["firmware"]))
    assert any("diverged from github" in t for t in _texts(r["work/analysis"]))  # dirty + diverged local


def test_branch_filter_defaults_skip_feature_branches(world, cfg):
    r = _results(world, cfg)
    assert not any("feature/kpi" in t for t in _texts(r["work/dashboard"]))
    cfg.sync_branches = ["*"]
    r = _results(world, cfg)
    assert any("feature/kpi: create on gitea" in t for t in _texts(r["work/dashboard"]))


def test_branch_selected(cfg):
    assert branch_selected("main", cfg)
    assert not branch_selected("feature/x", cfg)
    cfg.sync_branches = ["*"]
    assert branch_selected("feature/x", cfg)
    assert not branch_selected("dependabot/npm/x", cfg)


def test_repo_filter(world, cfg):
    r = sync_all(str(world.code), cfg, names=["ticker", "work/sim"])
    assert sorted(x.name for x in r) == ["ticker", "work/sim"]
