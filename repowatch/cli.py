"""Command line entry point.

    repowatch [ROOT]                 interactive matrix (default)
    repowatch status [ROOT] [--fetch] [--json] [--attention]
    repowatch sync [ROOT] [--apply] [--repo NAME ...]
                                     level Gitea and local with GitHub (fast-forward only)
    repowatch prune [ROOT] [--apply] [--include-unmerged DAYS]
                                     delete merged / squash-merged branches on GitHub
    repowatch schedule [--every MIN] [--remove]
                                     run `sync --apply` automatically (Windows Task Scheduler)
    repowatch init                   write an example config file
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__, core
from .config import config_dir, config_path, load_config, write_example_config

COMMANDS = {"tui", "status", "sync", "prune", "schedule", "init"}


def _root(arg: str | None, cfg) -> Path:
    raw = arg or cfg.root or "."
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        src = "command line" if arg else ("`root` in " + str(config_path()) if cfg.root else "current folder")
        print(f"repowatch: folder not found: {root}  (from {src})", file=sys.stderr)
        sys.exit(2)
    return root


def cmd_status(args, cfg) -> int:
    root = _root(args.root, cfg)
    statuses = core.scan(root, cfg, do_fetch=args.fetch)
    if args.attention:
        statuses = [s for s in statuses if s.flags()]

    if args.json:
        print(json.dumps({"root": str(root), "repos": [s.to_dict() for s in statuses]}, indent=2))
        return 0

    from rich.console import Console
    from rich.table import Table

    from .render import branch_cell, flags_cell, local_cell, remote_cell

    labels = core.all_labels(statuses, cfg.column_order)
    t = Table(title=f"{root}  ·  {len(statuses)} repos", title_justify="left", header_style="bold")
    for col in ["Repo", "Branch", "Local", *labels, "Status"]:
        t.add_column(col, no_wrap=col != "Status")
    for s in statuses:
        t.add_row(s.name, branch_cell(s), local_cell(s),
                  *[remote_cell(s.remote(l)) for l in labels], flags_cell(s))
    Console().print(t)
    if not args.fetch:
        Console(stderr=True).print("[dim]Remote columns reflect the last fetch. Add --fetch to refresh them.[/]")
    return 1 if any(s.flags() for s in statuses) and args.attention else 0


def cmd_sync(args, cfg) -> int:
    import datetime

    from rich.console import Console

    from .render import sync_text
    from .sync import sync_all

    root = _root(args.root, cfg)
    results = sync_all(str(root), cfg, apply=args.apply, names=args.repo)

    log_path = Path(args.log) if args.log else (config_dir() / "sync.log" if sys.stdout is None else None)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > 1_000_000:
            log_path.replace(log_path.with_suffix(".log.1"))
        fh = log_path.open("a", encoding="utf-8")
        con = Console(file=fh, width=120, color_system=None, highlight=False)
    else:
        con = Console(highlight=False)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "applied" if args.apply else "dry run — nothing changed, add --apply to do it"
    con.print(f"[bold]repowatch sync[/]  {root}  ·  {stamp}  ·  {mode}")
    shown = 0
    for r in results:
        if args.quiet and not r.steps and not r.skipped:
            continue
        con.print(sync_text(r, args.apply), end="")
        shown += 1
    n_act = sum(len(r.actions) for r in results)
    n_flag = sum(len(r.flags) for r in results)
    n_fail = sum(len(r.failed) for r in results)
    n_skip = sum(1 for r in results if r.skipped)
    con.print(f"[dim]{len(results)} repos · {n_act} action(s) · {n_fail} failed · "
              f"{n_flag} need you · {n_skip} skipped[/]")
    con.print()
    return 1 if (n_fail or n_flag) else 0


def cmd_prune(args, cfg) -> int:
    from rich.console import Console
    from rich.text import Text

    from .prune import prune_all

    root = _root(args.root, cfg)
    con = Console(highlight=False)
    mode = "applied" if args.apply else "dry run — nothing deleted, add --apply to do it"
    con.print(f"[bold]repowatch prune[/]  {root}  ·  {mode}")
    with con.status("Fetching and checking branches…"):
        results = prune_all(str(root), cfg, apply=args.apply, names=args.repo,
                            include_unmerged_days=args.include_unmerged, min_age_days=args.min_age)
    total_del = total_fail = 0
    for r in results:
        t = Text()
        t.append(r.name, style="bold")
        if r.skipped:
            t.append(f"  skipped: {r.skipped}", style="dim")
            con.print(t)
            continue
        dels = r.to_delete
        kept = [b for b in r.branches if not b.delete]
        t.append(f"  {r.remote} · default {r.default} · {len(dels)} to delete, {len(kept)} kept", style="dim")
        con.print(t)
        if r.note:
            con.print(f"  [yellow]! {r.note}[/]")
        if r.error:
            con.print(f"  [red]! {r.error}[/]")
        for b in r.branches:
            if not b.delete and not args.verbose and b.kind != "unmerged":
                continue
            line = Text("  ")
            if b.delete:
                if not args.apply:
                    line.append("→ delete ", style="cyan")
                elif b.deleted:
                    line.append("✓ deleted", style="green"); total_del += 1
                elif b.deleted is False:
                    line.append("✗ refused", style="red"); total_fail += 1
                else:
                    line.append("? unknown", style="yellow")
            else:
                line.append("· keep   ", style="dim")
            line.append(f" {b.name}")
            info = b.kind + (f" +{b.ahead}" if b.kind == "unmerged" else "") + f" · {b.age_days}d"
            if b.reason:
                info += f" · {b.reason}"
            line.append(f"   {info}", style="dim")
            con.print(line)
    n = sum(len(r.to_delete) for r in results)
    n_unm = sum(1 for r in results for b in r.branches if b.kind == "unmerged" and not b.delete)
    summary = f"{n} branch(es) {'deleted' if args.apply else 'to delete'} · {n_unm} unmerged kept"
    if args.apply:
        summary += f" · {total_fail} refused"
    con.print(f"[dim]{summary}[/]")
    return 1 if total_fail else 0


TASK_NAME = "repowatch sync"


def cmd_schedule(args, cfg) -> int:
    import subprocess

    if args.remove:
        if sys.platform != "win32":
            print("Remove the cron/launchd entry you added.")
            return 0
        cp = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True)
        print(cp.stdout or cp.stderr)
        return cp.returncode

    if not cfg.root:
        print(f"Set `root` in {config_path()} first (run `repowatch init`), so the task knows where to look.")
        return 2
    log = config_dir() / "sync.log"

    if sys.platform != "win32":
        exe = Path(sys.executable)
        line = f'*/{args.every} * * * * "{exe}" -m repowatch sync --apply --quiet --log "{log}"'
        print("Add this line with `crontab -e`:\n\n  " + line + "\n")
        return 0

    pyw = Path(sys.executable).with_name("pythonw.exe")
    runner = pyw if pyw.exists() else Path(sys.executable)
    tr = f'"{runner}" -m repowatch sync --apply --quiet --log "{log}"'
    if len(tr) > 261:
        print("Task command too long for Task Scheduler (261 chars):\n" + tr)
        return 2
    cmd = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "MINUTE", "/MO", str(args.every), "/TR", tr]
    cp = subprocess.run(cmd, capture_output=True, text=True)
    print(cp.stdout or cp.stderr)
    if cp.returncode == 0:
        print(f"Runs every {args.every} min while you're logged in. Log: {log}")
        print("Remove with: repowatch schedule --remove")
    return cp.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default to the TUI when no subcommand is given; skip past global options first.
    i = 0
    while i < len(argv) and argv[i] in {"--config"}:
        i += 2
    if i >= len(argv) or (argv[i] not in COMMANDS and argv[i] not in {"-h", "--help", "--version"}):
        argv.insert(i, "tui")

    p = argparse.ArgumentParser(
        prog="repowatch", description="Every git repo under a folder, compared across all its remotes."
    )
    p.add_argument("--version", action="version", version=f"repowatch {__version__}")
    p.add_argument("--config", type=Path, help=f"config file (default {config_path()})")
    sub = p.add_subparsers(dest="cmd")

    pt = sub.add_parser("tui", help="interactive matrix (default)")
    pt.add_argument("root", nargs="?")
    pt.add_argument("--fetch", action="store_true", help="fetch all remotes on start")
    pt.add_argument("--depth", type=int)

    ps = sub.add_parser("status", help="print the matrix once")
    ps.add_argument("root", nargs="?")
    ps.add_argument("--fetch", action="store_true", help="fetch all remotes first")
    ps.add_argument("--json", action="store_true", help="machine-readable output")
    ps.add_argument("--attention", action="store_true", help="only repos that need something (exit 1 if any)")
    ps.add_argument("--depth", type=int)

    py = sub.add_parser("sync", help="level Gitea and local with GitHub (fast-forward only)")
    py.add_argument("root", nargs="?")
    py.add_argument("--apply", action="store_true", help="actually push / fast-forward (default is a dry run)")
    py.add_argument("--repo", action="append", help="only this repo (repeatable)")
    py.add_argument("--quiet", action="store_true", help="hide repos with nothing to do")
    py.add_argument("--log", help="append output to this file instead of the screen")
    py.add_argument("--depth", type=int)

    pp = sub.add_parser("prune", help="delete merged / squash-merged branches on GitHub")
    pp.add_argument("root", nargs="?")
    pp.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    pp.add_argument("--repo", action="append", help="only this repo (repeatable)")
    pp.add_argument("--include-unmerged", type=int, metavar="DAYS",
                    help="also delete UNMERGED branches older than DAYS with no open PR (needs gh)")
    pp.add_argument("--min-age", type=int, metavar="DAYS", help="override prune_min_age_days")
    pp.add_argument("-v", "--verbose", action="store_true", help="also list kept merged branches")
    pp.add_argument("--depth", type=int)

    pc = sub.add_parser("schedule", help="run `sync --apply` automatically")
    pc.add_argument("--every", type=int, default=60, help="minutes between runs (default 60)")
    pc.add_argument("--remove", action="store_true", help="delete the scheduled task")

    sub.add_parser("init", help="write an example config file")

    args = p.parse_args(argv)
    if shutil.which("git") is None:
        print("repowatch: git not found on PATH", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    if getattr(args, "depth", None):
        cfg.depth = args.depth

    if args.cmd == "init":
        path = write_example_config(args.config)
        print(f"Config: {path}")
        return 0
    if args.cmd == "status":
        return cmd_status(args, cfg)
    if args.cmd == "sync":
        return cmd_sync(args, cfg)
    if args.cmd == "prune":
        return cmd_prune(args, cfg)
    if args.cmd == "schedule":
        return cmd_schedule(args, cfg)

    from .tui import run
    run(_root(args.root, cfg), cfg, fetch=args.fetch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
