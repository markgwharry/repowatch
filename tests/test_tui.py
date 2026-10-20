"""Headless TUI test: render, fetch, filter, add remote, sync."""

import asyncio

from textual.widgets import DataTable

from repowatch.tui import AddRemoteScreen, RepoWatch, SyncScreen


def test_tui_end_to_end(world, cfg):
    cfg.remote_templates["backup"] = "https://gitea.example/{owner}/{repo}-backup.git"
    app = RepoWatch(world.code, cfg)

    async def run():
        async with app.run_test(size=(150, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one(DataTable)
            assert table.row_count == 10
            labels = [str(c.label) for c in table.columns.values()]
            assert labels[3:5] == ["github", "gitea"]

            await pilot.press("F")
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.busy
            firmware = next(s for s in app.statuses.values() if s.name == "firmware")
            assert "unreachable" in firmware.flags()

            await pilot.press("d")
            await pilot.pause()
            expect = sum(1 for p in app.order if app.statuses[p].flags())
            assert table.row_count == expect < 10
            await pilot.press("d")
            await pilot.pause()

            table.move_cursor(row=0)
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AddRemoteScreen)
            await pilot.press("enter")
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert any(r.name == "backup" for r in app.statuses[app.order[0]].remotes)

            rows = app._visible()
            table.move_cursor(row=next(i for i, p in enumerate(rows)
                                       if app.statuses[p].name.endswith("tracker")))
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, SyncScreen)
            assert "fast-forward" in app.screen.plan.plain
            await pilot.press("y")
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause()
            dt = next(s for s in app.statuses.values() if s.name.endswith("tracker"))
            assert dt.flags() == []

    asyncio.run(run())
