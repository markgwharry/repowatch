"""Keep the secondary remote (Gitea) and the local checkout level with the primary (GitHub).

Rules — everything is fast-forward only; nothing is ever force-pushed or merged:

  per branch that exists on primary or secondary:
    same commit on both                  -> nothing to do
    only on primary                      -> create it on secondary
    secondary is behind primary          -> fast-forward secondary
    secondary is ahead of primary        -> fast-forward primary (work done on Gitea flows up)
    diverged                             -> flag, touch nothing
    only on secondary                    -> flag (new on Gitea, or deleted on GitHub? can't tell)

  local checkout (current branch only):
    clean and behind the new tip         -> fast-forward
    dirty                                -> flag, leave alone
    has commits the remotes don't        -> flag; pushing your own work is left to you
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from . import core
from .config import Config


@dataclass
class Step:
    kind: str          # "push", "ff-local", "flag"
    text: str
    cmd: list[str] | None = None
    ok: bool | None = None   # None = planned / not run
    error: str | None = None


@dataclass
class SyncResult:
    path: str
    name: str
    steps: list[Step] = field(default_factory=list)
    skipped: str | None = None

    @property
    def actions(self) -> list[Step]:
        return [s for s in self.steps if s.kind != "flag"]

    @property
    def flags(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "flag"]

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if s.ok is False]


def _is_ancestor(path: str, a: str, b: str) -> bool:
    return core.git(path, "merge-base", "--is-ancestor", a, b).returncode == 0


def _branches(refs: dict[str, str], remote: str) -> dict[str, str]:
    prefix = remote + "/"
    return {k[len(prefix):]: v for k, v in refs.items()
            if k.startswith(prefix) and k != f"{remote}/HEAD"}


def _err(cp) -> str:
    lines = [l.strip() for l in cp.stderr.splitlines() if l.strip()]
    key = [l for l in lines if l.startswith(("error:", "fatal:", "!", "remote: error"))]
    return (key or lines or ["failed"])[0]


def branch_selected(branch: str, cfg: Config) -> bool:
    return (any(fnmatchcase(branch, pat) for pat in cfg.sync_branches)
            and not any(fnmatchcase(branch, pat) for pat in cfg.sync_exclude))


def plan_and_run(path: str, root: str, cfg: Config, apply: bool = False, fetch: bool = True) -> SyncResult:
    path = str(path)
    status = core.repo_status(path, root, cfg)
    res = SyncResult(path=path, name=status.name)
    if status.error:
        res.skipped = status.error
        return res

    primary = next((r for r in status.remotes if r.label.split(":")[0] in cfg.primary_labels), None)
    secondaries = [r for r in status.remotes if r.mirror]
    if primary is None:
        res.skipped = "no primary remote (" + "/".join(cfg.primary_labels) + ")"
        return res

    fetch_errors = core.fetch_repo(path, cfg.fetch_timeout) if fetch else {}
    if primary.name in fetch_errors:
        res.skipped = f"{primary.label} unreachable: {fetch_errors[primary.name]}"
        return res

    cp = core.git(path, "for-each-ref", "--format=%(refname:strip=2)%09%(objectname)", "refs/remotes")
    refs = dict(l.split("\t", 1) for l in cp.stdout.splitlines() if "\t" in l)
    p_br = _branches(refs, primary.name)
    new_tip = dict(p_br)  # what primary will hold after this run (if pushes succeed)

    ssh = core._ssh_override(path)

    def push(remote: str, sha: str, branch: str, text: str) -> Step:
        st = Step("push", text, ["push", remote, f"{sha}:refs/heads/{branch}"])
        if apply:
            cp = core.git(path, *ssh, *st.cmd, timeout=cfg.fetch_timeout * 2)
            st.ok = cp.returncode == 0
            if not st.ok:
                st.error = _err(cp)
        res.steps.append(st)
        return st

    for sec in secondaries:
        if sec.name in fetch_errors:
            res.steps.append(Step("flag", f"{sec.label} unreachable — skipped ({fetch_errors[sec.name]})"))
            continue
        s_br = _branches(refs, sec.name)
        for b in sorted(set(p_br) | set(s_br)):
            if not branch_selected(b, cfg):
                continue
            p, s = p_br.get(b), s_br.get(b)
            if p == s:
                continue
            if p and not s:
                push(sec.name, p, b, f"{b}: create on {sec.label} from {primary.label} @ {p[:7]}")
            elif p and s and _is_ancestor(path, s, p):
                n = core.git(path, "rev-list", "--count", f"{s}..{p}").stdout.strip()
                push(sec.name, p, b, f"{b}: {sec.label} +{n} from {primary.label} ({s[:7]}→{p[:7]})")
            elif p and s and _is_ancestor(path, p, s):
                n = core.git(path, "rev-list", "--count", f"{p}..{s}").stdout.strip()
                st = push(primary.name, s, b, f"{b}: {primary.label} +{n} from {sec.label} ({p[:7]}→{s[:7]})")
                if st.ok is not False:
                    new_tip[b] = s
            elif p and s:
                res.steps.append(Step("flag", f"{b}: {primary.label} and {sec.label} have diverged — resolve by hand"))
            else:
                res.steps.append(Step("flag", f"{b}: only on {sec.label} — new there, or deleted on {primary.label}?"))

    # Local checkout: fast-forward the current branch to the (new) primary tip.
    if status.detached or not status.branch or not status.head:
        if status.detached:
            res.steps.append(Step("flag", "local: detached HEAD — left alone"))
        return res
    b = status.branch
    target = new_tip.get(b)
    if not target or target == status.head:
        pass
    elif _is_ancestor(path, status.head, target):
        if status.dirty or status.conflicts:
            res.steps.append(Step("flag", f"local {b}: behind {primary.label} but has uncommitted changes — not updated"))
        else:
            n = core.git(path, "rev-list", "--count", f"HEAD..{target}").stdout.strip()
            st = Step("ff-local", f"local {b}: fast-forward +{n} ({status.head[:7]}→{target[:7]})",
                      ["merge", "--ff-only", "--quiet", target])
            if apply:
                cp = core.git(path, *st.cmd, timeout=60)
                st.ok = cp.returncode == 0
                if not st.ok:
                    st.error = _err(cp)
            res.steps.append(st)
    elif _is_ancestor(path, target, status.head):
        n = core.git(path, "rev-list", "--count", f"{target}..HEAD").stdout.strip()
        res.steps.append(Step("flag", f"local {b}: {n} commit(s) not on {primary.label} — push when ready"))
    else:
        res.steps.append(Step("flag", f"local {b}: diverged from {primary.label} — pull/rebase by hand"))
    return res


def sync_all(root: str, cfg: Config, apply: bool = False, names: list[str] | None = None,
             workers: int = 6) -> list[SyncResult]:
    from concurrent.futures import ThreadPoolExecutor

    root = str(Path(root).expanduser().resolve())
    repos = [str(p) for p in core.find_repos(root, cfg.depth)]
    if names:
        wanted = {n.lower() for n in names}
        repos = [p for p in repos if Path(p).name.lower() in wanted
                 or Path(p).relative_to(root).as_posix().lower() in wanted]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: plan_and_run(p, root, cfg, apply=apply), repos))
