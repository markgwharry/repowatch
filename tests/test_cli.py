import json

import pytest

from repowatch import __version__
from repowatch.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_missing_root_is_a_clear_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["status", str(tmp_path / "nope")])
    assert e.value.code == 2
    assert "folder not found" in capsys.readouterr().err


def test_status_json(world, capsys, tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[hosts]\n"gitea.example" = "gitea"\n')
    assert main(["--config", str(cfg_file), "status", str(world.code), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    names = {r["name"] for r in data["repos"]}
    assert "core-lib" in names and len(names) == 10


def test_attention_exit_code(world):
    assert main(["status", str(world.code), "--attention"]) == 1


def test_config_before_subcommand(world, tmp_path, capsys):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("depth = 1\n")
    main(["--config", str(cfg_file), "status", str(world.code), "--json"])
    names = {r["name"] for r in json.loads(capsys.readouterr().out)["repos"]}
    assert "work/sim" not in names       # depth 1 from config took effect


def test_init_writes_parseable_config(tmp_path, capsys):
    import tomllib

    target = tmp_path / "cfg" / "config.toml"
    assert main(["--config", str(target), "init"]) == 0
    tomllib.loads(target.read_text())


def test_sync_dry_run_exit_code(world):
    assert main(["sync", str(world.code)]) == 1      # some repos need a human -> 1


def test_sync_log_file(world, tmp_path):
    log = tmp_path / "sync.log"
    main(["sync", str(world.code), "--quiet", "--log", str(log)])
    text = log.read_text(encoding="utf-8")
    assert "repowatch sync" in text and "repos" in text
