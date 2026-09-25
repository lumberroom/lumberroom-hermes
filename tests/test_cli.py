import argparse

from lumberroom_hermes.cli import lumberroom_command, register_cli


def parser():
    p = argparse.ArgumentParser()
    register_cli(p)
    return p


def test_the_four_subcommands_parse():
    p = parser()
    assert p.parse_args(["login", "--no-browser"]).no_browser is True
    assert p.parse_args(["status", "--json"]).json is True
    assert p.parse_args(["import-builtin", "--dry-run"]).dry_run is True
    assert p.parse_args(["logout"]).lumberroom_command == "logout"


def test_no_subcommand_prints_usage_and_fails(capsys):
    assert lumberroom_command(parser().parse_args([])) == 1
    assert "hermes lumberroom" in capsys.readouterr().out


def test_status_exits_one_when_the_config_is_missing(monkeypatch, capsys):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"memory": {}})
    assert lumberroom_command(parser().parse_args(["status"])) == 1
    assert "base_url" in capsys.readouterr().out
