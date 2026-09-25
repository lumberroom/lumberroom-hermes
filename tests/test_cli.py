import argparse
import json

import httpx2

from lumberroom_hermes.cli import lumberroom_command, register_cli, run_live_check
from lumberroom_hermes.config import LumberroomConfig


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


def test_status_exits_one_when_the_config_is_invalid(monkeypatch, capsys):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"memory": {"lumberroom": {"auth": "bearer"}}})
    assert lumberroom_command(parser().parse_args(["status"])) == 1
    assert "memory.lumberroom.auth" in capsys.readouterr().out


def test_status_works_with_an_empty_lumberroom_block_meaning_hosted_oauth(hermes_home, fake_engine, capsys):
    from lumberroom_hermes.config import parse, section

    cfg = parse(section({"memory": {}}))
    assert cfg.base_url == "https://mcp.lumberroom.cloud" and cfg.auth == "oauth"
    rc = run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)
    assert rc == 1
    out = capsys.readouterr().out
    assert "credential: missing" in out
    assert "reason:" in out


def test_status_in_token_mode_prints_credential_missing_instead_of_crashing(monkeypatch, hermes_home, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: None)
    cfg = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")

    rc = run_live_check(cfg, str(hermes_home))

    assert rc == 1
    out = capsys.readouterr().out
    assert "credential: missing" in out
    assert "reason: LUMBERROOM_HERMES_TOKEN is empty or unset" in out


def test_status_reports_the_oauth_credential_from_the_stored_pair_not_a_constant(hermes_home, fake_engine, capsys):
    from lumberroom_hermes.tokens import FileTokenStorage, token_paths

    cfg = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="oauth")
    path, _ = token_paths(str(hermes_home))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "mcp_url": cfg.mcp_url,
        "tokens": {"access_token": "at-1", "token_type": "Bearer", "refresh_token": "rt-1"},
        "expires_at": None, "client_info": None, "oauth_metadata": None,
    }))
    storage = FileTokenStorage(path, cfg.mcp_url)
    assert storage.read().tokens is not None

    rc = run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)

    out = capsys.readouterr().out
    assert "credential: present" in out
    assert rc in (0, 1)


def test_status_prints_the_reason_when_the_engine_is_unreachable(monkeypatch, hermes_home, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    cfg = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")

    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    def refusing(**kw):
        kw.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(refuse), **kw)

    rc = run_live_check(cfg, str(hermes_home), client_factory=refusing)

    assert rc == 1
    out = capsys.readouterr().out
    assert "reachable: False" in out
    assert "reason:" in out and "connection refused" in out


def test_status_reports_the_server_version_when_reachable(monkeypatch, hermes_home, fake_engine, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    cfg = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")

    rc = run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)

    assert rc == 0
    assert "server version: 3.2.0" in capsys.readouterr().out


def test_status_json_mode_carries_the_server_version_and_the_error(monkeypatch, hermes_home, fake_engine, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    cfg = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token")

    run_live_check(cfg, str(hermes_home), as_json=True, client_factory=fake_engine.client_factory)

    payload = json.loads(capsys.readouterr().out)
    assert payload["server_version"] == "3.2.0" and payload["error"] is None


def test_import_builtin_prints_a_message_and_exits_one_when_the_token_is_unset(monkeypatch, hermes_home, capsys):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"lumberroom": {"base_url": "http://fake.lumberroom.test", "auth": "token"}}})
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: None)

    rc = lumberroom_command(parser().parse_args(["import-builtin"]))

    assert rc == 1
    assert "LUMBERROOM_HERMES_TOKEN" in capsys.readouterr().out


def test_import_builtin_reports_a_signed_out_credential_instead_of_a_traceback(monkeypatch, hermes_home, capsys):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"lumberroom": {"base_url": "http://fake.lumberroom.test", "auth": "oauth"}}})
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("lumberroom_hermes.auth.build_auth", lambda cfg, **kw: object())

    class FakeBridge:
        def __init__(self, cfg, handle, *, session_id):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("lumberroom_hermes.bridge.Bridge", FakeBridge)

    from lumberroom_hermes.auth import LoginRequired

    def raise_signed_out(bridge, home, *, profile, dry_run):
        raise LoginRequired("lumberroom is signed out: run `hermes lumberroom login` to sign in")

    monkeypatch.setattr("lumberroom_hermes.importer.import_builtin", raise_signed_out)

    rc = lumberroom_command(parser().parse_args(["import-builtin"]))

    assert rc == 1
    assert "signed out" in capsys.readouterr().out


def test_import_builtin_reports_an_unreachable_engine_instead_of_a_traceback(monkeypatch, hermes_home, capsys):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"lumberroom": {"base_url": "http://fake.lumberroom.test", "auth": "token"}}})
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")

    class FakeBridge:
        def __init__(self, cfg, handle, *, session_id):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("lumberroom_hermes.bridge.Bridge", FakeBridge)

    def raise_unreachable(bridge, home, *, profile, dry_run):
        raise httpx2.ConnectError("connection refused")

    monkeypatch.setattr("lumberroom_hermes.importer.import_builtin", raise_unreachable)

    rc = lumberroom_command(parser().parse_args(["import-builtin"]))

    assert rc == 1
    assert "connection refused" in capsys.readouterr().out


def test_logout_reports_a_fence_timeout_instead_of_a_traceback(monkeypatch, hermes_home, capsys):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"lumberroom": {"base_url": "http://fake.lumberroom.test", "auth": "oauth"}}})
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)

    from lumberroom_hermes.tokens import FenceTimeout

    def raise_fence_timeout(cfg, *, hermes_home):
        raise FenceTimeout(f"another process held the lumberroom lock past the timeout")

    monkeypatch.setattr("lumberroom_hermes.auth.logout", raise_fence_timeout)

    rc = lumberroom_command(parser().parse_args(["logout"]))

    assert rc == 1
    assert "past the timeout" in capsys.readouterr().out


def test_status_prints_dreaming_review_off_when_the_setting_is_off(monkeypatch, hermes_home, fake_engine, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    cfg = LumberroomConfig(base_url="https://mcp.lumberroom.cloud", auth="token", dreaming_review=False)

    run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)

    assert "dreaming review: off (setting off)" in capsys.readouterr().out


def test_status_prints_dreaming_review_off_when_not_hosted(monkeypatch, hermes_home, fake_engine, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    cfg = LumberroomConfig(base_url="http://fake.lumberroom.test", auth="token", dreaming_review=True)

    run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)

    assert "dreaming review: off (not lumberroom.cloud)" in capsys.readouterr().out


def test_status_prints_dreaming_review_off_when_the_grant_lacks_the_tools(monkeypatch, hermes_home, fake_engine,
                                                                          capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    fake_engine.hide_tools = {"review_queue"}
    cfg = LumberroomConfig(base_url="https://mcp.lumberroom.cloud", auth="token", dreaming_review=True)

    run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)

    assert "dreaming review: off (the server's grant lacks the tools)" in capsys.readouterr().out


def test_status_prints_dreaming_review_on_when_everything_lines_up(monkeypatch, hermes_home, fake_engine, capsys):
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")
    cfg = LumberroomConfig(base_url="https://mcp.lumberroom.cloud", auth="token", dreaming_review=True)

    run_live_check(cfg, str(hermes_home), client_factory=fake_engine.client_factory)

    assert "dreaming review: on" in capsys.readouterr().out


def test_import_builtin_exits_two_and_names_the_grant_on_a_403(monkeypatch, hermes_home, capsys):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"memory": {"lumberroom": {"base_url": "http://fake.lumberroom.test", "auth": "token"}}})
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: "t-1")

    class RefusingBridge:
        def __init__(self, cfg, handle, *, session_id):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("lumberroom_hermes.bridge.Bridge", RefusingBridge)

    def refuse(bridge, home, *, profile, dry_run):
        from lumberroom_hermes.importer import MissingGrant
        raise MissingGrant()

    monkeypatch.setattr("lumberroom_hermes.importer.import_builtin", refuse)

    rc = lumberroom_command(parser().parse_args(["import-builtin"]))

    assert rc == 2
    assert "mayIngest" in capsys.readouterr().out


def test_dreaming_review_reports_unknown_when_the_engine_never_answered():
    from lumberroom_hermes.cli import _dreaming_review_status
    from lumberroom_hermes.config import parse

    cfg = parse({"dreaming_review": True})
    assert _dreaming_review_status(cfg, [], reachable=False) == (False, "unknown until the engine answers")
    assert _dreaming_review_status(cfg, [], reachable=True) == (False, "the server's grant lacks the tools")
