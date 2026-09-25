from lumberroom_hermes.importer import ImportReport
from lumberroom_hermes.wizard import apply_builtin_off, post_setup


def run_setup(monkeypatch, answers, secret="t-1"):
    saved, env = {}, {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: env.update({k: v}))
    it = iter(answers)
    config = {"memory": {}}
    post_setup("/tmp/h", config, ask=lambda _: next(it), ask_secret=lambda _: secret, out=lambda _: None)
    return saved, env


def test_pressing_enter_on_the_deployment_picker_chooses_lumberroom_cloud(monkeypatch):
    saved, env = run_setup(monkeypatch, ["", "", "n", "n", "n"])
    assert saved["memory"]["lumberroom"] == {"base_url": "https://mcp.lumberroom.cloud", "auth": "oauth"}
    assert env == {}


def test_hosted_with_a_pasted_token_writes_token_auth(monkeypatch):
    saved, env = run_setup(monkeypatch, ["1", "2", "n", "n"])
    assert saved["memory"]["lumberroom"] == {"base_url": "https://mcp.lumberroom.cloud", "auth": "token"}
    assert env == {"LUMBERROOM_HERMES_TOKEN": "t-1"}


def test_the_dreaming_review_question_defaults_to_no_and_writes_nothing_on_enter(monkeypatch):
    saved, _ = run_setup(monkeypatch, ["1", "2", "", "n"])
    assert "dreaming_review" not in saved["memory"]["lumberroom"]


def test_the_dreaming_review_question_writes_true_on_yes(monkeypatch):
    saved, _ = run_setup(monkeypatch, ["1", "2", "y", "n"])
    assert saved["memory"]["lumberroom"]["dreaming_review"] is True


def test_self_hosted_setup_never_asks_or_writes_dreaming_review(monkeypatch):
    saved, env = {}, {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: env.update({k: v}))
    answers = iter(["2", "http://127.0.0.1:8787", "token", "n"])
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return next(answers)

    post_setup("/tmp/h", {"memory": {}}, ask=ask, ask_secret=lambda _: "t-1", out=lambda _: None)

    assert not any("dreaming" in p.lower() for p in prompts)
    assert "dreaming_review" not in saved["memory"]["lumberroom"]


def test_self_hosted_token_setup_writes_the_block_the_secret_and_the_builtin_flags(monkeypatch):
    saved, env = run_setup(monkeypatch, ["2", "http://127.0.0.1:8787", "token", "n"])
    m = saved["memory"]
    assert m["provider"] == "lumberroom" and m["memory_enabled"] is False and m["user_profile_enabled"] is False
    assert m["lumberroom"] == {"base_url": "http://127.0.0.1:8787", "auth": "token"}
    assert env == {"LUMBERROOM_HERMES_TOKEN": "t-1"}


def test_an_invalid_self_hosted_auth_mode_is_rejected_and_asked_again(monkeypatch):
    saved, _ = run_setup(
        monkeypatch,
        ["2", "http://127.0.0.1:8787", "bearer", "2", "http://127.0.0.1:8787", "token", "n"])
    assert saved["memory"]["lumberroom"] == {"base_url": "http://127.0.0.1:8787", "auth": "token"}


def test_an_invalid_self_hosted_auth_mode_writes_nothing_and_prompts_no_secret(monkeypatch):
    saved, env = {}, {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: env.update({k: v}))
    answers = iter(["2", "http://127.0.0.1:8787", "bearer"])

    def ask(_prompt):
        return next(answers)

    def ask_secret(_prompt):
        raise AssertionError("no secret prompt before the answers parse")

    try:
        post_setup("/tmp/h", {"memory": {}}, ask=ask, ask_secret=ask_secret, out=lambda _: None)
    except StopIteration:
        pass
    assert saved == {} and env == {}


def test_apply_builtin_off_reports_each_change():
    config = {"memory": {"memory_enabled": True}}
    lines = apply_builtin_off(config)
    assert "memory.memory_enabled: true -> false" in lines
    assert config["memory"]["provider"] == "lumberroom" and config["memory"]["nudge_interval"] == 0


def test_the_import_step_uses_the_profile_name_for_this_hermes_home(monkeypatch, hermes_home):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: None)
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: None)
    monkeypatch.setattr("hermes_constants.profile_name_for_home", lambda home: "work")
    monkeypatch.setattr("lumberroom_hermes.auth.build_auth", lambda cfg, **kw: object())

    class FakeBridge:
        def __init__(self, cfg, handle, *, session_id):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("lumberroom_hermes.bridge.Bridge", FakeBridge)

    seen_profiles = []

    def fake_import(bridge, home, *, profile, dry_run):
        seen_profiles.append(profile)
        return ImportReport(run_id="r-1", posted=1, proposals_new=1, proposals_reinforced=0, refused=0, blocked=0)

    monkeypatch.setattr("lumberroom_hermes.importer.import_builtin", fake_import)

    answers = iter(["1", "2", "n", "n", "y"])
    post_setup(str(hermes_home), {"memory": {}}, ask=lambda _: next(answers), ask_secret=lambda _: "t",
              out=lambda _: None)

    assert seen_profiles == ["work"]


def test_a_missing_credential_during_the_import_step_is_reported_not_raised(monkeypatch, hermes_home):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: None)
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: None)
    monkeypatch.setattr("agent.secret_scope.get_secret", lambda name, default=None: None)

    lines = []
    answers = iter(["2", "http://127.0.0.1:8787", "token", "n", "y"])
    post_setup(str(hermes_home), {"memory": {}}, ask=lambda _: next(answers), ask_secret=lambda _: "t",
              out=lines.append)

    assert any("LUMBERROOM_HERMES_TOKEN" in line for line in lines)


def test_a_signed_out_credential_during_the_import_step_is_reported_not_raised(monkeypatch, hermes_home):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: None)
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: None)
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

    lines = []
    answers = iter(["1", "2", "n", "n", "y"])
    post_setup(str(hermes_home), {"memory": {}}, ask=lambda _: next(answers), ask_secret=lambda _: "t",
              out=lines.append)

    assert any("signed out" in line for line in lines)


def test_an_unreachable_engine_during_the_import_step_is_reported_not_raised(monkeypatch, hermes_home):
    (hermes_home / "memories" / "MEMORY.md").write_text("Fact one.\n")
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: None)
    monkeypatch.setattr("hermes_cli.config.save_env_value", lambda k, v: None)
    monkeypatch.setattr("lumberroom_hermes.auth.build_auth", lambda cfg, **kw: object())

    class FakeBridge:
        def __init__(self, cfg, handle, *, session_id):
            pass

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("lumberroom_hermes.bridge.Bridge", FakeBridge)

    import httpx2

    def raise_unreachable(bridge, home, *, profile, dry_run):
        raise httpx2.ConnectError("connection refused")

    monkeypatch.setattr("lumberroom_hermes.importer.import_builtin", raise_unreachable)

    lines = []
    answers = iter(["2", "http://127.0.0.1:8787", "token", "n", "y"])
    post_setup(str(hermes_home), {"memory": {}}, ask=lambda _: next(answers), ask_secret=lambda _: "t",
              out=lines.append)

    assert any("connection refused" in line for line in lines)
