"""Shared cell rendering for the CLI table and the TUI."""

from __future__ import annotations

from rich.text import Text

from .core import RemoteStatus, RepoStatus

STATE_STYLE = {
    "synced": "green",
    "ahead": "magenta",
    "behind": "yellow",
    "diverged": "bold red",
    "missing": "dim",
    "unknown": "dim",
    "unreachable": "red dim",
}

FLAG_STYLE = {
    "conflicts": "bold red",
    "dirty": "yellow",
    "untracked": "dim yellow",
    "stash": "cyan",
    "diverged": "bold red",
    "push": "magenta",
    "pull": "yellow",
    "remotes differ": "bold cyan",
    "mirror lag": "cyan",
    "unreachable": "red dim",
    "no remotes": "dim",
    "error": "bold red",
}


def local_cell(s: RepoStatus) -> Text:
    if s.error:
        return Text("error", style="bold red")
    t = Text((s.head or "(none)")[:7], style="bold")
    marks = []
    if s.staged:
        marks.append((f"+{s.staged}", "green"))
    if s.unstaged:
        marks.append((f"~{s.unstaged}", "yellow"))
    if s.untracked:
        marks.append((f"?{s.untracked}", "dim yellow"))
    if s.conflicts:
        marks.append((f"!{s.conflicts}", "bold red"))
    if s.stashes:
        marks.append((f"${s.stashes}", "cyan"))
    if marks:
        for m, style in marks:
            t.append(" ")
            t.append(m, style=style)
    else:
        t.append(" ✓", style="green")
    return t


def remote_cell(r: RemoteStatus | None) -> Text:
    if r is None:
        return Text("")
    style = STATE_STYLE[r.state]
    if r.mirror and r.state in ("ahead", "behind", "diverged", "missing"):
        style = "cyan"  # a lagging mirror is expected, not something to push to
    if r.state == "unreachable":
        sha = f"{r.sha[:7]} " if r.sha else ""
        return Text(f"{sha}✗", style=style)
    if r.state == "missing":
        return Text("—", style=style)
    t = Text(r.sha[:7] if r.sha else "", style=style)
    if r.state == "synced":
        t.append(" =", style=style)
    else:
        if r.ahead:
            t.append(f" ↑{r.ahead}", style=style)
        if r.behind:
            t.append(f" ↓{r.behind}", style=style)
    return t


def branch_cell(s: RepoStatus) -> Text:
    if s.detached:
        return Text("(detached)", style="italic yellow")
    return Text(s.branch or "?")


def flags_cell(s: RepoStatus) -> Text:
    t = Text()
    for i, f in enumerate(s.flags()):
        if i:
            t.append(" ")
        t.append(f, style=FLAG_STYLE.get(f, ""))
    return t if t.plain else Text("ok", style="green")


def sync_text(res, applied: bool) -> Text:
    """One block per repo for `repowatch sync` and the TUI confirm dialog."""
    t = Text()
    t.append(res.name, style="bold")
    if res.skipped:
        t.append(f"  skipped: {res.skipped}\n", style="dim")
        return t
    if not res.steps:
        t.append("  in step\n", style="green")
        return t
    t.append("\n")
    for st in res.steps:
        if st.kind == "flag":
            t.append("  ! ", style="yellow")
            t.append(st.text + "\n", style="yellow")
        else:
            if not applied:
                mark, style = "  → ", "cyan"
            elif st.ok:
                mark, style = "  ✓ ", "green"
            else:
                mark, style = "  ✗ ", "red"
            t.append(mark, style=style)
            t.append(st.text, style=style)
            if st.error:
                t.append(f"  ({st.error})", style="red dim")
            t.append("\n")
    return t
