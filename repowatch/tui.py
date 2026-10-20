"""Textual front end: one row per repo, one column per remote label."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from . import core
from . import sync as syncmod
from .config import Config
from .render import branch_cell, flags_cell, local_cell, remote_cell, sync_text

LEGEND = (
    "[green]=[/] in sync   [magenta]↑n[/] local ahead (push)   [yellow]↓n[/] remote ahead (pull)   "
    "[bold red]↑↓[/] diverged   [cyan]cyan[/] mirror lagging   [dim]—[/] not on remote   [red dim]✗[/] fetch failed   "
    "local: [green]+[/]staged [yellow]~[/]modified [dim yellow]?[/]untracked [cyan]$[/]stash"
)


class AddRemoteScreen(ModalScreen[tuple[str, str] | None]):
    DEFAULT_CSS = """
    AddRemoteScreen { align: center middle; }
    #dialog { width: 80; height: auto; border: thick $accent; padding: 1 2; background: $surface; }
    #dialog Input { margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, repo_name: str, name: str, url: str) -> None:
        super().__init__()
        self.repo_name, self.default_name, self.default_url = repo_name, name, url

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Add remote to [b]{self.repo_name}[/b]")
            yield Input(value=self.default_name, placeholder="remote name, e.g. gitea", id="rname")
            yield Input(value=self.default_url, placeholder="URL", id="rurl")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add & fetch", variant="primary", id="ok")

    def on_mount(self) -> None:
        self.query_one("#rurl" if self.default_name else "#rname", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        name = self.query_one("#rname", Input).value.strip()
        url = self.query_one("#rurl", Input).value.strip()
        if name and url:
            self.dismiss((name, url))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SyncScreen(ModalScreen[bool]):
    """Shows the dry-run plan; Apply returns True."""
    DEFAULT_CSS = """
    SyncScreen { align: center middle; }
    #sdialog { width: 110; max-width: 95%; height: auto; max-height: 85%; border: thick $accent;
               padding: 1 2; background: $surface; }
    #splan { height: auto; max-height: 30; margin: 1 0; }
    #sbuttons { height: auto; align-horizontal: right; }
    #sbuttons Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("y", "apply", "Apply")]

    def __init__(self, plan: Text, can_apply: bool) -> None:
        super().__init__()
        self.plan, self.can_apply = plan, can_apply

    def compose(self) -> ComposeResult:
        with Vertical(id="sdialog"):
            yield Label("[b]Sync plan[/b] — fast-forward only, nothing is force-pushed")
            with VerticalScroll(id="splan"):
                yield Static(self.plan)
            with Horizontal(id="sbuttons"):
                yield Button("Close" if not self.can_apply else "Cancel", id="cancel")
                if self.can_apply:
                    yield Button("Apply (y)", variant="primary", id="apply")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "apply")

    def action_apply(self) -> None:
        if self.can_apply:
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RepoWatch(App):
    TITLE = "repowatch"
    CSS = """
    DataTable { height: 1fr; }
    #detail { height: auto; max-height: 14; border: round $primary-darken-2; padding: 0 1; }
    #legend { height: auto; color: $text-muted; padding: 0 1; }
    """
    BINDINGS = [
        Binding("r", "rescan", "Rescan"),
        Binding("f", "fetch_one", "Fetch"),
        Binding("F", "fetch_all", "Fetch all"),
        Binding("s", "sync_one", "Sync"),
        Binding("S", "sync_all", "Sync all"),
        Binding("a", "add_remote", "Add remote"),
        Binding("o", "open_folder", "Open folder"),
        Binding("d", "toggle_dirty", "Only attention"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, root: Path, cfg: Config, fetch_on_start: bool = False) -> None:
        super().__init__()
        self.root, self.cfg, self.fetch_on_start = root, cfg, fetch_on_start
        self.statuses: dict[str, core.RepoStatus] = {}
        self.order: list[str] = []
        self.fetch_errors: dict[str, dict[str, str]] = {}
        self.attention_only = False
        self.busy: set[str] = set()

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="detail")
        yield Static(LEGEND, id="legend")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self.root)
        if self.fetch_on_start:
            self.action_fetch_all()
        else:
            self.action_rescan()
        if self.cfg.refresh_seconds > 0:
            self.set_interval(self.cfg.refresh_seconds, self._auto_refresh)

    # ------------------------------------------------------------------- table

    def _visible(self) -> list[str]:
        if not self.attention_only:
            return self.order
        return [p for p in self.order if self.statuses[p].flags()]

    def _render_table(self) -> None:
        table = self.query_one(DataTable)
        cursor_key = self._selected_path()
        table.clear(columns=True)
        labels = core.all_labels(list(self.statuses.values()), self.cfg.column_order)
        table.add_column("Repo", key="repo")
        table.add_column("Branch", key="branch")
        table.add_column("Local", key="local")
        for lab in labels:
            table.add_column(lab, key=f"r:{lab}")
        table.add_column("Status", key="flags")

        rows = self._visible()
        for p in rows:
            s = self.statuses[p]
            name = Text(s.name)
            if p in self.busy:
                name.append(" ⟳", style="cyan")
            cells = [name, branch_cell(s), local_cell(s)]
            cells += [remote_cell(s.remote(lab)) for lab in labels]
            cells.append(flags_cell(s))
            table.add_row(*cells, key=p)

        if cursor_key in rows:
            table.move_cursor(row=rows.index(cursor_key))
        self._render_detail()
        n_att = sum(1 for p in self.order if self.statuses[p].flags())
        self.sub_title = f"{self.root}  ·  {len(self.order)} repos, {n_att} need attention" + (
            "  ·  filtered" if self.attention_only else "")

    def _selected_path(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None

    def _render_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        p = self._selected_path()
        if not p or p not in self.statuses:
            detail.update("No repos found." if not self.order else "")
            return
        s = self.statuses[p]
        t = Text()
        t.append(s.name, style="bold")
        t.append(f"   {s.path}\n", style="dim")
        if s.error:
            t.append(f"Error: {s.error}\n", style="red")
        br = "(detached)" if s.detached else (s.branch or "?")
        t.append(f"Branch {br}")
        t.append(f" → {s.upstream}" if s.upstream else "  (no upstream)", style="dim")
        t.append(f"   ·   last fetch {core.ago(s.last_fetch)}\n", style="dim")
        for r in s.remotes:
            t.append(f"  {r.label:<10}", style="bold")
            t.append(f"{r.name:<10}", style="dim")
            t.append_text(remote_cell(r))
            t.append(f"   {r.url}", style="dim")
            if r.state == "missing":
                t.append(f"   (no {s.branch or 'HEAD'} on this remote)", style="dim")
            if r.error:
                t.append(f"\n              {r.error}", style="red dim")
            t.append("\n")
        tips = {}
        for r in s.remotes:
            if r.sha:
                tips.setdefault(r.sha, []).append(r.label)
        if len(tips) > 1:
            t.append("Remotes disagree: ", style="bold cyan")
            t.append("  vs  ".join(f"{'/'.join(v)} @ {k[:7]}" for k, v in tips.items()))
        detail.update(t)

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._render_detail()

    # ----------------------------------------------------------------- workers

    @work(thread=True, exclusive=True, group="scan")
    def action_rescan(self) -> None:
        statuses = core.scan(self.root, self.cfg, fetch_errors=self.fetch_errors)
        self.call_from_thread(self._apply_scan, statuses)

    def _apply_scan(self, statuses: list[core.RepoStatus]) -> None:
        self.statuses = {s.path: s for s in statuses}
        self.order = [s.path for s in statuses]
        self._render_table()

    def _auto_refresh(self) -> None:
        if not self.busy:
            self.action_rescan()

    def _refresh_one(self, p: str) -> None:
        s = core.repo_status(Path(p), self.root, self.cfg, self.fetch_errors.get(p))
        self.call_from_thread(self._apply_one, s)

    def _apply_one(self, s: core.RepoStatus) -> None:
        self.busy.discard(s.path)
        if s.path not in self.statuses:
            self.order.append(s.path)
        self.statuses[s.path] = s
        self._render_table()

    def _mark_busy(self, paths: list[str]) -> None:
        self.busy.update(paths)
        self._render_table()

    @work(thread=True, group="fetch")
    def _fetch_paths(self, paths: list[str]) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def one(p: str) -> None:
            self.fetch_errors[p] = core.fetch_repo(p, self.cfg.fetch_timeout)
            self._refresh_one(p)

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(one, paths))
        self.call_from_thread(self.notify, f"Fetched {len(paths)} repo(s)", timeout=3)

    # ----------------------------------------------------------------- actions

    def action_fetch_one(self) -> None:
        p = self._selected_path()
        if p:
            self._mark_busy([p])
            self._fetch_paths([p])

    def action_fetch_all(self) -> None:
        if not self.order:
            # first run with --fetch: discover, then fetch everything
            repos = [str(p) for p in core.find_repos(self.root, self.cfg.depth)]
            self.order = repos
            self.statuses = {p: core.RepoStatus(path=p, name=Path(p).name) for p in repos}
        self._mark_busy(list(self.order))
        self._fetch_paths(list(self.order))

    # -------------------------------------------------------------------- sync

    def action_sync_one(self) -> None:
        p = self._selected_path()
        if p:
            self._mark_busy([p])
            self._sync_plan([p])

    def action_sync_all(self) -> None:
        if self.order:
            self._mark_busy(list(self.order))
            self._sync_plan(list(self.order))

    @work(thread=True, exclusive=True, group="sync")
    def _sync_plan(self, paths: list[str]) -> None:
        from concurrent.futures import ThreadPoolExecutor

        root = str(self.root)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda p: syncmod.plan_and_run(p, root, self.cfg, apply=False), paths))
        # fetch happened as part of planning: refresh rows now
        for p in paths:
            self._refresh_one(p)
        text = Text()
        for r in results:
            if len(results) > 1 and not r.steps and not r.skipped:
                continue
            text.append_text(sync_text(r, applied=False))
        if not text.plain:
            text = Text("Everything is in step.", style="green")
        can_apply = any(r.actions for r in results)
        to_apply = [r.path for r in results if r.actions]

        def done(ok: bool | None) -> None:
            if ok:
                self._mark_busy(to_apply)
                self._sync_apply(to_apply)

        self.call_from_thread(self.push_screen, SyncScreen(text, can_apply), done)

    @work(thread=True, group="sync")
    def _sync_apply(self, paths: list[str]) -> None:
        root = str(self.root)
        failed = 0
        for p in paths:
            r = syncmod.plan_and_run(p, root, self.cfg, apply=True, fetch=False)
            failed += len(r.failed)
            self._refresh_one(p)
        msg = f"Synced {len(paths)} repo(s)" + (f", {failed} step(s) failed — run sync again to see why" if failed else "")
        self.call_from_thread(self.notify, msg, severity="error" if failed else "information", timeout=5)

    def action_toggle_dirty(self) -> None:
        self.attention_only = not self.attention_only
        self._render_table()

    def action_open_folder(self) -> None:
        p = self._selected_path()
        if not p:
            return
        try:
            if sys.platform == "win32":
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as exc:
            self.notify(f"Couldn't open folder: {exc}", severity="error")

    def action_add_remote(self) -> None:
        p = self._selected_path()
        if not p:
            return
        s = self.statuses[p]
        existing = {r.label for r in s.remotes} | {r.name for r in s.remotes}
        name, url = "", ""
        owner_repo = next((core.url_owner_repo(r.url) for r in s.remotes if core.url_owner_repo(r.url)), None)
        for tname, tmpl in self.cfg.remote_templates.items():
            if tname not in existing:
                name = tname
                if owner_repo:
                    url = tmpl.format(owner=owner_repo[0], repo=owner_repo[1])
                break

        def done(result: tuple[str, str] | None) -> None:
            if not result:
                return
            rname, rurl = result
            err = core.add_remote(p, rname, rurl)
            if err:
                self.notify(err, severity="error")
                return
            self.notify(f"Added remote {rname}", timeout=3)
            self._mark_busy([p])
            self._fetch_paths([p])

        self.push_screen(AddRemoteScreen(s.name, name, url), done)


def run(root: Path, cfg: Config, fetch: bool = False) -> None:
    RepoWatch(root, cfg, fetch_on_start=fetch).run()
